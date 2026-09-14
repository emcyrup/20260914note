"""
複数の職員が同じ画面を同時に編集したときの競合対策。

1. 楽観ロック：編集フォームに開いた時点の版（updated_at）を隠し項目 version として持たせ、
   保存時に DB の版と違えば「他の職員が先に保存した」として保存を止める（force_save=1 なら上書き）。
2. 編集中の表示：フォームを開いている間、EditingSession に記録し、同じ画面を開いた他の職員に知らせる。
"""
import datetime

from django.apps import apps
from django.utils import timezone

VERSION_FIELD = 'version'
FORCE_FIELD = 'force_save'
EDITING_TTL = datetime.timedelta(seconds=120)   # この時間 touch が無ければ「編集中」とみなさない

# 画面上の種類 → (モデル, 事業所への絞り込み)
KINDS = {
    'daily_record':     ('records.DailyRecord', 'facility'),
    'memo':             ('records.StaffMemo', 'facility'),
    'template':         ('records.RecordTemplate', 'facility'),
    'beneficiary':      ('beneficiaries.Beneficiary', 'facility'),
    'guardian':         ('beneficiaries.Guardian', 'beneficiary__facility'),
    'certificate':      ('beneficiaries.RecipientCertificate', 'beneficiary__facility'),
    'facility':         ('facilities.Facility', 'pk'),
    'support_plan':     ('support_plans.SupportPlan', 'facility'),
    'assessment':       ('support_plans.Assessment', 'plan__facility'),
    'plan_draft':       ('support_plans.PlanDraft', 'plan__facility'),
    'staff_meeting':    ('support_plans.StaffMeeting', 'plan__facility'),
    'consent':          ('support_plans.ConsentDelivery', 'plan__facility'),
    'monitoring':       ('support_plans.Monitoring', 'plan__facility'),
    'goal':             ('support_plans.PlanGoal', 'plan__facility'),
    'meeting_report':   ('custom_forms.AgencyMeetingReport', 'facility'),
    'specialized_plan': ('custom_forms.SpecializedSupportPlan', 'facility'),
    'copayment':        ('billing.CopaymentManagement', 'facility'),
}
STEP_KINDS = {1: 'assessment', 2: 'plan_draft', 3: 'staff_meeting', 4: 'consent', 5: 'monitoring'}


def version_token(obj):
    """フォームに埋める版。updated_at のマイクロ秒までの文字列。新規や版を持たないものは空"""
    ts = getattr(obj, 'updated_at', None)
    if obj is None or getattr(obj, 'pk', None) is None or ts is None:
        return ''
    return ts.isoformat()


def saved_at_label(obj):
    ts = getattr(obj, 'updated_at', None)
    return timezone.localtime(ts).strftime('%H:%M') if ts else ''


def check_conflict(request, obj):
    """
    フォームが送ってきた版と DB の版を比べる。
    競合していればメッセージ（文字列）を返し、問題なければ None。
    フォームが version を送っていない（古い画面・API）場合と、force_save=1 の場合は確認しない。
    """
    sent = request.POST.get(VERSION_FIELD)
    if sent is None or request.POST.get(FORCE_FIELD) == '1' or obj is None or obj.pk is None:
        return None
    if sent == version_token(obj):
        return None
    when = saved_at_label(obj)
    return (f'この画面を開いたあとに、他の職員が{f" {when} に" if when else ""}保存しています。'
            '保存を止めました。画面を開き直して最新の内容を確認してから、もう一度編集してください。')


def resolve(kind, pk, facility):
    """種類と ID から、その事業所のオブジェクトを返す（無ければ None）"""
    if kind not in KINDS or facility is None:
        return None
    label, lookup = KINDS[kind]
    try:
        pk = int(pk)
    except (TypeError, ValueError):
        return None
    return apps.get_model(label).objects.filter(pk=pk, **{lookup: facility.pk}).first()


# ---- 編集中の表示 -------------------------------------------------------
def touch(facility, kind, pk, user):
    from facilities.models import EditingSession
    EditingSession.objects.update_or_create(kind=kind, target_id=pk, user=user, defaults={'facility': facility})
    # 古い行は掃除する
    EditingSession.objects.filter(touched_at__lt=timezone.now() - datetime.timedelta(minutes=15)).delete()


def release(kind, pk, user):
    from facilities.models import EditingSession
    EditingSession.objects.filter(kind=kind, target_id=pk, user=user).delete()


def others_editing(facility, kind, pk, user):
    """同じものを開いている他の職員（表示名・開始時刻）"""
    from facilities.models import EditingSession
    qs = (EditingSession.objects.filter(facility=facility, kind=kind, target_id=pk, touched_at__gte=timezone.now() - EDITING_TTL)
          .exclude(user=user).select_related('user').order_by('started_at'))
    return [{'name': s.user.display_name or s.user.username, 'since': timezone.localtime(s.started_at).strftime('%H:%M')} for s in qs]

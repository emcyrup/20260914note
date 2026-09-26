"""
期限のお知らせ（受給者証の有効期限・個別支援計画の計画期間・モニタリングの期日）。

- ホームの「お知らせ・アラート」に出す（collect）
- 毎朝の cron から `python manage.py send_reminders` で、管理者・児発管のメールと代表者メールへ送る（send_for）
  メールは毎日は送らず、期限の 30・14・7・3・1・0 日前と、期限を過ぎているものがあるときの月曜だけ送る（should_send）
"""
import datetime

from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse

DEFAULT_DAYS = 30
MILESTONES = (30, 14, 7, 3, 1, 0)

KIND_CERT = 'cert'
KIND_PLAN = 'plan'
KIND_MONITORING = 'monitoring'
KIND_LABELS = {KIND_CERT: '受給者証の有効期限', KIND_PLAN: '個別支援計画の計画期間（終了）', KIND_MONITORING: 'モニタリングの期日'}


class Item:
    __slots__ = ('kind', 'beneficiary', 'due', 'days_left', 'url', 'detail')

    def __init__(self, kind, beneficiary, due, today, url, detail=''):
        self.kind, self.beneficiary, self.due, self.url, self.detail = kind, beneficiary, due, url, detail
        self.days_left = (due - today).days

    @property
    def overdue(self):
        return self.days_left < 0

    @property
    def label(self):
        return KIND_LABELS[self.kind]

    @property
    def when(self):
        if self.days_left < 0:
            return f'{-self.days_left} 日過ぎています'
        if self.days_left == 0:
            return 'きょうが期限です'
        return f'あと {self.days_left} 日'

    def __repr__(self):
        return f'<Item {self.kind} {self.beneficiary} {self.due} {self.days_left}>'

    def __eq__(self, other):
        return isinstance(other, Item) and (self.kind, self.beneficiary.pk, self.due) == (other.kind, other.beneficiary.pk, other.due)

    def __hash__(self):
        return hash((self.kind, self.beneficiary.pk, self.due))


def collect(facility, today=None, days=DEFAULT_DAYS, kinds=(KIND_CERT, KIND_PLAN, KIND_MONITORING)):
    """期限が today+days までのもの（過ぎたものも含む）を、期限の近い順に返す"""
    from beneficiaries.models import Beneficiary
    from support_plans.models import SupportPlan

    today = today or datetime.date.today()
    limit = today + datetime.timedelta(days=days)
    items = []
    if KIND_CERT in kinds:
        for b in (Beneficiary.objects.filter(facility=facility, status=Beneficiary.STATUS_ACTIVE)
                  .prefetch_related('recipient_certificates')):
            cert = b.latest_certificate
            if cert and cert.valid_until <= limit:
                items.append(Item(KIND_CERT, b, cert.valid_until, today, reverse('beneficiaries:detail', args=[b.pk])))
    if KIND_PLAN in kinds or KIND_MONITORING in kinds:
        plans = (SupportPlan.objects.filter(facility=facility, beneficiary__status=Beneficiary.STATUS_ACTIVE)
                 .exclude(status=SupportPlan.STATUS_CLOSED).select_related('beneficiary', 'draft', 'consent'))
        for plan in plans:
            url = reverse('support_plans:detail', args=[plan.pk])
            draft = getattr(plan, 'draft', None)
            if KIND_PLAN in kinds and draft is not None and draft.period_end and draft.period_end <= limit:
                items.append(Item(KIND_PLAN, plan.beneficiary, draft.period_end, today, url, plan.title))
            if KIND_MONITORING in kinds and plan.status == SupportPlan.STATUS_ACTIVE:
                due = plan.next_monitoring_due
                if due and due <= limit:
                    items.append(Item(KIND_MONITORING, plan.beneficiary, due, today,
                                      reverse('support_plans:step', args=[plan.pk, SupportPlan.STEP_MONITORING]), plan.title))
    items.sort(key=lambda i: (i.due, i.kind, i.beneficiary.pk))
    return items


def should_send(items, today):
    """期限の節目（30・14・7・3・1・0 日前）のものがあるか、期限切れがあって月曜なら送る"""
    if any(i.days_left in MILESTONES for i in items):
        return True
    return today.weekday() == 0 and any(i.overdue for i in items)


def recipients(facility):
    """送り先：管理者・児発管のメールと、施設の代表者メール（重複なし）"""
    from accounts.models import StaffAccount
    out = []
    for email in StaffAccount.objects.filter(
            facility=facility, is_active=True,
            role__in=(StaffAccount.ROLE_ADMIN, StaffAccount.ROLE_CHILD_DEV_MANAGER)).exclude(email='') \
            .order_by('pk').values_list('email', flat=True):
        if email not in out:
            out.append(email)
    if facility.representative_email and facility.representative_email not in out:
        out.append(facility.representative_email)
    return out


def build_mail(facility, items, today, base_url=''):
    base_url = (base_url or settings.RESERVATION_SITE_URL or '').rstrip('/')
    overdue = [i for i in items if i.overdue]
    subject = f'【{facility.name}】期限のお知らせ（{len(items)} 件' + (f'・期限切れ {len(overdue)} 件' if overdue else '') + '）'
    lines = [f'{facility.name} のみなさま', '',
             f'{today:%Y年%m月%d日} 時点で、期限が近い・過ぎているものをお知らせします。', '']
    for kind in (KIND_CERT, KIND_PLAN, KIND_MONITORING):
        group = [i for i in items if i.kind == kind]
        if not group:
            continue
        lines.append(f'■ {KIND_LABELS[kind]}')
        for i in group:
            mark = '【期限切れ】' if i.overdue else ''
            extra = f'（{i.detail}）' if i.detail else ''
            lines.append(f'・{i.beneficiary.full_name} さん{extra}：{i.due:%Y/%m/%d}　{mark}{i.when}')
            if base_url:
                lines.append(f'　{base_url}{i.url}')
        lines.append('')
    lines += ['ホームの「お知らせ・アラート」でも確認できます。', 'このメールは業務支援システムが自動で送っています。']
    return subject, '\n'.join(lines)


def send_for(facility, today=None, days=DEFAULT_DAYS, force=False, dry_run=False, base_url=''):
    """1つの事業所ぶんを送る。戻り値は (送ったか, 件数, 宛先, 理由)"""
    today = today or datetime.date.today()
    items = collect(facility, today, days)
    if not items:
        return False, 0, [], '期限が近いものはありません'
    if not force and not should_send(items, today):
        return False, len(items), [], '節目の日ではないので送りません（--force で送れます）'
    to = recipients(facility)
    if not to:
        return False, len(items), [], '宛先がありません（管理者・児発管のメール、または施設設定の代表者メールを入れてください）'
    subject, body = build_mail(facility, items, today, base_url)
    if not dry_run:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, to, fail_silently=False)
    return True, len(items), to, subject

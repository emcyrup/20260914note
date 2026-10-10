import datetime
import json
import base64
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import ListView, DetailView, CreateView, UpdateView
from django.views import View
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.http import Http404, JsonResponse
from django.contrib import messages
from django.db import models as db_models
from django.conf import settings
from django.utils import timezone
import anthropic

from .models import TRASH_DAYS, DeletedBeneficiary, Beneficiary, BeneficiaryAssessment, BeneficiaryOffice, Guardian, RecipientCertificate, BeneficiaryDocument, DOCUMENT_EXTENSIONS, DOCUMENT_MAX_BYTES, KANA_ROWS, DevelopmentAssessment, RecordDigest
from .forms import BeneficiaryForm, BeneficiaryOfficeForm, GuardianForm, RecipientCertificateForm
from facilities.context_processors import get_terms
from config.concurrency import check_conflict
from config.utils import reservation_enabled, to_int
from .importer import PLACEHOLDER_DOB
from config.pdf import pdf_or_html


# =============================================
# 利用者一覧
# =============================================
class BeneficiaryListView(LoginRequiredMixin, ListView):
    """
    利用者一覧。ログインユーザーの施設に紐づく利用者のみ表示する。
    """
    model = Beneficiary
    template_name = 'beneficiaries/list.html'
    context_object_name = 'beneficiaries'

    def get_queryset(self):
        qs = Beneficiary.objects.filter(facility=self.request.user.facility).prefetch_related('guardians')
        # 氏名・かなで検索
        q = self.request.GET.get('q', '')
        if q:
            qs = qs.filter(
                db_models.Q(last_name__icontains=q) |
                db_models.Q(first_name__icontains=q) |
                db_models.Q(last_name_kana__icontains=q) |
                db_models.Q(first_name_kana__icontains=q)
            )
        # 在籍状況フィルタ（デフォルトは在籍中のみ）
        status = self.request.GET.get('status', Beneficiary.STATUS_ACTIVE)
        if status:
            qs = qs.filter(status=status)
        # 50 音の行で絞る（row=か など。row=他 はふりがなの無い人）
        row = self.request.GET.get('row', '')
        chars = dict(KANA_ROWS).get(row)
        if chars:
            cond = db_models.Q()
            for ch in chars:
                cond |= db_models.Q(last_name_kana__startswith=ch)
            qs = qs.filter(cond)
        elif row == '他':
            qs = qs.filter(last_name_kana='')
        # 50 音順。ふりがなの無い人は最後（かなが空だと先頭に来てしまうので）
        return qs.annotate(no_kana=db_models.Case(db_models.When(last_name_kana='', then=1), default=0,
                                                  output_field=db_models.IntegerField())) \
                 .order_by('no_kana', 'last_name_kana', 'first_name_kana', 'last_name', 'first_name', 'pk')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['q'] = self.request.GET.get('q', '')
        ctx['status'] = self.request.GET.get('status', Beneficiary.STATUS_ACTIVE)
        ctx['row'] = self.request.GET.get('row', '')
        ctx['kana_rows'] = [r for r, _ in KANA_ROWS]
        counts = dict(Beneficiary.objects.filter(facility=self.request.user.facility).values_list('status')
                      .annotate(n=db_models.Count('pk')).values_list('status', 'n'))
        ctx['status_tabs'] = [(value, label, counts.get(value, 0)) for value, label in Beneficiary.STATUS_CHOICES] \
            + [('', '全員', sum(counts.values()))]
        ctx['can_delete'] = self.request.user.is_admin or self.request.user.is_superuser
        if ctx['can_delete']:      # 同じ名前の組の数（重複のまとめへ）
            from .merge import find_groups
            ctx['duplicate_groups'] = len(find_groups(self.request.user.facility))
            ctx['trash_count'] = DeletedBeneficiary.objects.filter(facility=self.request.user.facility).count()
        ctx['status_choices'] = Beneficiary.STATUS_CHOICES
        return ctx


# =============================================
# 利用者の削除（退所した人だけ。管理者だけ）
# =============================================
RETENTION_NOTE = '障害児通所支援の記録（支援計画・サービス提供の記録など）は、サービスを終えた日から 5 年間の保存が求められています。'


def related_counts(b):
    """削除のときに一緒に消えるものの件数（画面で確かめてもらう）"""
    from records.models import DailyRecord
    rows = [
        ('療育記録', b.therapy_records.count()),
        ('予約', b.reservations.count()),
        ('月の利用希望', b.monthly_requests.count()),
        ('アセスメント・資料', b.assessments.count()),
        ('書類・画像', b.documents.count()),
        ('書類から分かっていること', b.knowledge.count()),
        ('個別支援計画', b.support_plans.count()),
        ('日誌（記録）', DailyRecord.objects.filter(beneficiary=b).count()),
        ('来所予定', b.scheduled_visits.count()),
        ('受給者証', b.recipient_certificates.count()),
        ('保護者', b.guardians.count()),
    ]
    return [(label, n) for label, n in rows if n]


def delete_beneficiary(b, by=None):
    """利用者を関連する記録・書類ごと消す。ごみ箱に TRASH_DAYS 日置き、そのあいだは戻せる（beneficiaries/trash.py）"""
    from .trash import trash
    return trash(b, by=by)


def set_status(b, status):
    """在籍状況を変える。退所・卒業にすると退所日が空なら今日、在籍中に戻すと退所日を消す"""
    b.status = status
    fields = ['status', 'updated_at']
    if status in Beneficiary.LEFT_STATUSES and not b.discharge_date:
        b.discharge_date = datetime.date.today()
        fields.append('discharge_date')
    elif status == Beneficiary.STATUS_ACTIVE and b.discharge_date:
        b.discharge_date = None
        fields.append('discharge_date')
    b.save(update_fields=fields)


def _back_to(request, default):
    """next（同じサイトの中だけ）があればそこへ、なければ default へ戻る"""
    from django.utils.http import url_has_allowed_host_and_scheme
    nxt = request.POST.get('next') or request.GET.get('next') or ''
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return redirect(nxt)
    return redirect(default)


class BeneficiaryDeleteView(LoginRequiredMixin, View):
    """
    利用者を、関連する記録・書類ごと消す（辞めた子・まちがえて登録した子）。ごみ箱に置くので戻せるが、確認画面を挟み、管理者だけができる。
    在籍中の人も消せるが、確認画面で「在籍中」と強く知らせる。
    """

    def _get(self, request, pk):
        b = get_object_or_404(Beneficiary, pk=pk, facility=request.user.facility)
        if not (request.user.is_admin or request.user.is_superuser):
            messages.error(request, f'{get_terms(request.user.facility)["beneficiary"]}の削除は管理者だけができます。')
            return b, redirect('beneficiaries:detail', pk=pk)
        return b, None

    def get(self, request, pk):
        b, bounce = self._get(request, pk)
        if bounce:
            return bounce
        return render(request, 'beneficiaries/delete.html',
                      {'beneficiary': b, 'counts': related_counts(b), 'retention_note': RETENTION_NOTE, 'trash_days': TRASH_DAYS})

    def post(self, request, pk):
        b, bounce = self._get(request, pk)
        if bounce:
            return bounce
        if request.POST.get('confirm_name', '').replace('\u3000', ' ').strip() != b.full_name.strip() \
                or not request.POST.get('agree'):
            messages.error(request, '氏名が合っていないか、確認の印が付いていません。削除していません。')
            return redirect('beneficiaries:delete', pk=pk)
        name, status = b.full_name, b.status
        delete_beneficiary(b, by=request.user)
        messages.success(request, f'「{name}」を削除しました（ごみ箱に {TRASH_DAYS} 日置きます。そのあいだは一覧の「ごみ箱」から戻せます）。')
        return redirect(f"{reverse('beneficiaries:list')}?status={status}")


class BeneficiaryStatusView(LoginRequiredMixin, View):
    """在籍状況だけを変える（在籍中・退所・卒業）。退所・卒業にした日が退所日に無ければ今日を入れる"""

    def post(self, request, pk):
        b = get_object_or_404(Beneficiary, pk=pk, facility=request.user.facility)
        status = request.POST.get('status')
        back = reverse('beneficiaries:detail', args=[pk])
        if status not in dict(Beneficiary.STATUS_CHOICES):
            messages.error(request, '在籍状況を選んでください。')
            return _back_to(request, back)
        set_status(b, status)
        messages.success(request, f'{b.full_name} さんを「{b.get_status_display()}」にしました。'
                         + ('一覧の在籍中には出なくなります（「' + b.get_status_display() + '」の見出しで見られます）。'
                            if status in Beneficiary.LEFT_STATUSES else ''))
        return _back_to(request, back)


class BeneficiaryBulkView(LoginRequiredMixin, View):
    """
    利用者一覧で印を付けた人をまとめて：action が在籍状況（active・inactive・graduated）なら切り替え、
    delete なら確認画面（管理者だけ）→ 確認の文字「削除」と印で、関連する記録ごと消す
    """

    def _selected(self, request):
        ids = [int(x) for x in request.POST.getlist('ids') if str(x).isdigit()]
        return list(Beneficiary.objects.filter(facility=request.user.facility, pk__in=ids)
                    .order_by('last_name_kana', 'first_name_kana', 'pk'))

    def post(self, request):
        back = reverse('beneficiaries:list')
        people = self._selected(request)
        action = request.POST.get('action', '')
        if not people:
            messages.error(request, '一覧の左の四角に印を付けてから選んでください。')
            return _back_to(request, back)
        if action in dict(Beneficiary.STATUS_CHOICES):
            for b in people:
                set_status(b, action)
            label = dict(Beneficiary.STATUS_CHOICES)[action]
            messages.success(request, f'{len(people)} 名を「{label}」にしました：' + '、'.join(b.full_name for b in people[:10])
                             + (f' ほか {len(people) - 10} 名' if len(people) > 10 else '') + '。')
            return _back_to(request, back)
        if action == 'delete':
            if not (request.user.is_admin or request.user.is_superuser):
                messages.error(request, f'{get_terms(request.user.facility)["beneficiary"]}の削除は管理者だけができます。')
                return _back_to(request, back)
            if request.POST.get('confirm') != '1':
                return render(request, 'beneficiaries/bulk_delete.html', {
                    'people': [(b, related_counts(b)) for b in people], 'retention_note': RETENTION_NOTE, 'trash_days': TRASH_DAYS,
                    'active_count': sum(1 for b in people if b.status == Beneficiary.STATUS_ACTIVE),
                    'next': request.POST.get('next', '')})
            if request.POST.get('confirm_text', '').strip() != '削除' or not request.POST.get('agree'):
                messages.error(request, '確認の文字「削除」が入っていないか、確認の印が付いていません。削除していません。')
                return _back_to(request, back)
            names = [b.full_name for b in people]
            for b in people:
                delete_beneficiary(b, by=request.user)
            messages.success(request, f'{len(names)} 名を削除しました：' + '、'.join(names[:10])
                             + (f' ほか {len(names) - 10} 名' if len(names) > 10 else '')
                             + f'。ごみ箱に {TRASH_DAYS} 日置きます（そのあいだは戻せます）。')
            return _back_to(request, back)
        messages.error(request, '操作を選んでください。')
        return _back_to(request, back)


RETENTION_YEARS = 5     # 記録の保存年数（サービスを終えた日から）


class LeftListView(LoginRequiredMixin, View):
    """
    やめた子リスト：退所・卒業した利用者の一覧（名前・かな・退所した年で探せる）。
    入所日・退所日・在籍期間・最後の利用日・療育記録の件数・記録の保存期限（退所日から 5 年）を出す
    """

    def get(self, request):
        from django.db.models import Count, Max
        facility = request.user.facility
        q = request.GET.get('q', '').strip()
        status = request.GET.get('status', '')
        year = to_int(request.GET.get('year'))
        qs = Beneficiary.objects.filter(facility=facility, status__in=Beneficiary.LEFT_STATUSES)
        if q:
            qs = qs.filter(db_models.Q(last_name__icontains=q) | db_models.Q(first_name__icontains=q)
                           | db_models.Q(last_name_kana__icontains=q) | db_models.Q(first_name_kana__icontains=q))
        if status in Beneficiary.LEFT_STATUSES:
            qs = qs.filter(status=status)
        if year:
            qs = qs.filter(discharge_date__year=year)
        qs = qs.annotate(record_count=Count('therapy_records', distinct=True), last_record=Max('therapy_records__date'))
        people = list(qs.order_by(db_models.F('discharge_date').desc(nulls_last=True), 'last_name_kana', 'pk'))
        today = datetime.date.today()
        last_visit = {}
        if facility.use_reservation:
            from reservations.models import Reservation
            last_visit = dict(Reservation.objects.filter(facility=facility, beneficiary__in=people, status=Reservation.STATUS_CONFIRMED)
                              .exclude(attendance__in=(Reservation.ATT_ABSENT, Reservation.ATT_CANCELLED))
                              .values_list('beneficiary').annotate(d=Max('date')).values_list('beneficiary', 'd'))
        rows = []
        for b in people:
            last = max(x for x in (b.last_record, last_visit.get(b.pk)) if x) if (b.last_record or last_visit.get(b.pk)) else None
            start = b.admission_date
            end = b.discharge_date or last or today
            # 在籍期間：退所日の翌日までで数える（4/1〜3/31 は 3 年）
            after = end + datetime.timedelta(days=1)
            months = (after.year - start.year) * 12 + after.month - start.month - (1 if after.day < start.day else 0) if start else None
            base = b.discharge_date or last
            keep_until = None
            if base:
                try:
                    keep_until = base.replace(year=base.year + RETENTION_YEARS)
                except ValueError:          # 2/29
                    keep_until = base.replace(year=base.year + RETENTION_YEARS, day=28)
            period = '' if months is None else (f'{months // 12}年' if months >= 12 else '') + (f'{months % 12}か月' if months % 12 or months < 12 else '')
            rows.append({'b': b, 'last': last, 'months': months, 'period': period, 'keep_until': keep_until,
                         'expired': bool(keep_until and keep_until < today)})
        years = sorted({d.year for d in Beneficiary.objects.filter(facility=facility, status__in=Beneficiary.LEFT_STATUSES)
                        .exclude(discharge_date__isnull=True).values_list('discharge_date', flat=True)}, reverse=True)
        return render(request, 'beneficiaries/left.html', {
            'rows': rows, 'q': q, 'status': status, 'year': year, 'years': years, 'today': today,
            'status_choices': [(v, l) for v, l in Beneficiary.STATUS_CHOICES if v in Beneficiary.LEFT_STATUSES],
            'retention_years': RETENTION_YEARS, 'can_delete': request.user.is_admin or request.user.is_superuser,
        })


class TrashView(LoginRequiredMixin, View):
    """ごみ箱：消した利用者の一覧と「戻す」「完全に消す」（管理者だけ。beneficiaries/trash.py）"""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not (request.user.is_admin or request.user.is_superuser):
            messages.error(request, 'ごみ箱は管理者だけが使えます。')
            return redirect('beneficiaries:list')
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        from .trash import purge_expired
        purge_expired()
        entries = DeletedBeneficiary.objects.filter(facility=request.user.facility).select_related('deleted_by')
        return render(request, 'beneficiaries/trash.html', {'entries': entries, 'trash_days': TRASH_DAYS})

    def post(self, request):
        from . import trash
        entry = get_object_or_404(DeletedBeneficiary, pk=to_int(request.POST.get('entry'), -1), facility=request.user.facility)
        if request.POST.get('action') == 'restore':
            try:
                b = trash.restore(entry)
            except ValueError as e:
                messages.error(request, str(e))
                return redirect('beneficiaries:trash')
            messages.success(request, f'{b.full_name} さんを戻しました（記録・予約・書類なども元どおり）。'
                             + (f'消したあとに相手がいなくなった結びつき {entry.skipped} 件は戻していません。' if entry.skipped else ''))
            return redirect('beneficiaries:detail', pk=b.pk)
        if request.POST.get('action') == 'purge':
            name = entry.name
            trash.purge(entry)
            messages.success(request, f'{name} さんをごみ箱から完全に消しました（戻せません）。')
        return redirect('beneficiaries:trash')


class DuplicateListView(LoginRequiredMixin, View):
    """同じ名前の利用者の組を並べ、「この人にまとめる」を選ぶ（管理者だけ。beneficiaries/merge.py）"""

    def get(self, request):
        if not (request.user.is_admin or request.user.is_superuser):
            messages.error(request, '重複のまとめは管理者だけができます。')
            return redirect('beneficiaries:list')
        from . import merge
        groups = [[(b, merge.counts(b)) for b in people] for people in merge.find_groups(request.user.facility)]
        return render(request, 'beneficiaries/duplicates.html', {'groups': groups, 'placeholder_dob': PLACEHOLDER_DOB})


class MergeView(LoginRequiredMixin, View):
    """2人を1人にまとめる。GET で見込み（移る記録・埋まる欄・重なり）を見せ、POST（確認の印）でまとめる。管理者だけ"""

    def _pair(self, request, data):
        facility = request.user.facility
        keep = get_object_or_404(Beneficiary, pk=to_int(data.get('keep'), -1), facility=facility)
        drop = get_object_or_404(Beneficiary, pk=to_int(data.get('drop'), -1), facility=facility)
        if keep.pk == drop.pk:
            raise Http404
        return keep, drop

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not (request.user.is_admin or request.user.is_superuser):
            messages.error(request, '重複のまとめは管理者だけができます。')
            return redirect('beneficiaries:list')
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        from . import merge
        keep, drop = self._pair(request, request.GET)
        return render(request, 'beneficiaries/merge.html', {'p': merge.plan(keep, drop), 'placeholder_dob': PLACEHOLDER_DOB})

    def post(self, request):
        from . import merge
        keep, drop = self._pair(request, request.POST)
        if not request.POST.get('agree'):
            messages.error(request, '確認の印が付いていません。まとめていません。')
            return redirect(f"{reverse('beneficiaries:merge')}?keep={keep.pk}&drop={drop.pk}")
        drop_label = f'{drop.full_name}（{drop.date_of_birth:%Y/%-m/%-d} 生まれ・No.{drop.pk}）'
        try:
            merge.merge(keep, drop)
        except ValueError as e:
            messages.error(request, str(e))
            return redirect(f"{reverse('beneficiaries:merge')}?keep={keep.pk}&drop={drop.pk}")
        messages.success(request, f'{drop_label} を {keep.full_name} さんにまとめました（記録・予約などを移し、空いていた欄を埋めました）。')
        return redirect('beneficiaries:detail', pk=keep.pk)


# =============================================
# 利用者詳細（支援の流れナビ付き）
# =============================================
RECENT_THERAPY_DAYS = 5     # 利用者の詳細に出す療育記録の日数（ゆあーず）


class BeneficiaryDetailView(LoginRequiredMixin, DetailView):
    """
    利用者詳細。保護者・受給者証・支援計画の流れを1画面で確認できる。
    """
    model = Beneficiary
    template_name = 'beneficiaries/detail.html'
    context_object_name = 'beneficiary'

    def get_queryset(self):
        # 自施設の利用者のみアクセス可能
        return Beneficiary.objects.filter(facility=self.request.user.facility)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        b = self.object
        today = datetime.date.today()
        ctx['guardians'] = b.guardians.all()
        ctx['status_choices'] = Beneficiary.STATUS_CHOICES
        facility = self.request.user.facility
        if facility.is_ryoiku and b.status == 'active' and reservation_enabled(facility):
            # 「今日の予定に追加」：きょうの時間枠と空き（reservations/views.py の AddTodayView で予約を作る）
            from reservations.services import add_today_choices
            ctx['add_today'] = add_today_choices(facility, b, timezone.localdate())
        ctx['certificates'] = b.recipient_certificates.all()
        ctx['offices'] = b.offices.all()
        ctx['assessments'] = b.assessments.select_related('created_by')
        ctx['assessment_kinds'] = BeneficiaryAssessment.KIND_CHOICES
        ctx['documents'] = b.documents.select_related('uploaded_by') if self.request.user.facility.is_ryoiku else []
        # 書類から分かっていること（診断書などを AI で読み取ったもの。ゆあーず）
        ctx['knowledge'], ctx['knowledge_drafts'], ctx['ai_ready'] = [], [], bool(settings.ANTHROPIC_API_KEY)
        if self.request.user.facility.is_ryoiku:
            from . import knowledge as kn
            items = list(b.knowledge.select_related('created_by'))
            ctx['knowledge'] = [k for k in items if k.is_saved]
            ctx['knowledge_drafts'] = [k for k in items if not k.is_saved]
            read_doc = {k.document_id: k for k in reversed(items) if k.document_id}
            read_asm = {k.assessment_id: k for k in reversed(items) if k.assessment_id}
            docs = list(ctx['documents'])
            for d in docs:
                d.readable = kn.is_readable(d.file_name or d.file.name)
                d.read_k = read_doc.get(d.pk)
            ctx['documents'] = docs
            asms = list(ctx['assessments'])
            for a in asms:
                a.readable = bool(a.file) and kn.is_readable(a.file_name or a.file.name)
                a.read_k = read_asm.get(a.pk)
            ctx['assessments'] = asms
        ctx['document_accept'] = 'image/*,.pdf,.xlsx,.xls,.csv'
        ctx['document_max_mb'] = DOCUMENT_MAX_BYTES // (1024 * 1024)
        ctx['office_form'] = BeneficiaryOfficeForm()
        ctx['guardian_form'] = GuardianForm()
        ctx['certificate_form'] = RecipientCertificateForm()
        # 編集モーダル用に現在の利用者データをセットしたフォームを渡す
        ctx['edit_form'] = BeneficiaryForm(instance=b, facility=b.facility)
        ctx['pair_names'] = b.pair_names()
        ctx['pair_ids'] = set(b.cannot_pair.values_list('pk', flat=True))
        ctx['sibling_ids'] = set(b.siblings.values_list('pk', flat=True))
        ctx['sibling_names'] = [s.full_name for s in b.siblings.filter(status=Beneficiary.STATUS_ACTIVE).order_by('last_name_kana', 'first_name_kana')]
        # 曜日チェックボックス用（フィールド名と表示名のペア）
        ctx['edit_weekdays'] = [
            ('weekday_mon', '月'), ('weekday_tue', '火'), ('weekday_wed', '水'),
            ('weekday_thu', '木'), ('weekday_fri', '金'), ('weekday_sat', '土'),
        ]
        # 受給者証の期限アラート判定用
        ctx['today'] = today
        ctx['cert_expiry_soon'] = today + datetime.timedelta(days=30)
        # 療育記録の直近5日分（ゆあーず。日付の新しい順に5日ぶん。同じ日に2件あればどちらも出す）
        ctx['recent_therapy'] = []
        if self.request.user.facility.is_ryoiku and getattr(self.request.user.facility, 'use_therapy_record', False):
            days = list(b.therapy_records.order_by('-date').values_list('date', flat=True).distinct()[:RECENT_THERAPY_DAYS])
            if days:
                ctx['recent_therapy'] = list(b.therapy_records.filter(date__in=days).select_related('staff')
                                             .order_by('-date', '-time', '-pk'))
            ctx['therapy_total'] = b.therapy_records.count()
        # 5領域アセスメント（評価シート）と送迎の設定（施設設定で使うにした事業所だけ。オウル）
        ctx['dev_assessments'] = (list(b.development_assessments.select_related('assessed_by'))
                                  if self.request.user.facility.use_dev_assessment else [])
        if self.request.user.facility.use_transport:
            from transport.views import profile_context
            ctx.update(profile_context(b))
        # 発達検査の推移と健康の注意（毎日の運営を使う事業所）
        if getattr(self.request.user.facility, 'use_daily_ops', False):
            from . import dev_tests
            from daily.models import HealthProfile
            tests = list(b.development_tests.all())
            ctx['dev_tests'] = tests
            ctx['dev_test_chart'] = dev_tests.trend_svg(tests)
            hp = HealthProfile.objects.filter(beneficiary=b).first()
            ctx['health_alerts'] = hp.alerts if hp else []
        # 個別支援計画（進行中のものを先頭に）
        plans = list(b.support_plans.select_related('manager'))
        ctx['support_plans'] = plans
        ctx['current_plan'] = next((p for p in plans if p.status != 'closed'), None)
        return ctx


# =============================================
# 利用者 新規登録
# =============================================
class BeneficiaryCreateView(LoginRequiredMixin, CreateView):
    model = Beneficiary
    form_class = BeneficiaryForm
    template_name = 'beneficiaries/form.html'

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), 'facility': self.request.user.facility}

    def form_valid(self, form):
        # 施設を自動でセット（手入力させない）
        form.instance.facility = self.request.user.facility
        # 先に保存を完了させてからメッセージを追加する（保存失敗時にメッセージが残らないよう順番を逆にする）
        response = super().form_valid(form)
        messages.success(self.request, f'{get_terms(self.request.user)["beneficiary"]}「{self.object.full_name}」を登録しました。')
        return response

    def get_success_url(self):
        return reverse('beneficiaries:detail', kwargs={'pk': self.object.pk})

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['page_title'] = f'{get_terms(self.request.user)["beneficiary"]} 新規登録'
        return ctx


# =============================================
# 利用者 編集
# =============================================
class BeneficiaryUpdateView(LoginRequiredMixin, UpdateView):
    model = Beneficiary
    form_class = BeneficiaryForm
    template_name = 'beneficiaries/form.html'

    def get_queryset(self):
        return Beneficiary.objects.filter(facility=self.request.user.facility)

    # 利用者の「編集」の窓（モーダル）と編集の画面（form.html）に出している項目。そこから保存したとき（_partial=1）は、
    # 画面に無い項目（住所・電話・学校・入所日など。取り込みや計画書中心の画面で入れる）を今の値のままにする（空で上書きして消さないように）
    MODAL_FIELDS = {'last_name', 'first_name', 'last_name_kana', 'first_name_kana', 'date_of_birth', 'gender',
                    'disability_class', 'disability_type', 'is_severe', 'notes', 'status', 'cannot_pair', 'siblings',
                    'weekday_mon', 'weekday_tue', 'weekday_wed', 'weekday_thu', 'weekday_fri', 'weekday_sat',
                    'line_send_journal'}

    def get_form_kwargs(self):
        kwargs = {**super().get_form_kwargs(), 'facility': self.request.user.facility}
        data = kwargs.get('data')
        if data is not None and data.get('_partial') == '1':
            data = data.copy()
            obj = self.object
            for name, field in BeneficiaryForm.base_fields.items():
                if name in self.MODAL_FIELDS:
                    continue
                value = getattr(obj, name)
                if hasattr(value, 'all'):                  # 多対多
                    data.setlist(name, [str(pk) for pk in value.values_list('pk', flat=True)])
                elif isinstance(value, bool):
                    if value:
                        data[name] = 'on'
                    else:
                        data.pop(name, None)
                elif isinstance(value, datetime.date):
                    data[name] = value.isoformat()
                else:
                    data[name] = '' if value is None else str(value)
            kwargs['data'] = data
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['pair_ids'] = set(self.object.cannot_pair.values_list('pk', flat=True)) if self.object else set()
        ctx['sibling_ids'] = set(self.object.siblings.values_list('pk', flat=True)) if self.object else set()
        return ctx

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        conflict = check_conflict(request, self.object)
        if conflict:
            messages.error(request, conflict)
            form = self.get_form()
            return self.render_to_response(self.get_context_data(form=form, conflict=conflict))
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        messages.success(self.request, f'{get_terms(self.request.user)["beneficiary"]}情報を更新しました。')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('beneficiaries:detail', kwargs={'pk': self.object.pk})

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['page_title'] = f'{get_terms(self.request.user)["beneficiary"]}編集 — {self.object.full_name}'
        return ctx


# =============================================
# 保護者 追加
# =============================================
class GuardianCreateView(LoginRequiredMixin, View):
    """利用者詳細画面から保護者を追加する"""

    def post(self, request, beneficiary_pk):
        beneficiary = get_object_or_404(
            Beneficiary, pk=beneficiary_pk, facility=request.user.facility
        )
        form = GuardianForm(request.POST)
        if form.is_valid():
            guardian = form.save(commit=False)
            guardian.beneficiary = beneficiary
            guardian.save()
            messages.success(request, '保護者を追加しました。')
        else:
            messages.error(request, '入力内容を確認してください。')
        return redirect('beneficiaries:detail', pk=beneficiary_pk)


# =============================================
# 保護者 編集
# =============================================
class GuardianUpdateView(LoginRequiredMixin, View):
    """利用者詳細画面から保護者情報を編集する"""

    def post(self, request, beneficiary_pk, guardian_pk):
        beneficiary = get_object_or_404(
            Beneficiary, pk=beneficiary_pk, facility=request.user.facility
        )
        guardian = get_object_or_404(Guardian, pk=guardian_pk, beneficiary=beneficiary)
        conflict = check_conflict(request, guardian)
        if conflict:
            messages.error(request, conflict)
            return redirect('beneficiaries:detail', pk=beneficiary_pk)
        form = GuardianForm(request.POST, instance=guardian)
        if form.is_valid():
            form.save()
            messages.success(request, '保護者情報を更新しました。')
        else:
            messages.error(request, '入力内容を確認してください。')
        return redirect('beneficiaries:detail', pk=beneficiary_pk)


# =============================================
# 利用事業所（上限額管理の相手先） 追加 / 編集 / 削除
# =============================================
class BeneficiaryOfficeCreateView(LoginRequiredMixin, View):
    """利用者詳細画面から利用事業所を追加する"""

    def post(self, request, beneficiary_pk):
        beneficiary = get_object_or_404(Beneficiary, pk=beneficiary_pk, facility=request.user.facility)
        form = BeneficiaryOfficeForm(request.POST)
        if form.is_valid():
            office = form.save(commit=False)
            office.beneficiary = beneficiary
            if office.is_this_office:
                # 当施設の行は施設設定の名前・番号をそのまま使う
                office.name = office.name or request.user.facility.name
                office.office_number = office.office_number or request.user.facility.office_number
                beneficiary.offices.filter(is_this_office=True).update(is_this_office=False)
            office.save()
            messages.success(request, '利用事業所を追加しました。')
        else:
            messages.error(request, '入力内容を確認してください。')
        return redirect('beneficiaries:detail', pk=beneficiary_pk)


class BeneficiaryOfficeUpdateView(LoginRequiredMixin, View):
    """利用事業所を編集する"""

    def post(self, request, beneficiary_pk, office_pk):
        beneficiary = get_object_or_404(Beneficiary, pk=beneficiary_pk, facility=request.user.facility)
        office = get_object_or_404(BeneficiaryOffice, pk=office_pk, beneficiary=beneficiary)
        form = BeneficiaryOfficeForm(request.POST, instance=office)
        if form.is_valid():
            office = form.save(commit=False)
            if office.is_this_office:
                beneficiary.offices.filter(is_this_office=True).exclude(pk=office.pk).update(is_this_office=False)
            office.save()
            messages.success(request, '利用事業所を更新しました。')
        else:
            messages.error(request, '入力内容を確認してください。')
        return redirect('beneficiaries:detail', pk=beneficiary_pk)


class BeneficiaryOfficeDeleteView(LoginRequiredMixin, View):
    """利用事業所を削除する（POSTのみ）"""

    def post(self, request, beneficiary_pk, office_pk):
        beneficiary = get_object_or_404(Beneficiary, pk=beneficiary_pk, facility=request.user.facility)
        office = get_object_or_404(BeneficiaryOffice, pk=office_pk, beneficiary=beneficiary)
        office.delete()
        messages.success(request, '利用事業所を削除しました。')
        return redirect('beneficiaries:detail', pk=beneficiary_pk)


# =============================================
# 受給者証 追加 / 編集
# =============================================
class RecipientCertificateCreateView(LoginRequiredMixin, View):
    """利用者詳細画面から受給者証を追加する"""

    def post(self, request, beneficiary_pk):
        beneficiary = get_object_or_404(
            Beneficiary, pk=beneficiary_pk, facility=request.user.facility
        )
        form = RecipientCertificateForm(request.POST, request.FILES)
        if form.is_valid():
            cert = form.save(commit=False)
            cert.beneficiary = beneficiary
            cert.save()
            messages.success(request, '受給者証を登録しました。')
        else:
            messages.error(request, '入力内容を確認してください。')
        return redirect('beneficiaries:detail', pk=beneficiary_pk)


class RecipientCertificateUpdateView(LoginRequiredMixin, View):
    """利用者詳細画面から受給者証を編集する"""

    def post(self, request, beneficiary_pk, cert_pk):
        beneficiary = get_object_or_404(
            Beneficiary, pk=beneficiary_pk, facility=request.user.facility
        )
        cert = get_object_or_404(RecipientCertificate, pk=cert_pk, beneficiary=beneficiary)
        conflict = check_conflict(request, cert)
        if conflict:
            messages.error(request, conflict)
            return redirect('beneficiaries:detail', pk=beneficiary_pk)
        form = RecipientCertificateForm(request.POST, request.FILES, instance=cert)
        if form.is_valid():
            form.save()
            messages.success(request, '受給者証を更新しました。')
        else:
            messages.error(request, '入力内容を確認してください。')
        return redirect('beneficiaries:detail', pk=beneficiary_pk)


# =============================================
# OCR：受給者証の画像をClaude Vision APIで読み取る
# =============================================
class RecipientCertificateOcrView(LoginRequiredMixin, View):
    """
    アップロードされた受給者証の画像を Claude Vision API に送り、
    必要項目を抽出してJSONで返す。フロント側でフォームに自動入力する。
    """

    def post(self, request, beneficiary_pk):
        # 施設チェック
        get_object_or_404(Beneficiary, pk=beneficiary_pk, facility=request.user.facility)

        image_file = request.FILES.get('image')
        if not image_file:
            return JsonResponse({'error': '画像が選択されていません。'}, status=400)

        # ファイルサイズチェック（5MB上限）
        MAX_SIZE = 5 * 1024 * 1024
        if image_file.size > MAX_SIZE:
            return JsonResponse({'error': '画像ファイルは5MB以下にしてください。'}, status=400)

        # MIMEタイプをPillowで実際に判定（content_typeはユーザーが偽造できるため）
        from PIL import Image
        import io
        ALLOWED_TYPES = {'JPEG': 'image/jpeg', 'PNG': 'image/png', 'WEBP': 'image/webp', 'GIF': 'image/gif'}
        try:
            img = Image.open(io.BytesIO(image_file.read()))
            media_type = ALLOWED_TYPES.get(img.format, '')
            if not media_type:
                return JsonResponse({'error': '対応していない画像形式です（JPEG・PNG・WEBP・GIFのみ）。'}, status=400)
        except Exception:
            return JsonResponse({'error': '画像ファイルを読み込めませんでした。'}, status=400)
        image_file.seek(0)

        # 画像をBase64エンコード（APIに送るため）
        image_data = base64.standard_b64encode(image_file.read()).decode('utf-8')

        prompt = """
この画像は放課後等デイサービスの受給者証です。
以下の項目を読み取り、JSONのみで返してください（説明文は不要）。
読み取れなかった項目は空文字にしてください。

{
  "certificate_number": "受給者証番号（数字・ハイフン）",
  "granted_days": "支給量（月あたり日数。数字のみ）",
  "monthly_cap": "負担上限月額（数字のみ、円マーク不要）",
  "valid_from": "有効期間の開始日（YYYY-MM-DD形式）",
  "valid_until": "有効期間の終了日（YYYY-MM-DD形式）",
  "municipality": "市区町村名",
  "support_office": "相談支援事業所名"
}
"""

        try:
            client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
            response = client.messages.create(
                model='claude-opus-4-6',
                max_tokens=512,
                messages=[{
                    'role': 'user',
                    'content': [
                        {
                            'type': 'image',
                            'source': {
                                'type': 'base64',
                                'media_type': media_type,
                                'data': image_data,
                            },
                        },
                        {'type': 'text', 'text': prompt},
                    ],
                }],
            )
            # レスポンスからJSONを取り出す
            text = response.content[0].text.strip()
            # コードブロック（```json ... ```）が含まれる場合は取り除く
            if text.startswith('```'):
                text = text.split('```')[1]
                if text.startswith('json'):
                    text = text[4:]
            extracted = json.loads(text)
            return JsonResponse({'result': extracted})

        except json.JSONDecodeError:
            return JsonResponse({'error': 'AIの返答をJSONに変換できませんでした。'}, status=500)
        except Exception as e:
            return JsonResponse({'error': f'OCR処理中にエラーが発生しました: {str(e)}'}, status=500)


class RegenerateLineCodeView(LoginRequiredMixin, View):
    """
    LINE登録コードを再発行する。
    古いコードは無効になり、新しいコードが発行される。
    """
    def post(self, request, guardian_pk):
        guardian = get_object_or_404(
            Guardian, pk=guardian_pk,
            beneficiary__facility=request.user.facility
        )
        guardian.issue_line_code()
        guardian.save(update_fields=['line_registration_code', 'line_code_expires_at', 'updated_at'])
        messages.success(request, f'{guardian}のLINE登録コードを再発行しました（72時間有効）。')
        return redirect('beneficiaries:detail', pk=guardian.beneficiary_id)



# =============================================
# アセスメント・資料（台帳に付ける記録と書類）
# =============================================
ASSESSMENT_EXTS = ('pdf', 'jpg', 'jpeg', 'png', 'webp', 'heic', 'gif')
ASSESSMENT_MAX_SIZE = 20 * 1024 * 1024


def _assessment_fields(request):
    """フォームの値を読む。(値の dict, エラーの文 or None)"""
    p = request.POST
    try:
        date = datetime.date.fromisoformat(p.get('date') or '')
    except ValueError:
        return None, '日付を入れてください。'
    title = (p.get('title') or '').strip()[:100]
    kinds = dict(BeneficiaryAssessment.KIND_CHOICES)
    kind = p.get('kind') if p.get('kind') in kinds else BeneficiaryAssessment.KIND_ASSESSMENT
    fields = {'date': date, 'kind': kind, 'title': title or kinds[kind], 'content': (p.get('content') or '').strip()[:20000]}
    f = request.FILES.get('file')
    if f:
        ext = f.name.rsplit('.', 1)[-1].lower() if '.' in f.name else ''
        if ext not in ASSESSMENT_EXTS:
            return None, '書類は PDF か写真（JPEG・PNG など）にしてください。'
        if f.size > ASSESSMENT_MAX_SIZE:
            return None, '書類が大きすぎます（20MB まで）。'
        fields['file'] = f
        fields['file_name'] = f.name[:200]
    return fields, None


DOCUMENT_MAX_FILES = 20


class DocumentUploadView(LoginRequiredMixin, View):
    """利用者の基本情報に書類・画像（写真・PDF・Excel・CSV）を付ける。1回に 20 個まで。ゆあーず（療育の型）だけ"""

    def post(self, request, beneficiary_pk):
        facility = request.user.facility
        if not facility.is_ryoiku:
            raise Http404
        beneficiary = get_object_or_404(Beneficiary, pk=beneficiary_pk, facility=facility)
        back = redirect(f"{reverse('beneficiaries:detail', args=[beneficiary_pk])}#documents")
        files = request.FILES.getlist('files')[:DOCUMENT_MAX_FILES]
        title = (request.POST.get('title') or '').strip()[:100]
        made, skipped = 0, []
        for f in files:
            ext = (f.name or '').lower().rsplit('.', 1)[-1] if '.' in (f.name or '') else ''
            if ext not in DOCUMENT_EXTENSIONS:
                skipped.append(f'{f.name}（この種類は入れられません）')
                continue
            if f.size > DOCUMENT_MAX_BYTES:
                skipped.append(f'{f.name}（{DOCUMENT_MAX_BYTES // (1024 * 1024)} MB を超えています）')
                continue
            BeneficiaryDocument.objects.create(beneficiary=beneficiary, file=f, file_name=(f.name or '')[:200],
                                               title=title if len(files) == 1 else '', uploaded_by=request.user)
            made += 1
        if made:
            messages.success(request, f'書類を {made} 件取り込みました。')
        if skipped:
            messages.error(request, '取り込めなかったもの：' + '、'.join(skipped))
        if not made and not skipped:
            messages.error(request, 'ファイルを選ぶか、枠の中に置いてください（写真・PDF・Excel・CSV）。')
        return back


class DocumentRenameView(LoginRequiredMixin, View):
    """書類・画像の名前（件名）を変える。元のファイル名は残す"""

    def post(self, request, beneficiary_pk, document_pk):
        beneficiary = get_object_or_404(Beneficiary, pk=beneficiary_pk, facility=request.user.facility)
        d = get_object_or_404(beneficiary.documents, pk=document_pk)
        title = (request.POST.get('title') or '').strip()[:100]
        if not title:
            messages.error(request, '名前を入れてください。')
        elif title != d.title:
            d.title = title
            d.save(update_fields=['title'])
            messages.success(request, f'書類の名前を「{title}」にしました。')
        return redirect(f"{reverse('beneficiaries:detail', args=[beneficiary_pk])}#documents")


class DocumentDeleteView(LoginRequiredMixin, View):
    def post(self, request, beneficiary_pk, document_pk):
        beneficiary = get_object_or_404(Beneficiary, pk=beneficiary_pk, facility=request.user.facility)
        d = get_object_or_404(beneficiary.documents, pk=document_pk)
        label = d.label
        if d.file:
            d.file.delete(save=False)
        d.delete()
        messages.success(request, f'「{label}」を削除しました。')
        return redirect(f"{reverse('beneficiaries:detail', args=[beneficiary_pk])}#documents")


class AssessmentCreateView(LoginRequiredMixin, View):
    """利用者詳細画面からアセスメント・資料を追加する"""

    def post(self, request, beneficiary_pk):
        beneficiary = get_object_or_404(Beneficiary, pk=beneficiary_pk, facility=request.user.facility)
        fields, error = _assessment_fields(request)
        if error is None and not fields['content'] and 'file' not in fields:
            error = '内容を書くか、書類を付けてください。'
        if error:
            messages.error(request, error)
        else:
            BeneficiaryAssessment.objects.create(beneficiary=beneficiary, created_by=request.user, **fields)
            messages.success(request, f'「{fields["title"]}」を登録しました。')
        return redirect(f"{reverse('beneficiaries:detail', args=[beneficiary_pk])}#assessments")


class AssessmentUpdateView(LoginRequiredMixin, View):
    """アセスメント・資料を直す（書類を付け替える・外すこともできる）"""

    def post(self, request, beneficiary_pk, assessment_pk):
        beneficiary = get_object_or_404(Beneficiary, pk=beneficiary_pk, facility=request.user.facility)
        a = get_object_or_404(beneficiary.assessments, pk=assessment_pk)
        conflict = check_conflict(request, a)
        if conflict:
            messages.error(request, conflict)
            return redirect(f"{reverse('beneficiaries:detail', args=[beneficiary_pk])}#assessments")
        fields, error = _assessment_fields(request)
        if error:
            messages.error(request, error)
            return redirect(f"{reverse('beneficiaries:detail', args=[beneficiary_pk])}#assessments")
        old_file = a.file.name if a.file else ''
        if request.POST.get('remove_file') and 'file' not in fields:
            fields['file'], fields['file_name'] = '', ''
        for k, v in fields.items():
            setattr(a, k, v)
        a.save()
        if old_file and old_file != (a.file.name if a.file else ''):
            a.file.storage.delete(old_file)
        messages.success(request, f'「{a.title}」を保存しました。')
        return redirect(f"{reverse('beneficiaries:detail', args=[beneficiary_pk])}#assessments")


class AssessmentDeleteView(LoginRequiredMixin, View):
    def post(self, request, beneficiary_pk, assessment_pk):
        beneficiary = get_object_or_404(Beneficiary, pk=beneficiary_pk, facility=request.user.facility)
        a = get_object_or_404(beneficiary.assessments, pk=assessment_pk)
        if a.file:
            a.file.delete(save=False)
        a.delete()
        messages.success(request, f'「{a.title}」を削除しました。')
        return redirect(f"{reverse('beneficiaries:detail', args=[beneficiary_pk])}#assessments")


# =============================================
# 利用者情報の Excel・CSV 取り込み（ゆあーず）
# =============================================
IMPORT_SESSION_KEY = 'beneficiary_import_rows'


class ImportRyoikuMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        facility = getattr(request.user, 'facility', None)
        if request.user.is_authenticated and (facility is None or not facility.is_ryoiku):
            raise Http404
        return super().dispatch(request, *args, **kwargs)


class BeneficiaryImportTemplateView(ImportRyoikuMixin, View):
    """取り込みの雛形（Excel）"""

    def get(self, request):
        from django.http import HttpResponse
        from . import importer
        res = HttpResponse(importer.template_xlsx(),
                           content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        res['Content-Disposition'] = "attachment; filename*=UTF-8''%E5%88%A9%E7%94%A8%E8%80%85_%E5%8F%96%E3%82%8A%E8%BE%BC%E3%81%BF%E9%9B%9B%E5%BD%A2.xlsx"
        return res


class BeneficiaryImportView(ImportRyoikuMixin, View):
    """
    Excel・CSV から利用者を取り込む。1) ファイルを選ぶ → 2) 行ごとの見込み（新規・書き換え・エラー）を確かめる → 3) 登録する。
    見込みはセッションに置き、「登録する」でその内容を保存する
    """
    template_name = 'beneficiaries/import.html'

    def _ctx(self, request, **extra):
        from . import importer
        return {'columns': importer.COLUMNS, 'headers': importer.HEADERS, 'max_rows': importer.MAX_ROWS,
                'guardian_headers': importer.GUARDIAN_HEADERS, 'children_headers': importer.CHILDREN_COLUMNS, **extra}

    def get(self, request):
        from django.shortcuts import render
        request.session.pop(IMPORT_SESSION_KEY, None)
        return render(request, self.template_name, self._ctx(request))

    def post(self, request):
        from django.shortcuts import render
        from . import importer
        facility = request.user.facility
        create_children = request.POST.get('create_children') == '1'
        mode = request.POST.get('mode') if request.POST.get('mode') in importer.MODES else importer.MODE_KEEP
        if request.POST.get('action') == 'commit':
            rows = request.session.pop(IMPORT_SESSION_KEY, None)
            if not rows:
                messages.error(request, '取り込む内容がありません。もう一度ファイルを選んでください。')
                return redirect('beneficiaries:import')
            if importer.format_of(rows) == importer.FORMAT_GUARDIAN:
                planned = importer.plan_guardians(facility, rows, create_children=create_children)
                made, guardians, errors = importer.apply_guardians(facility, planned, mode=mode)
                msg = f'保護者一覧を取り込みました：保護者 {guardians} 件（新しく作った児童 {made} 名）'
            else:
                planned = importer.plan(facility, rows)
                created, updated, errors = importer.apply(facility, planned, mode=mode, dob_lines=request.POST.getlist('dob'))
                msg = f'利用者を取り込みました：新規 {created} 名・台帳にいた人 {updated} 名' + \
                      ('（台帳の値を残し、空欄だけ埋めました）' if mode == importer.MODE_KEEP else '（取り込んだ値で上書きしました）') if updated else \
                      f'利用者を取り込みました：新規 {created} 名'
            if errors:
                msg += f'（読めなかった行 {errors} 件は登録していません）'
            messages.success(request, msg + '。')
            return redirect('beneficiaries:list')
        f = request.FILES.get('file')
        if f is None:
            messages.error(request, 'ファイルを選んでください（Excel の .xlsx か CSV）。')
            return render(request, self.template_name, self._ctx(request))
        try:
            rows = importer.rows_from_file(f)
        except Exception as e:  # noqa: BLE001 - 壊れたファイルなど。理由をそのまま見せる
            messages.error(request, f'ファイルを読めませんでした：{e}')
            return render(request, self.template_name, self._ctx(request))
        if not rows:
            messages.error(request, 'データの行がありません（1行目は見出し、2行目から利用者）。')
            return render(request, self.template_name, self._ctx(request))
        fmt = importer.format_of(rows)
        planned = (importer.plan_guardians(facility, rows, create_children=create_children) if fmt == importer.FORMAT_GUARDIAN
                   else importer.plan(facility, rows))
        request.session[IMPORT_SESSION_KEY] = rows
        counts = {k: sum(1 for p in planned if p['action'] == k) for k in ('create', 'update', 'error')}
        diff_count = sum(1 for p in planned if p.get('diffs'))
        return render(request, self.template_name, self._ctx(request, planned=planned, counts=counts, file_name=f.name,
                                                             fmt=fmt, create_children=create_children, diff_count=diff_count,
                                                             mode=importer.MODE_KEEP))


# =============================================
# 書類の読み取り（診断書など）→ 台帳への反映・療育記録の AI の参考（ゆあーず）
# =============================================
class KnowledgeReadView(ImportRyoikuMixin, View):
    """書類・画像（またはアセスメント・資料の書類）を AI で読み取り、確認画面へ進む"""

    def post(self, request, beneficiary_pk):
        from . import knowledge
        beneficiary = get_object_or_404(Beneficiary, pk=beneficiary_pk, facility=request.user.facility)
        back = redirect(f"{reverse('beneficiaries:detail', args=[beneficiary_pk])}#knowledge")
        source = request.POST.get('source')
        document = assessment = None
        if source == 'assessment':
            assessment = get_object_or_404(beneficiary.assessments, pk=request.POST.get('source_pk') or 0)
            file_field, name, hint = assessment.file, assessment.file_name or assessment.file.name, f'{assessment.title}\n{assessment.content}'
        else:
            document = get_object_or_404(beneficiary.documents, pk=request.POST.get('source_pk') or 0)
            file_field, name, hint = document.file, document.file_name or document.file.name, document.title
        if not file_field:
            messages.error(request, '書類が付いていません。')
            return back
        try:
            data = knowledge.read_file(beneficiary, file_field, name, hint=hint)
        except knowledge.ReadError as e:
            messages.error(request, str(e))
            return back
        draft = knowledge.make_draft(beneficiary, data, user=request.user, document=document, assessment=assessment)
        return redirect('beneficiaries:knowledge_review', beneficiary_pk=beneficiary.pk, pk=draft.pk)


class KnowledgeReviewView(ImportRyoikuMixin, View):
    """読み取った内容の確認・修正。台帳への反映（確認待ちのときだけ）・保存・破棄"""
    template_name = 'beneficiaries/knowledge_review.html'

    def _get(self, request, beneficiary_pk, pk):
        beneficiary = get_object_or_404(Beneficiary, pk=beneficiary_pk, facility=request.user.facility)
        return beneficiary, get_object_or_404(beneficiary.knowledge.select_related('document', 'assessment'), pk=pk)

    def get(self, request, beneficiary_pk, pk):
        from django.shortcuts import render
        from . import knowledge
        beneficiary, k = self._get(request, beneficiary_pk, pk)
        return render(request, self.template_name, {
            'beneficiary': beneficiary, 'k': k, 'p': k.proposals or {}, 'kinds': knowledge.KINDS,
            'therapy': getattr(request.user.facility, 'use_therapy_record', False),
        })

    def post(self, request, beneficiary_pk, pk):
        from . import knowledge
        beneficiary, k = self._get(request, beneficiary_pk, pk)
        back = f"{reverse('beneficiaries:detail', args=[beneficiary.pk])}#knowledge"
        action = request.POST.get('action')
        if action in ('discard', 'delete'):
            label = k.label
            k.delete()
            messages.success(request, f'「{label}」の読み取り結果を消しました。' if action == 'discard'
                             else f'「{label}」を書類から分かっていることから消しました（台帳に反映した内容は残ります）。')
            return redirect(back)
        conflict = check_conflict(request, k)
        if conflict:
            messages.error(request, conflict)
            return redirect('beneficiaries:knowledge_review', beneficiary_pk=beneficiary.pk, pk=k.pk)
        was_draft = not k.is_saved
        applied = knowledge.save_reviewed(k, request.POST, user=request.user)
        msg = f'「{k.label}」を保存しました。'
        if applied:
            msg += '台帳に反映：' + '・'.join(applied) + '。'
        elif was_draft:
            msg += '台帳は変えていません。'
        msg += '療育記録の AI が参考にします。' if k.use_in_ai else '療育記録の AI では使いません。'
        messages.success(request, msg)
        return redirect(back)


class KnowledgeToggleView(ImportRyoikuMixin, View):
    """療育記録の AI で参考にする／しない を切り替える"""

    def post(self, request, beneficiary_pk, pk):
        beneficiary = get_object_or_404(Beneficiary, pk=beneficiary_pk, facility=request.user.facility)
        k = get_object_or_404(beneficiary.knowledge, pk=pk)
        k.use_in_ai = not k.use_in_ai
        k.save(update_fields=['use_in_ai', 'updated_at'])
        messages.success(request, f'「{k.label}」を療育記録の AI で{"参考にします" if k.use_in_ai else "使わないようにしました"}。')
        return redirect(f"{reverse('beneficiaries:detail', args=[beneficiary.pk])}#knowledge")


# =============================================
# 5領域アセスメント（評価シート。施設設定で「5領域アセスメントを使う」にした事業所）
# =============================================
class DevAssessmentMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        facility = getattr(request.user, 'facility', None)
        if request.user.is_authenticated and not (facility is not None and facility.use_dev_assessment):
            messages.info(request, 'この事業所では5領域アセスメントを使わない設定になっています（施設設定の「使う機能」で変えられます）。')
            return redirect('facilities:dashboard')
        return super().dispatch(request, *args, **kwargs)


def _age_on(birth, day):
    if not birth:
        return None
    return day.year - birth.year - ((day.month, day.day) < (birth.month, birth.day))


def _sheet_or_404(request, beneficiary_pk, sheet_pk):
    b = get_object_or_404(Beneficiary, pk=beneficiary_pk, facility=request.user.facility)
    a = get_object_or_404(b.development_assessments.select_related('assessed_by', 'created_by'), pk=sheet_pk) if sheet_pk else None
    return b, a


class DevAssessmentFormView(DevAssessmentMixin, View):
    """5領域アセスメントを新しく作る・直す。?copy=1 で前回の内容を写して始める"""
    template_name = 'beneficiaries/dev_assessment_form.html'

    def get(self, request, beneficiary_pk, sheet_pk=None):
        b, a = _sheet_or_404(request, beneficiary_pk, sheet_pk)
        latest = b.development_assessments.order_by('-date', '-pk').first()
        if a is None:
            a = DevelopmentAssessment(beneficiary=b, date=datetime.date.today(), assessed_by=request.user)
            if request.GET.get('copy') and latest:
                a.domains = latest.domains
                a.interviewed_with = latest.interviewed_with
                for f in ('strengths', 'concerns', 'wishes_child', 'wishes_family', 'summary'):
                    setattr(a, f, getattr(latest, f))
            previous = latest
        else:
            previous = a.previous()
        from . import sheet_plan
        rows = a.rows(previous)
        plan = sheet_plan.current_plan(b) if a.pk else None
        return render(request, self.template_name, {
            'beneficiary': b, 'sheet': a, 'previous': previous, 'rows': rows,
            'ratings': DevelopmentAssessment.RATINGS, 'age': _age_on(b.date_of_birth, a.date),
            'text_fields': [(f, DevelopmentAssessment._meta.get_field(f).verbose_name)
                            for f in ('strengths', 'concerns', 'wishes_child', 'wishes_family', 'summary')],
            'radar': sheet_plan.radar_svg(rows) if any(r['rating'] for r in rows) else '',
            'plan': plan, 'plan_importable': bool(plan and plan.current_step <= 2),
        })

    def post(self, request, beneficiary_pk, sheet_pk=None):
        b, a = _sheet_or_404(request, beneficiary_pk, sheet_pk)
        p = request.POST
        if a is not None:
            conflict = check_conflict(request, a)
            if conflict:
                messages.error(request, conflict)
                return redirect('beneficiaries:dev_assessment_edit', beneficiary_pk, a.pk)
        try:
            date = datetime.date.fromisoformat(p.get('date') or '')
        except ValueError:
            messages.error(request, '実施日を入れてください。')
            return redirect(request.get_full_path())
        if a is None:
            a = DevelopmentAssessment(beneficiary=b, assessed_by=request.user, created_by=request.user)
        a.date = date
        a.interviewed_with = (p.get('interviewed_with') or '').strip()[:100]
        a.domains = DevelopmentAssessment.domains_from_post(p)
        for f in ('strengths', 'concerns', 'wishes_child', 'wishes_family', 'summary'):
            setattr(a, f, (p.get(f) or '').strip()[:DevelopmentAssessment.TEXT_MAX])
        a.save()
        messages.success(request, f'{b.full_name} さんの5領域アセスメント（{a.date:%-m月%-d日}）を保存しました。')
        return redirect('beneficiaries:dev_assessment_edit', beneficiary_pk, a.pk)


class DevAssessmentPdfView(DevAssessmentMixin, View):
    """5領域アセスメントの A4 の用紙（PDF。?fmt=html で画面）"""

    def get(self, request, beneficiary_pk, sheet_pk):
        b, a = _sheet_or_404(request, beneficiary_pk, sheet_pk)
        previous = a.previous()
        from . import sheet_plan
        rows = a.rows(previous)
        return pdf_or_html(request, 'beneficiaries/pdf/dev_assessment.html', {
            'facility': request.user.facility, 'beneficiary': b, 'sheet': a, 'previous': previous, 'rows': rows,
            'ratings': DevelopmentAssessment.RATINGS, 'age': _age_on(b.date_of_birth, a.date),
            'radar': sheet_plan.radar_svg(rows, size=150, short=True) if any(r['rating'] for r in rows) else '',
        }, f'5領域アセスメント_{b.full_name}_{a.date:%Y%m%d}')


class DevAssessmentDeleteView(DevAssessmentMixin, View):
    def post(self, request, beneficiary_pk, sheet_pk):
        b, a = _sheet_or_404(request, beneficiary_pk, sheet_pk)
        label = f'{a.date:%-m月%-d日}'
        a.delete()
        messages.success(request, f'{b.full_name} さんの5領域アセスメント（{label}）を削除しました。')
        return redirect(f"{reverse('beneficiaries:detail', args=[b.pk])}#dev-assessments")


class DevAssessmentToPlanView(DevAssessmentMixin, View):
    """用紙を個別支援計画に取り込む（plan を指定しなければ、終了していない一番新しい計画。無ければ新しく作る）"""

    def post(self, request, beneficiary_pk, sheet_pk):
        from support_plans.models import SupportPlan
        from . import sheet_plan
        b, a = _sheet_or_404(request, beneficiary_pk, sheet_pk)
        plan_id = request.POST.get('plan')
        if plan_id:
            plan = get_object_or_404(SupportPlan, pk=plan_id, beneficiary=b, facility=request.user.facility)
        else:
            plan = sheet_plan.current_plan(b)
            if plan is None:
                plan = sheet_plan.new_plan(b, request.user)
                messages.info(request, f'{b.full_name} さんの「{plan.title}」を新しく作りました。')
        try:
            result = sheet_plan.import_sheet(a, plan)
        except ValueError as e:
            messages.error(request, str(e))
            return redirect('beneficiaries:dev_assessment_edit', beneficiary_pk, a.pk)
        messages.success(request, sheet_plan.import_message(a, plan, result))
        return redirect('support_plans:step', pk=plan.pk, n=plan.current_step)


# =============================================
# 発達検査の結果（毎日の運営を使う事業所）
# =============================================
class DailyOpsMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        facility = getattr(request.user, 'facility', None)
        if request.user.is_authenticated and not (facility is not None and facility.use_daily_ops):
            messages.info(request, 'この事業所では「毎日の運営」を使わない設定になっています（施設設定の「使う機能」で変えられます）。')
            return redirect('facilities:dashboard')
        return super().dispatch(request, *args, **kwargs)


DEV_TEST_SESSION = 'dev_test_draft'


def _readable_sources(b):
    """AI で読み取れる書類（書類・画像と、アセスメント・資料の書類）"""
    from . import knowledge as kn
    out = []
    for d in b.documents.order_by('-created_at')[:30]:
        if kn.is_readable(d.file_name or d.file.name):
            out.append((f'doc:{d.pk}', f'書類：{d.label}'))
    for a in b.assessments.exclude(file='').order_by('-date')[:30]:
        if kn.is_readable(a.file_name or a.file.name):
            out.append((f'asm:{a.pk}', f'アセスメント・資料：{a.title}（{a.date:%Y/%-m/%-d}）'))
    return out


class DevTestFormView(DailyOpsMixin, View):
    template_name = 'beneficiaries/dev_test_form.html'

    def _get(self, request, beneficiary_pk, test_pk):
        from .models import DevelopmentTest
        b = get_object_or_404(Beneficiary, pk=beneficiary_pk, facility=request.user.facility)
        t = get_object_or_404(b.development_tests, pk=test_pk) if test_pk else DevelopmentTest(beneficiary=b, date=datetime.date.today())
        return b, t

    def get(self, request, beneficiary_pk, test_pk=None):
        from .models import DevelopmentTest
        b, t = self._get(request, beneficiary_pk, test_pk)
        draft = request.session.pop(DEV_TEST_SESSION, None) if not test_pk else None
        if draft and draft.get('beneficiary') == b.pk:
            d = draft['data']
            t.test, t.test_other, t.examiner, t.note = d['test'], d['test_other'], d['examiner'], d['note']
            t.ca_months, t.overall_age_months, t.overall_quotient, t.results = d['ca_months'], d['overall_age_months'], d['overall_quotient'], d['results']
            t.source_label = draft.get('source', '')
            if d.get('date'):
                t.date = datetime.date.fromisoformat(d['date'])
        elif not t.pk:
            kind = request.GET.get('test') if request.GET.get('test') in DevelopmentTest.TEST_LABELS else 'kshiki'
            t.test = kind
            t.results = [{'label': lab, 'age_months': None, 'quotient': None} for lab in DevelopmentTest.DOMAINS[kind]]
            if b.date_of_birth:
                bd, today = b.date_of_birth, datetime.date.today()
                t.ca_months = (today.year - bd.year) * 12 + today.month - bd.month - (1 if today.day < bd.day else 0)
        rows = t.result_rows()
        rows += [{'label': '', 'age_months': None, 'quotient': None}] * max(0, 6 - len(rows))
        rows = [dict(r, y=(r['age_months'] // 12 if r.get('age_months') is not None else ''),
                     m=(r['age_months'] % 12 if r.get('age_months') is not None else '')) for r in rows][:12]
        split = lambda v: ('', '') if v is None else (v // 12, v % 12)
        return render(request, self.template_name, {
            'beneficiary': b, 'test': t, 'rows': rows, 'tests': DevelopmentTest.TESTS, 'domains': DevelopmentTest.DOMAINS,
            'ca': split(t.ca_months), 'oa': split(t.overall_age_months), 'sources': _readable_sources(b),
            'ai_ready': bool(settings.ANTHROPIC_API_KEY), 'from_ai': bool(draft),
        })

    def post(self, request, beneficiary_pk, test_pk=None):
        from . import dev_tests
        b, t = self._get(request, beneficiary_pk, test_pk)
        if t.pk:
            conflict = check_conflict(request, t)
            if conflict:
                messages.error(request, conflict)
                return redirect('beneficiaries:dev_test_edit', b.pk, t.pk)
        else:
            t.created_by = request.user
        error = dev_tests.read_form(t, request.POST)
        if error:
            messages.error(request, error)
            return redirect(request.get_full_path())
        t.save()
        messages.success(request, f'{t.test_name}（{t.date:%Y/%-m/%-d}）の結果を保存しました。')
        return redirect(f"{reverse('beneficiaries:detail', args=[b.pk])}#dev-tests")


class DevTestReadView(DailyOpsMixin, View):
    """書類を AI で読み取り、検査結果の入力画面に下書きを入れる"""

    def post(self, request, beneficiary_pk):
        from . import dev_tests, knowledge
        b = get_object_or_404(Beneficiary, pk=beneficiary_pk, facility=request.user.facility)
        kind, _, pk = (request.POST.get('source') or '').partition(':')
        if kind == 'doc':
            src = get_object_or_404(b.documents, pk=to_int_safe(pk))
            file_field, name, label = src.file, src.file_name or src.file.name, f'書類：{src.label}'
        elif kind == 'asm':
            src = get_object_or_404(b.assessments, pk=to_int_safe(pk))
            file_field, name, label = src.file, src.file_name or src.file.name, f'アセスメント・資料：{src.title}'
        else:
            messages.error(request, '読み取る書類を選んでください。')
            return redirect('beneficiaries:dev_test_create', b.pk)
        try:
            data = dev_tests.read_test(b, file_field, name)
        except knowledge.ReadError as e:
            messages.error(request, str(e))
            return redirect('beneficiaries:dev_test_create', b.pk)
        request.session[DEV_TEST_SESSION] = {'beneficiary': b.pk, 'data': data, 'source': label}
        messages.info(request, f'「{label}」を読み取りました。数値を書類と見比べて、直してから保存してください。')
        return redirect('beneficiaries:dev_test_create', b.pk)


def to_int_safe(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return -1


class DevTestDeleteView(DailyOpsMixin, View):
    def post(self, request, beneficiary_pk, test_pk):
        b = get_object_or_404(Beneficiary, pk=beneficiary_pk, facility=request.user.facility)
        t = get_object_or_404(b.development_tests, pk=test_pk)
        label = f'{t.test_name}（{t.date:%Y/%-m/%-d}）'
        t.delete()
        messages.success(request, f'{label}を削除しました。')
        return redirect(f"{reverse('beneficiaries:detail', args=[b.pk])}#dev-tests")


# =============================================
# 面談資料（記録のまとめ）：期間の記録から数字と文章のまとめ → 直して保存・印刷
# =============================================
class DigestView(LoginRequiredMixin, View):
    """一覧と作成。期間（過去 3 か月・半年・1 か月・日付を指定）を選んで「AI でまとめる」"""
    template_name = 'beneficiaries/digest_list.html'

    def _beneficiary(self, request, pk):
        return get_object_or_404(Beneficiary, pk=pk, facility=request.user.facility)

    def get(self, request, pk):
        from . import digest
        b = self._beneficiary(request, pk)
        start, end = digest.period_range('3m')
        data = digest.collect(b, start, end)
        return render(request, self.template_name, {
            'beneficiary': b, 'digests': b.digests.all()[:20], 'periods': digest.PERIODS,
            'preview': data['stats'], 'preview_from': start, 'preview_to': end,
        })

    def post(self, request, pk):
        from . import digest
        b = self._beneficiary(request, pk)
        back = redirect('beneficiaries:digest', pk=pk)
        key = request.POST.get('period', '3m')
        try:
            d_from = datetime.date.fromisoformat(request.POST.get('date_from', '')) if request.POST.get('date_from') else None
            d_to = datetime.date.fromisoformat(request.POST.get('date_to', '')) if request.POST.get('date_to') else None
        except ValueError:
            messages.error(request, '日付が正しくありません。')
            return back
        if key == 'custom' and not (d_from and d_to and d_from <= d_to):
            messages.error(request, '期間を指定するときは、から・まで の両方の日付を入れてください。')
            return back
        start, end = digest.period_range(key, date_from=d_from, date_to=d_to)
        data = digest.collect(b, start, end)
        if not data['lines']:
            messages.error(request, f'{start:%Y/%m/%d}〜{end:%Y/%m/%d} の日誌・療育記録がありません。期間を広げてください。')
            return back
        text, error = digest.summarize(b, start, end, data)
        if error:
            try:
                msg = json.loads(error.content).get('error')
            except Exception:  # noqa: BLE001
                msg = None
            messages.error(request, msg or 'AI でまとめられませんでした。もう一度お試しください。')
            return back
        stats = dict(data['stats'], goals=len(data['goals']), lines=len(data['lines']))
        item = RecordDigest.objects.create(beneficiary=b, date_from=start, date_to=end, summary=text, stats=stats,
                                           purpose=request.POST.get('purpose', '').strip()[:50], created_by=request.user)
        messages.success(request, '面談資料のまとめを作りました。記録と見比べて、直してから保存・印刷してください。')
        return redirect('beneficiaries:digest_detail', pk=pk, digest_pk=item.pk)


class DigestDetailView(LoginRequiredMixin, View):
    """まとめを見る・直す・消す。?print=1 で A4 の印刷（PDF）"""

    def _get(self, request, pk, digest_pk):
        b = get_object_or_404(Beneficiary, pk=pk, facility=request.user.facility)
        return b, get_object_or_404(RecordDigest, pk=digest_pk, beneficiary=b)

    def get(self, request, pk, digest_pk):
        from . import digest
        b, item = self._get(request, pk, digest_pk)
        ctx = {'beneficiary': b, 'item': item, 'facility': request.user.facility,
               'sources': digest.collect(b, item.date_from, item.date_to)['lines']}
        if request.GET.get('print'):
            return pdf_or_html(request, 'beneficiaries/pdf/digest.html', ctx,
                               f'面談資料_{b.full_name}_{item.date_from:%Y%m%d}-{item.date_to:%Y%m%d}')
        return render(request, 'beneficiaries/digest_detail.html', ctx)

    def post(self, request, pk, digest_pk):
        b, item = self._get(request, pk, digest_pk)
        if request.POST.get('action') == 'delete':
            item.delete()
            messages.success(request, '面談資料を削除しました。')
            return redirect('beneficiaries:digest', pk=pk)
        item.summary = request.POST.get('summary', '').strip()[:20000]
        item.purpose = request.POST.get('purpose', '').strip()[:50]
        item.save(update_fields=['summary', 'purpose', 'updated_at'])
        messages.success(request, '面談資料を保存しました。')
        return redirect('beneficiaries:digest_detail', pk=pk, digest_pk=item.pk)

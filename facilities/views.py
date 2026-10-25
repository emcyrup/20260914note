import datetime
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import OuterRef, Subquery
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import TemplateView

from beneficiaries.models import Beneficiary, RecipientCertificate
from records.models import ActivityTag
from schedules.models import ScheduledVisit
from accounts.views import AdminOnlyMixin
from config import concurrency
from billing import codes
from .addon_defaults import DEFAULT_ADDONS, load_default_addons  # noqa: F401（tests から参照）
from .forms import ActivityTagForm, FacilityForm, SupportContentTagForm
from .models import AddonMaster, Facility, FacilityAddonSetting, SupportContentTag, facility_addon_rows
from config.utils import to_int


class DashboardView(LoginRequiredMixin, TemplateView):
    """
    ログイン後のトップページ（ダッシュボード）。
    各利用者の「最新の受給者証」だけを対象にアラートを表示する。
    """
    template_name = 'facilities/dashboard.html'

    def get(self, request, *args, **kwargs):
        # かんたん3ステップの画面では「きょうの きろく」がホーム
        if request.user.ui_theme == 'simple':
            return redirect('records:simple_home')
        # 計画書中心の画面（シンプル）では利用者一覧がホーム
        facility = getattr(request.user, 'facility', None)
        if facility is not None and facility.is_planbook:
            return redirect('planbook:students')
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        facility = self.request.user.facility
        today = datetime.date.today()
        soon = today + datetime.timedelta(days=30)

        # 利用者ごとに最新の受給者証（valid_until が最も新しい1件）のPKを取得
        latest_cert_pk_subq = (
            RecipientCertificate.objects
            .filter(beneficiary=OuterRef('pk'))
            .order_by('-valid_until')
            .values('pk')[:1]
        )
        active_beneficiaries = (
            Beneficiary.objects
            .filter(facility=facility, status='active')
            .annotate(latest_cert_pk=Subquery(latest_cert_pk_subq))
        )
        latest_cert_pks = [b.latest_cert_pk for b in active_beneficiaries if b.latest_cert_pk]

        # 有効期限切れ（最新証が今日より過去）
        expired_certs = (
            RecipientCertificate.objects
            .filter(pk__in=latest_cert_pks, valid_until__lt=today)
            .select_related('beneficiary')
            .order_by('valid_until')
        )

        # まもなく期限切れ（today <= valid_until <= today+30日）
        expiring_certs = (
            RecipientCertificate.objects
            .filter(pk__in=latest_cert_pks, valid_until__gte=today, valid_until__lte=soon)
            .select_related('beneficiary')
            .order_by('valid_until')
        )

        # 今日の予定（欠席以外）
        today_schedules = (
            ScheduledVisit.objects
            .filter(facility=facility, date=today)
            .exclude(status='absent')
            .select_related('beneficiary')
            .order_by('beneficiary__last_name_kana')
        )

        # 週間の来所状況（月〜日）：予定と日誌の作成状況
        from records.models import DailyRecord
        week_start = today - datetime.timedelta(days=today.weekday())
        week_dates = [week_start + datetime.timedelta(days=i) for i in range(7)]
        week_visits = (ScheduledVisit.objects
                       .filter(facility=facility, date__range=(week_dates[0], week_dates[-1]))
                       .exclude(status='absent').select_related('beneficiary')
                       .order_by('date', 'beneficiary__last_name_kana'))
        recorded = set(DailyRecord.objects
                       .filter(facility=facility, date__range=(week_dates[0], week_dates[-1]))
                       .values_list('beneficiary_id', 'date'))
        by_day = {d: [] for d in week_dates}
        for v in week_visits:
            by_day[v.date].append({'visit': v, 'has_record': (v.beneficiary_id, v.date) in recorded})
        weekday_names = ['月', '火', '水', '木', '金', '土', '日']
        week_info = [{
            'date': d, 'weekday_ja': weekday_names[d.weekday()], 'is_today': d == today,
            'is_saturday': d.weekday() == 5, 'is_sunday': d.weekday() == 6,
            'entries': by_day[d], 'count': len(by_day[d]),
            'recorded': sum(1 for e in by_day[d] if e['has_record']),
        } for d in week_dates]

        # 個別支援計画の計画期間・モニタリングの期日（30日以内と期限切れ）
        from . import reminders
        plan_items = reminders.collect(facility, today, kinds=(reminders.KIND_PLAN, reminders.KIND_MONITORING))
        # 書いていない日誌（きのうまで 14 日ぶん。来所したのに日誌が無い・下書きのまま）
        from records import unwritten
        unwritten_rows = unwritten.collect(facility, today) if unwritten.enabled(facility) else []

        ctx.update({
            'today':           today,
            'soon':            soon,
            'plan_items':      plan_items,
            'unwritten_rows':  unwritten_rows,
            'unwritten_days':  unwritten.DEFAULT_DAYS,
            'expired_certs':   expired_certs,
            'expiring_certs':  expiring_certs,
            'today_schedules': today_schedules,
            'week_info':       week_info,
            'week_start':      week_dates[0],
            'week_end':        week_dates[-1],
        })
        return ctx


# =============================================
# 施設設定 トップページ（タグ一覧＋施設情報まとめ）
# =============================================
class SettingsView(LoginRequiredMixin, TemplateView):
    """施設設定のトップ。活動タグ・支援タグ・施設情報をまとめて管理する。"""
    template_name = 'settings/index.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        facility = self.request.user.facility

        # 加算の一覧（個別・体制）と、この施設のON/OFF・コード・単位数
        addons = AddonMaster.objects.filter(is_active=True)
        addon_list = facility_addon_rows(facility)
        addon_individual = [r for r in addon_list if r.addon.addon_type == 'individual']
        addon_facility = [r for r in addon_list if r.addon.addon_type == 'facility']

        from ai_assist.models import ReferenceDocument
        ctx['reference_documents'] = ReferenceDocument.objects.filter(facility=facility)
        ctx['is_admin'] = self.request.user.is_admin or self.request.user.is_superuser
        ctx['ai_enabled'] = bool(settings.ANTHROPIC_API_KEY)
        from records import quality
        ctx['journal_rule_choices'] = [(k, label, section) for k, label, section, _t, _n in quality.RULES]
        from records import questions
        from records.models import QuestionSetting
        ctx['question_setting'] = QuestionSetting.objects.filter(facility=facility).first()
        ctx['question_default'] = questions.DEFAULT_COMMON
        ctx.update({
            'facility':      facility,
            'facility_form': FacilityForm(instance=facility),
            'mail_source': __import__('facilities.mail', fromlist=['source_label']).source_label(facility),
            'mail_server_ok': bool(__import__('django.conf', fromlist=['settings']).settings.EMAIL_HOST),
            'activity_tags': ActivityTag.objects.filter(facility=facility),
            'support_tags':  SupportContentTag.objects.filter(facility=facility),
            'activity_form': ActivityTagForm(),
            'support_form':  SupportContentTagForm(),
            'addon_list':    addon_list,
            'addon_groups':  [('個別加算（利用者×日）', addon_individual), ('体制加算（施設全体・月単位）', addon_facility)],
            'unit_price':    facility.unit_price,
            'base_units_table': codes.units_table(facility),
            'base_codes_auto': {k: codes.table_code(facility.capacity_class, k) for k in (1, 2, 3)},
            'no_addons':     not addons.exists(),
        })
        return ctx


class FacilityUpdateView(AdminOnlyMixin, View):
    """施設基本情報を更新する"""

    def post(self, request):
        facility = request.user.facility
        conflict = concurrency.check_conflict(request, facility)
        if conflict:
            messages.error(request, conflict)
            return redirect('facilities:settings')
        form = FacilityForm(request.POST, request.FILES, instance=facility)
        if form.is_valid():
            form.save()
            messages.success(request, '施設情報を更新しました。')
        else:
            messages.error(request, '入力内容に誤りがあります。')
        return redirect('facilities:settings')


class FeatureSettingsView(LoginRequiredMixin, View):
    """使う機能（請求・LINE）と、日誌で AI が作る項目の順番を保存する（管理者のみ。帳票様式は開発向けユーザーのみ）"""

    def post(self, request):
        if not (request.user.is_admin or request.user.is_superuser):
            messages.error(request, 'この設定を変えられるのは管理者だけです。')
            return redirect('facilities:settings')
        facility = request.user.facility
        conflict = concurrency.check_conflict(request, facility)
        if conflict:
            messages.error(request, conflict)
            return redirect('facilities:settings')
        facility.use_billing = 'use_billing' in request.POST
        facility.use_schedule = 'use_schedule' in request.POST
        facility.use_line = 'use_line' in request.POST
        facility.line_auto_send = 'line_auto_send' in request.POST
        facility.use_reservation = 'use_reservation' in request.POST
        facility.use_therapy_record = 'use_therapy_record' in request.POST
        facility.therapy_voice_direct = 'therapy_voice_direct' in request.POST
        facility.knowledge_reflect = 'knowledge_reflect' in request.POST
        facility.beneficiary_tools = 'beneficiary_tools' in request.POST
        facility.schedule_tools = 'schedule_tools' in request.POST
        facility.therapy_no_wording = 'therapy_no_wording' in request.POST
        facility.plan_no_ai = 'plan_no_ai' in request.POST
        facility.use_transport = 'use_transport' in request.POST
        facility.use_dev_assessment = 'use_dev_assessment' in request.POST
        facility.use_survey = 'use_survey' in request.POST
        facility.use_daily_ops = 'use_daily_ops' in request.POST
        valid = [k for k, _ in Facility.JOURNAL_SECTIONS]
        keys = [k for k in request.POST.getlist('journal_sections') if k in valid]
        if not keys:
            messages.error(request, '日誌の項目は1つ以上選んでください。')
            return redirect('facilities:settings')
        facility.journal_sections = keys
        facility.speech_words = request.POST.get('speech_words', facility.speech_words).strip()[:2000]
        facility.word_rules = request.POST.get('word_rules', facility.word_rules).strip()[:4000]
        # 日誌を確定するのに要る項目と最低字数（records/quality.py）
        if 'journal_required_form' in request.POST:
            from records import quality
            facility.journal_required = [k for k, *_ in quality.RULES if k in request.POST.getlist('journal_required')]
            try:
                facility.journal_min_chars = max(0, min(quality.MIN_CHARS_MAX, int(request.POST.get('journal_min_chars') or 0)))
            except ValueError:
                pass
        # 日誌の質問（共通の質問・個別の質問。records/questions.py）
        if 'question_form' in request.POST:
            from records import questions
            from records.models import QuestionSetting
            qs = QuestionSetting.objects.filter(facility=facility).first() or QuestionSetting(facility=facility)
            qs.enabled = 'question_enabled' in request.POST
            qs.common_questions = '\n'.join(questions.lines(request.POST.get('question_common', '')))
            for name, lo, hi in (('window', 1, 50), ('rate', 1, 100)):
                try:
                    setattr(qs, name, max(lo, min(hi, int(request.POST.get(f'question_{name}') or getattr(qs, name)))))
                except ValueError:
                    pass
            if qs.pk or qs.enabled:
                qs.save()
        # 帳票様式は開発向けユーザーだけが変えられる（他の事業所の様式名を管理者に見せない）
        if request.user.can_switch_facility:
            form_set = request.POST.get('form_set', facility.form_set)
            if form_set in dict(Facility.FORM_SET_CHOICES):
                facility.form_set = form_set
            layout = request.POST.get('layout', facility.layout)
            if layout in dict(Facility.LAYOUT_CHOICES):
                facility.layout = layout
            try:
                facility.trial_ai_limit = max(0, min(9999, int(request.POST.get('trial_ai_limit', facility.trial_ai_limit))))
            except (TypeError, ValueError):
                pass
            if request.POST.get('trial_ai_reset'):
                facility.trial_ai_used = 0
        facility.save(update_fields=['use_billing', 'use_schedule', 'use_line', 'line_auto_send', 'journal_required', 'journal_min_chars',
                                     'use_reservation', 'use_therapy_record', 'therapy_voice_direct', 'knowledge_reflect', 'beneficiary_tools', 'schedule_tools', 'therapy_no_wording', 'plan_no_ai',
                                     'use_transport', 'use_dev_assessment', 'use_survey', 'use_daily_ops',
                                     'journal_sections', 'form_set', 'layout', 'trial_ai_limit', 'trial_ai_used',
                                     'speech_words', 'word_rules', 'updated_at'])
        messages.success(request, '使う機能と日誌の項目を保存しました。')
        return redirect('facilities:settings')


# =============================================
# 活動タグ 追加・編集・削除
# =============================================
class ActivityTagCreateView(AdminOnlyMixin, View):
    """活動タグを追加する"""

    def post(self, request):
        facility = request.user.facility
        form = ActivityTagForm(request.POST)
        if form.is_valid():
            tag = form.save(commit=False)
            tag.facility = facility
            tag.save()
            messages.success(request, f'活動タグ「{tag.name}」を追加しました。')
        else:
            messages.error(request, '入力内容を確認してください。')
        return redirect('facilities:settings')


class ActivityTagUpdateView(AdminOnlyMixin, View):
    """活動タグを編集する"""

    def post(self, request, pk):
        tag = get_object_or_404(ActivityTag, pk=pk, facility=request.user.facility)
        form = ActivityTagForm(request.POST, instance=tag)
        if form.is_valid():
            form.save()
            messages.success(request, f'活動タグ「{tag.name}」を更新しました。')
        else:
            messages.error(request, '入力内容を確認してください。')
        return redirect('facilities:settings')


class ActivityTagDeleteView(AdminOnlyMixin, View):
    """活動タグを削除する"""

    def post(self, request, pk):
        tag = get_object_or_404(ActivityTag, pk=pk, facility=request.user.facility)
        name = tag.name
        tag.delete()
        messages.success(request, f'活動タグ「{name}」を削除しました。')
        return redirect('facilities:settings')


class ActivityTagLoadDefaultsView(AdminOnlyMixin, View):
    """標準の活動タグを一括追加する"""

    DEFAULT_TAGS = [
        ('宿題・学習支援', 0), ('制作活動', 1), ('運動・体育活動', 2),
        ('音楽活動', 3), ('調理活動', 4), ('買い物学習', 5),
        ('野外活動・遠足', 6), ('ゲーム・遊び', 7), ('読み聞かせ・絵本', 8),
        ('おやつ', 9), ('食事', 10), ('自由遊び', 11),
        ('個別学習', 12), ('SST（ソーシャルスキルトレーニング）', 13),
        ('生活スキル訓練', 14), ('ICT・タブレット活動', 15),
    ]

    def post(self, request):
        facility = request.user.facility
        added = 0
        for name, order in self.DEFAULT_TAGS:
            if not ActivityTag.objects.filter(facility=facility, name=name).exists():
                ActivityTag.objects.create(facility=facility, name=name, display_order=order)
                added += 1
        if added:
            messages.success(request, f'標準タグを {added} 件追加しました。')
        else:
            messages.info(request, 'すでにすべての標準タグが登録されています。')
        return redirect('facilities:settings')


# =============================================
# 支援内容タグ 追加・編集・削除
# =============================================
class SupportTagCreateView(AdminOnlyMixin, View):
    """支援内容タグを追加する"""

    def post(self, request):
        facility = request.user.facility
        form = SupportContentTagForm(request.POST)
        if form.is_valid():
            tag = form.save(commit=False)
            tag.facility = facility
            tag.save()
            messages.success(request, f'支援タグ「{tag.name}」を追加しました。')
        else:
            messages.error(request, '入力内容を確認してください。')
        return redirect('facilities:settings')


class SupportTagUpdateView(AdminOnlyMixin, View):
    """支援内容タグを編集する"""

    def post(self, request, pk):
        tag = get_object_or_404(SupportContentTag, pk=pk, facility=request.user.facility)
        form = SupportContentTagForm(request.POST, instance=tag)
        if form.is_valid():
            form.save()
            messages.success(request, f'支援タグ「{tag.name}」を更新しました。')
        else:
            messages.error(request, '入力内容を確認してください。')
        return redirect('facilities:settings')


class SupportTagDeleteView(AdminOnlyMixin, View):
    """支援内容タグを削除する"""

    def post(self, request, pk):
        tag = get_object_or_404(SupportContentTag, pk=pk, facility=request.user.facility)
        name = tag.name
        tag.delete()
        messages.success(request, f'支援タグ「{name}」を削除しました。')
        return redirect('facilities:settings')


class SupportTagLoadDefaultsView(AdminOnlyMixin, View):
    """標準の支援内容タグを一括追加する"""

    DEFAULT_TAGS = [
        ('個別支援', 0), ('集団活動', 1), ('声かけ・励まし', 2),
        ('見守り', 3), ('身体介助', 4), ('感情調整支援', 5),
        ('コミュニケーション支援', 6), ('行動観察・記録', 7),
        ('保護者連絡', 8), ('迎え支援（送迎加算）', 9), ('送り支援（送迎加算）', 10),
        ('入浴支援', 11), ('環境調整', 12),
    ]

    def post(self, request):
        facility = request.user.facility
        added = 0
        for name, order in self.DEFAULT_TAGS:
            if not SupportContentTag.objects.filter(facility=facility, name=name).exists():
                SupportContentTag.objects.create(facility=facility, name=name, order=order)
                added += 1
        if added:
            messages.success(request, f'標準タグを {added} 件追加しました。')
        else:
            messages.info(request, 'すでにすべての標準タグが登録されています。')
        return redirect('facilities:settings')


# =============================================
# 加算マスタ・施設加算設定
# =============================================

def to_rate(value):
    """加算率の入力（%）→ Decimal（小数 1 桁、0 より大きく 50 以下）。読めなければ None"""
    try:
        rate = Decimal(str(value).strip().rstrip('%％')).quantize(Decimal('0.1'))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return rate if Decimal(0) < rate <= Decimal(50) else None


class AddonSettingView(AdminOnlyMixin, View):
    """
    施設が算定する加算（個別・体制）の ON/OFF と、事業所ごとのサービスコード・単位数を保存する。
    コード・単位数を空にするとマスタの値に戻る。
    """

    def post(self, request):
        facility = request.user.facility
        addons = AddonMaster.objects.filter(is_active=True)
        enabled_ids = set(request.POST.getlist('addon_ids'))
        for addon in addons:
            code = (request.POST.get(f'code_{addon.pk}') or '').strip()[:10]
            units = to_int(request.POST.get(f'units_{addon.pk}'))
            by_cap = codes.capacity_addon(addon.name, facility.capacity_class) if facility.capacity_class else None
            if code == addon.code or (by_cap and code == by_cap[0]):
                code = ''
            if units is not None and (units == addon.unit_count or (by_cap and units == by_cap[1])):
                units = None
            # 処遇改善加算は率（%）。標準の率と同じなら空（月で標準の率が変わっても付いていく）
            rate = None
            if codes.is_treatment(addon.name):
                units = None
                rate = to_rate(request.POST.get(f'rate_{addon.pk}'))
                if rate is not None and rate == codes.treatment_rate(addon.name, datetime.date.today()):
                    rate = None
            FacilityAddonSetting.objects.update_or_create(
                facility=facility,
                addon=addon,
                defaults={'is_enabled': str(addon.pk) in enabled_ids, 'code': code, 'unit_count': units, 'rate': rate},
            )
        messages.success(request, '加算設定を保存しました。')
        return redirect('facilities:settings')


# 改定や整理で名前を変えた加算（古い名前 → 新しい名前）。「標準の加算を読み込む」で付け替える


class AddonLoadDefaultsView(LoginRequiredMixin, View):
    """標準の加算マスタを DB に合わせ込む（追加・名前の付け替え・単位数の更新）。全事業所共通のマスタなので開発向けユーザーのみ"""

    def post(self, request):
        if not request.user.can_switch_facility:
            messages.error(request, '加算マスタは全事業所で共通のため、開発向けユーザーだけが投入できます。')
            return redirect('facilities:settings')
        added, renamed, updated, retired = load_default_addons()
        parts = []
        if added:
            parts.append(f'{added} 件追加')
        if renamed:
            parts.append(f'{renamed} 件の名前を付け替え')
        if updated:
            parts.append(f'{updated} 件の単位数・説明を標準に更新')
        if retired:
            parts.append(f'{retired} 件を無効化')
        if parts:
            messages.success(request, '加算マスタを標準に合わせました：' + '、'.join(parts) + '。')
        else:
            messages.info(request, 'すでにすべての標準加算マスタが登録されています。')
        return redirect('facilities:settings')


# =============================================
# 同時編集：版の確認と「編集中」の通知
# =============================================
class EditingView(LoginRequiredMixin, View):
    """
    GET  ?kind=&id=            → その対象の現在の版と、編集中の他の職員
    POST action=touch|release  → 自分が編集中であることを知らせる／やめる
    """

    def _target(self, request, data):
        obj = concurrency.resolve(data.get('kind'), data.get('id'), request.user.facility)
        if obj is None:
            raise Http404
        return data.get('kind'), obj

    def get(self, request):
        kind, obj = self._target(request, request.GET)
        return JsonResponse({
            'version': concurrency.version_token(obj), 'updated_at': concurrency.saved_at_label(obj),
            'others': concurrency.others_editing(request.user.facility, kind, obj.pk, request.user),
        })

    def post(self, request):
        kind, obj = self._target(request, request.POST)
        if request.POST.get('action') == 'release':
            concurrency.release(kind, obj.pk, request.user)
            return JsonResponse({'ok': True})
        concurrency.touch(request.user.facility, kind, obj.pk, request.user)
        return JsonResponse({'ok': True, 'others': concurrency.others_editing(request.user.facility, kind, obj.pk, request.user)})

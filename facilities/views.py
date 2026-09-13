import datetime

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import OuterRef, Subquery
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import TemplateView

from beneficiaries.models import Beneficiary, RecipientCertificate
from records.models import ActivityTag
from schedules.models import ScheduledVisit
from .forms import ActivityTagForm, FacilityForm, SupportContentTagForm
from .models import AddonMaster, FacilityAddonSetting, SupportContentTag


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

        ctx.update({
            'today':           today,
            'soon':            soon,
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

        # 体制加算の一覧と、この施設のON/OFF状態を合わせて取得
        addons = AddonMaster.objects.filter(is_active=True, addon_type='facility')
        settings_map = {
            s.addon_id: s.is_enabled
            for s in FacilityAddonSetting.objects.filter(facility=facility)
        }
        addon_list = [
            {'addon': addon, 'is_enabled': settings_map.get(addon.pk, False)}
            for addon in addons
        ]

        from ai_assist.models import ReferenceDocument
        ctx['reference_documents'] = ReferenceDocument.objects.filter(facility=facility)
        ctx['is_admin'] = self.request.user.is_admin or self.request.user.is_superuser
        ctx['ai_enabled'] = bool(settings.ANTHROPIC_API_KEY)
        ctx.update({
            'facility':      facility,
            'facility_form': FacilityForm(instance=facility),
            'activity_tags': ActivityTag.objects.filter(facility=facility),
            'support_tags':  SupportContentTag.objects.filter(facility=facility),
            'activity_form': ActivityTagForm(),
            'support_form':  SupportContentTagForm(),
            'addon_list':    addon_list,
            'no_addons':     not addons.exists(),
        })
        return ctx


class FacilityUpdateView(LoginRequiredMixin, View):
    """施設基本情報を更新する"""

    def post(self, request):
        facility = request.user.facility
        form = FacilityForm(request.POST, request.FILES, instance=facility)
        if form.is_valid():
            form.save()
            messages.success(request, '施設情報を更新しました。')
        else:
            messages.error(request, '入力内容に誤りがあります。')
        return redirect('facilities:settings')


# =============================================
# 活動タグ 追加・編集・削除
# =============================================
class ActivityTagCreateView(LoginRequiredMixin, View):
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


class ActivityTagUpdateView(LoginRequiredMixin, View):
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


class ActivityTagDeleteView(LoginRequiredMixin, View):
    """活動タグを削除する"""

    def post(self, request, pk):
        tag = get_object_or_404(ActivityTag, pk=pk, facility=request.user.facility)
        name = tag.name
        tag.delete()
        messages.success(request, f'活動タグ「{name}」を削除しました。')
        return redirect('facilities:settings')


class ActivityTagLoadDefaultsView(LoginRequiredMixin, View):
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
class SupportTagCreateView(LoginRequiredMixin, View):
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


class SupportTagUpdateView(LoginRequiredMixin, View):
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


class SupportTagDeleteView(LoginRequiredMixin, View):
    """支援内容タグを削除する"""

    def post(self, request, pk):
        tag = get_object_or_404(SupportContentTag, pk=pk, facility=request.user.facility)
        name = tag.name
        tag.delete()
        messages.success(request, f'支援タグ「{name}」を削除しました。')
        return redirect('facilities:settings')


class SupportTagLoadDefaultsView(LoginRequiredMixin, View):
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

# 標準加算マスタの初期データ（法令ベース）
DEFAULT_ADDONS = [
    # 個別加算
    {'name': '送迎加算（往・迎え）',               'addon_type': 'individual', 'unit_count': 54,  'description': '利用者を自宅等から事業所まで迎えに行った場合（片道）。'},
    {'name': '送迎加算（復・送り）',               'addon_type': 'individual', 'unit_count': 54,  'description': '利用者を事業所から自宅等まで送り届けた場合（片道）。'},
    {'name': '延長支援加算（30分以上1時間未満）',  'addon_type': 'individual', 'unit_count': 61,  'description': '通常の開所時間を超えて支援した場合（30分以上1時間未満）。'},
    {'name': '延長支援加算（1時間以上2時間未満）', 'addon_type': 'individual', 'unit_count': 92,  'description': '通常の開所時間を超えて支援した場合（1時間以上2時間未満）。'},
    {'name': '延長支援加算（2時間以上）',          'addon_type': 'individual', 'unit_count': 123, 'description': '通常の開所時間を超えて支援した場合（2時間以上）。'},
    {'name': '欠席時対応加算',                     'addon_type': 'individual', 'unit_count': 94,  'description': '利用者が欠席した際に連絡・相談対応を行った場合。月4回まで。'},
    {'name': '家族支援加算（居宅訪問・1時間以上）', 'addon_type': 'individual', 'unit_count': 300, 'description': '家族の居宅を訪問して1時間以上の支援を行った場合。月4回まで。'},
    {'name': '家族支援加算（居宅訪問・1時間未満）', 'addon_type': 'individual', 'unit_count': 200, 'description': '家族の居宅を訪問して1時間未満の支援を行った場合。月4回まで。'},
    {'name': '家族支援加算（事業所で対面）',        'addon_type': 'individual', 'unit_count': 100, 'description': '事業所で家族と対面して支援を行った場合。月4回まで。'},
    {'name': '家族支援加算（オンライン）',          'addon_type': 'individual', 'unit_count': 100, 'description': 'オンラインで家族支援を行った場合。月4回まで。'},
    {'name': '専門的支援実施加算（個別実施分）',    'addon_type': 'individual', 'unit_count': 150, 'description': '理学療法士・作業療法士等の専門職が個別に支援を実施した場合。月2回まで。'},
    {'name': '入浴支援加算',                       'addon_type': 'individual', 'unit_count': 70,  'description': '入浴の支援を行った場合。月8回まで。'},
    {'name': '子育てサポート加算',                 'addon_type': 'individual', 'unit_count': 80,  'description': '保護者への子育て支援を行った場合。月4回まで。'},
    {'name': '通所自立支援加算',                   'addon_type': 'individual', 'unit_count': 60,  'description': '自立した通所に向けた支援を行った場合。90日以内の算定。'},
    {'name': '医療連携体制加算',                   'addon_type': 'individual', 'unit_count': 0,   'description': '看護職員等が医療的ケアを要する利用者に支援を行った場合。区分・単位数は報酬告示を要確認。'},
    {'name': '個別サポート加算Ⅰ',                 'addon_type': 'individual', 'unit_count': 0,   'description': 'ケアニーズが高い・著しく重度の児童・要保護児童・不登校児童への手厚い個別支援。単位数は要確認。'},
    {'name': '個別サポート加算Ⅱ',                 'addon_type': 'individual', 'unit_count': 0,   'description': '個別サポート加算Ⅰとは異なる区分。単位数・算定要件は要確認。'},
    {'name': '個別サポート加算Ⅲ',                 'addon_type': 'individual', 'unit_count': 0,   'description': '個別サポート加算Ⅱとは異なる区分。単位数・算定要件は要確認。'},
    # 体制加算
    {'name': '児童指導員等加配加算',               'addon_type': 'facility', 'unit_count': 0,   'description': '指定基準を上回る児童指導員等を配置している場合。区分（Ⅰ〜Ⅲ等）により単位数が異なる。'},
    {'name': '専門的支援体制加算',                 'addon_type': 'facility', 'unit_count': 0,   'description': '理学療法士・作業療法士等の専門職員を配置・連携している場合。'},
    {'name': '福祉専門職員配置等加算Ⅰ',           'addon_type': 'facility', 'unit_count': 15,  'description': '社会福祉士等の有資格者を一定割合以上配置している場合。'},
    {'name': '福祉専門職員配置等加算Ⅱ',           'addon_type': 'facility', 'unit_count': 10,  'description': '社会福祉士等の有資格者を一定割合配置している場合（Ⅰより基準が低い）。'},
    {'name': '看護職員加配加算',                   'addon_type': 'facility', 'unit_count': 0,   'description': '看護職員を基準を超えて配置している場合。区分により単位数が異なる。'},
    {'name': '福祉・介護職員等処遇改善加算',       'addon_type': 'facility', 'unit_count': 0,   'description': '職員の処遇改善のための体制加算。区分（Ⅰ〜Ⅳ等）により単位数が異なる。'},
    {'name': '利用者負担上限額管理加算',           'addon_type': 'facility', 'unit_count': 150, 'description': '複数事業所を利用する利用者の負担上限額を管理する事業所に算定。月1回。'},
]


class AddonSettingView(LoginRequiredMixin, View):
    """施設が算定する体制加算をON/OFFで管理する"""

    def post(self, request):
        facility = request.user.facility
        addons = AddonMaster.objects.filter(is_active=True, addon_type='facility')
        enabled_ids = set(request.POST.getlist('addon_ids'))
        for addon in addons:
            FacilityAddonSetting.objects.update_or_create(
                facility=facility,
                addon=addon,
                defaults={'is_enabled': str(addon.pk) in enabled_ids},
            )
        messages.success(request, '加算設定を保存しました。')
        return redirect('facilities:settings')


class AddonLoadDefaultsView(LoginRequiredMixin, View):
    """標準の加算マスタを一括投入する（同名のものは追加しない）"""

    def post(self, request):
        added = 0
        for item in DEFAULT_ADDONS:
            if not AddonMaster.objects.filter(name=item['name']).exists():
                AddonMaster.objects.create(**item)
                added += 1
        if added:
            messages.success(request, f'加算マスタを {added} 件追加しました。')
        else:
            messages.info(request, 'すでにすべての標準加算マスタが登録されています。')
        return redirect('facilities:settings')

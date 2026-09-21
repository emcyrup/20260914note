import datetime

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

        # 加算の一覧（個別・体制）と、この施設のON/OFF・コード・単位数
        addons = AddonMaster.objects.filter(is_active=True)
        addon_list = facility_addon_rows(facility)
        addon_individual = [r for r in addon_list if r.addon.addon_type == 'individual']
        addon_facility = [r for r in addon_list if r.addon.addon_type == 'facility']

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
            'addon_groups':  [('個別加算（利用者×日）', addon_individual), ('体制加算（施設全体・月単位）', addon_facility)],
            'unit_price':    facility.unit_price,
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
        facility.use_line = 'use_line' in request.POST
        facility.use_reservation = 'use_reservation' in request.POST
        facility.use_therapy_record = 'use_therapy_record' in request.POST
        valid = [k for k, _ in Facility.JOURNAL_SECTIONS]
        keys = [k for k in request.POST.getlist('journal_sections') if k in valid]
        if not keys:
            messages.error(request, '日誌の項目は1つ以上選んでください。')
            return redirect('facilities:settings')
        facility.journal_sections = keys
        # 帳票様式は開発向けユーザーだけが変えられる（他の事業所の様式名を管理者に見せない）
        if request.user.can_switch_facility:
            form_set = request.POST.get('form_set', facility.form_set)
            if form_set in dict(Facility.FORM_SET_CHOICES):
                facility.form_set = form_set
            layout = request.POST.get('layout', facility.layout)
            if layout in dict(Facility.LAYOUT_CHOICES):
                facility.layout = layout
        facility.save(update_fields=['use_billing', 'use_line', 'use_reservation', 'use_therapy_record',
                                     'journal_sections', 'form_set', 'layout', 'updated_at'])
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

# 標準加算マスタの初期データ（法令ベース）
DEFAULT_ADDONS = [
    # 単位数は「令和６年度障害福祉サービス等報酬改定（障害児支援関係）改定事項の概要」（こども家庭庁・令和6年4月1日）と
    # 「障害福祉サービス費等の報酬算定構造」にもとづく（放課後等デイサービス・児童発達支援事業所）。
    # 定員や区分で幅のあるもの（体制加算）は 0 にして説明に幅を書き、事業所ごとに加算設定で単位数を入れる。
    # サービスコードは事業所で確認して入力する（推測で入れない）。
    # ---- 個別加算（利用者×日／回）----
    {'name': '送迎加算（往・迎え）',               'addon_type': 'individual', 'unit_count': 54,  'description': '利用者を自宅等から事業所まで迎えに行った場合（片道）。主として重症心身障害児を支援する事業所は、この加算ではなく重症心身障害児・医療的ケア児の送迎加算（40／80）を使う。'},
    {'name': '送迎加算（復・送り）',               'addon_type': 'individual', 'unit_count': 54,  'description': '利用者を事業所から自宅等まで送り届けた場合（片道）。'},
    {'name': '送迎加算（重症心身障害児・片道の上乗せ）',            'addon_type': 'individual', 'unit_count': 40,  'description': '重症心身障害児を職員が付き添って送迎した場合、片道につき 54 に加えて算定。主として重症心身障害児を支援する事業所では片道 40 のみ。'},
    {'name': '送迎加算（医療的ケア児 スコア16点以上・片道の上乗せ）', 'addon_type': 'individual', 'unit_count': 80,  'description': '医療的ケアスコア16点以上の医療的ケア児を、医療的ケアが可能な職員が付き添って送迎した場合、片道につき 54 に加えて算定（重症心身障害児の事業所では片道 80 のみ）。'},
    {'name': '送迎加算（医療的ケア児 その他・片道の上乗せ）',        'addon_type': 'individual', 'unit_count': 40,  'description': '上記以外の医療的ケア児を、医療的ケアが可能な職員が付き添って送迎した場合、片道につき 54 に加えて算定（重症心身障害児の事業所では片道 40 のみ）。'},
    {'name': '延長支援加算（30分以上1時間未満）',  'addon_type': 'individual', 'unit_count': 61,  'description': '基本報酬の最長の時間区分（放デイは平日3時間・学校休業日5時間）を超えて、預かりニーズに対応した支援を計画的に行った場合。この区分は、利用者の都合等で延長時間が計画より短くなったときだけ算定できる。職員2名以上（うち1名は人員基準上の職員）。'},
    {'name': '延長支援加算（1時間以上2時間未満）', 'addon_type': 'individual', 'unit_count': 92,  'description': '延長時間が1時間以上2時間未満。'},
    {'name': '延長支援加算（2時間以上）',          'addon_type': 'individual', 'unit_count': 123, 'description': '延長時間が2時間以上。'},
    {'name': '延長支援加算（重症児・医療的ケア児 30分以上1時間未満）', 'addon_type': 'individual', 'unit_count': 128, 'description': '重症心身障害児・医療的ケア児の延長支援。30分以上1時間未満（計画より短くなったときのみ）。'},
    {'name': '延長支援加算（重症児・医療的ケア児 1時間以上2時間未満）', 'addon_type': 'individual', 'unit_count': 192, 'description': '重症心身障害児・医療的ケア児の延長支援。1時間以上2時間未満。'},
    {'name': '延長支援加算（重症児・医療的ケア児 2時間以上）',        'addon_type': 'individual', 'unit_count': 256, 'description': '重症心身障害児・医療的ケア児の延長支援。2時間以上。'},
    {'name': '欠席時対応加算',                     'addon_type': 'individual', 'unit_count': 94,  'description': '利用者が急病等で欠席した際に連絡・相談援助を行った場合。月4回まで（重症心身障害児を支援する場合で定員充足率80％未満のときは月8回まで）。'},
    {'name': '初期加算',                           'addon_type': 'individual', 'unit_count': 30,  'description': '利用開始から30日以内の期間に算定。'},
    {'name': '家族支援加算Ⅰ（居宅訪問・1時間以上）', 'addon_type': 'individual', 'unit_count': 300, 'description': '家族（きょうだいを含む）の居宅を訪問して、個別に1時間以上の相談援助等を行った場合。Ⅰは月4回まで。'},
    {'name': '家族支援加算Ⅰ（居宅訪問・1時間未満）', 'addon_type': 'individual', 'unit_count': 200, 'description': '家族の居宅を訪問して、個別に1時間未満の相談援助等を行った場合。Ⅰは月4回まで。'},
    {'name': '家族支援加算Ⅰ（事業所等で対面）',      'addon_type': 'individual', 'unit_count': 100, 'description': '事業所等で家族と対面して、個別に相談援助等を行った場合。Ⅰは月4回まで。'},
    {'name': '家族支援加算Ⅰ（オンライン）',          'addon_type': 'individual', 'unit_count': 80,  'description': 'オンラインで家族に個別の相談援助等を行った場合。Ⅰは月4回まで。'},
    {'name': '家族支援加算Ⅱ（グループ・事業所等で対面）', 'addon_type': 'individual', 'unit_count': 80,  'description': '複数の家族に対するグループでの相談援助等を事業所等で対面で行った場合。Ⅱは月4回まで。実施記録と参加者名簿を残す。'},
    {'name': '家族支援加算Ⅱ（グループ・オンライン）',   'addon_type': 'individual', 'unit_count': 60,  'description': '複数の家族に対するグループでの相談援助等をオンラインで行った場合。Ⅱは月4回まで。実施記録と参加者名簿を残す。'},
    {'name': '子育てサポート加算',                 'addon_type': 'individual', 'unit_count': 80,  'description': '保護者に支援場面の観察や参加の機会を提供したうえで、こどもの特性や関わり方について相談援助等を行った場合。月4回まで。'},
    {'name': '専門的支援実施加算',                 'addon_type': 'individual', 'unit_count': 150, 'description': '理学療法士等の専門職が、個別・集中的な専門的支援を計画的に行った場合（1回につき）。放デイは月2回〜最大月6回まで（利用日数等による）。専門的支援体制加算と併算定可。日誌に担当者・開始／終了時刻を記録する。'},
    {'name': '関係機関連携加算Ⅰ（計画作成時の会議）',   'addon_type': 'individual', 'unit_count': 250, 'description': '保育所や学校等との個別支援計画に関する会議を開催し、連携して個別支援計画を作成等した場合。月1回まで。'},
    {'name': '関係機関連携加算Ⅱ（保育所・学校等との情報連携）', 'addon_type': 'individual', 'unit_count': 200, 'description': '保育所や学校等との会議等により情報連携を行った場合。月1回まで。'},
    {'name': '関係機関連携加算Ⅲ（児童相談所・医療機関等との情報連携）', 'addon_type': 'individual', 'unit_count': 150, 'description': '児童相談所、医療機関等との会議等により情報連携を行った場合。月1回まで。'},
    {'name': '関係機関連携加算Ⅳ（就学先・就職先との連絡調整）',   'addon_type': 'individual', 'unit_count': 200, 'description': '就学先の小学校や就職先の企業等との連絡調整を行った場合。1回限り。'},
    {'name': '事業所間連携加算Ⅰ（中核となる事業所）',   'addon_type': 'individual', 'unit_count': 500, 'description': 'セルフプランで複数事業所を併用する児について、コーディネートの中核となる事業所として会議を開催する等により事業所間の情報連携を行い、家族への助言援助や自治体との情報連携等を行った場合。月1回まで。'},
    {'name': '事業所間連携加算Ⅱ（会議に参画）',        'addon_type': 'individual', 'unit_count': 150, 'description': 'セルフプランで複数事業所を併用する児について、Ⅰの会議に参画する等により事業所間の情報連携を行い、事業所内で共有して支援に反映させた場合。月1回まで。'},
    {'name': '保育・教育等移行支援加算（退所前の取組）',   'addon_type': 'individual', 'unit_count': 500, 'description': '退所前に、移行先への助言援助や関係機関等との移行に向けた協議等を行った場合。2回まで。'},
    {'name': '保育・教育等移行支援加算（退所後の居宅訪問）', 'addon_type': 'individual', 'unit_count': 500, 'description': '退所後に居宅等を訪問して相談援助を行った場合。1回まで。'},
    {'name': '保育・教育等移行支援加算（退所後の保育所等訪問）', 'addon_type': 'individual', 'unit_count': 500, 'description': '退所後に保育所等を訪問して助言・援助を行った場合。1回まで。'},
    {'name': '通所自立支援加算',                   'addon_type': 'individual', 'unit_count': 60,  'description': '放デイ。学校・居宅等と事業所の間の移動について、自立して通所できるよう職員が付き添って計画的に支援した場合（1回につき）。算定開始から3か月まで。'},
    {'name': '自立サポート加算',                   'addon_type': 'individual', 'unit_count': 100, 'description': '放デイ。高校2・3年生について、卒業後の生活に向けて学校や企業等と連携しながら相談援助や体験等の支援を計画的に行った場合（1回につき）。月2回まで。'},
    {'name': '入浴支援加算',                       'addon_type': 'individual', 'unit_count': 70,  'description': '医療的ケア児または重症心身障害児に、発達支援とあわせて入浴支援を行った場合。放デイ 70、児童発達支援 55。月8回まで。'},
    {'name': '個別サポート加算Ⅰ（ケアニーズの高い児）',   'addon_type': 'individual', 'unit_count': 90,  'description': '放デイ。ケアニーズの高い障害児に支援を行った場合（主として重症心身障害児が利用する事業所の基本報酬を算定している場合を除く）。児童発達支援は著しく重度の児に 120。'},
    {'name': '個別サポート加算Ⅰ（基礎研修修了者を配置・著しく重度）', 'addon_type': 'individual', 'unit_count': 120, 'description': '放デイ。ケアニーズの高い障害児に強度行動障害支援者養成研修（基礎研修）修了者を配置して支援した場合、または著しく重度の障害児に支援を行った場合。強度行動障害児支援加算との併算定不可。'},
    {'name': '個別サポート加算Ⅱ（要保護・要支援児童）',   'addon_type': 'individual', 'unit_count': 150, 'description': '要保護児童・要支援児童に対し、児童相談所やこども家庭センター等と連携（支援の状況等を6か月に1回以上共有）して支援を行った場合。'},
    {'name': '個別サポート加算Ⅲ（不登校）',            'addon_type': 'individual', 'unit_count': 70,  'description': '放デイ。不登校の状態にある障害児に対して、学校との連携のもと、家族への相談援助等を含めて支援を行った場合。'},
    {'name': '強度行動障害児支援加算Ⅰ（児基準20点以上）', 'addon_type': 'individual', 'unit_count': 200, 'description': '強度行動障害支援者養成研修（実践研修）修了者を配置し、児基準20点以上の児に支援計画シートを作成して支援を行った場合。'},
    {'name': '強度行動障害児支援加算Ⅱ（児基準30点以上）', 'addon_type': 'individual', 'unit_count': 250, 'description': '中核的人材養成研修修了者を配置し、児基準30点以上の児に支援計画シートを作成して支援を行った場合。'},
    {'name': '強度行動障害児支援加算（開始から90日以内の上乗せ）', 'addon_type': 'individual', 'unit_count': 500, 'description': '強度行動障害児支援加算Ⅰ・Ⅱの算定開始から90日以内の期間に、さらに加算。'},
    {'name': '集中的支援加算',                     'addon_type': 'individual', 'unit_count': 1000, 'description': '強度行動障害を有する児の状態が悪化した場合に、広域的支援人材が訪問して集中的な支援を行った場合。3か月以内・月4回まで。'},
    {'name': '人工内耳装用児支援加算Ⅱ',           'addon_type': 'individual', 'unit_count': 150, 'description': '児童発達支援。眼科・耳鼻咽喉科の医療機関との連携のもと言語聴覚士を配置し、人工内耳を装用している児に専門的な支援を計画的に行った場合。Ⅰ（445〜603）は聴力検査室のある児童発達支援センターのみ。'},
    {'name': '視覚・聴覚・言語機能障害児支援加算', 'addon_type': 'individual', 'unit_count': 100, 'description': '視覚・聴覚・言語機能に重度の障害のある児に対して、意思疎通に専門性のある人材を配置して支援を行った場合。'},
    {'name': '共生型サービス医療的ケア児支援加算', 'addon_type': 'individual', 'unit_count': 400, 'description': '共生型サービス事業所が看護職員等を配置し、地域貢献活動を行っているものとして届け出たうえで、医療的ケア児に支援を行った場合。医療連携体制加算Ⅰ〜Ⅶとの併算定不可。'},
    {'name': '食事提供加算Ⅰ（栄養士の助言）',     'addon_type': 'individual', 'unit_count': 30,  'description': '児童発達支援センター。低所得・中間所得世帯の児に、栄養士の助言・指導のもとで栄養面等に配慮した食事を提供した場合。'},
    {'name': '食事提供加算Ⅱ（管理栄養士の助言）', 'addon_type': 'individual', 'unit_count': 40,  'description': '児童発達支援センター。管理栄養士等の助言・指導のもとで食事を提供した場合。'},
    # 医療連携体制加算（Ⅰ〜Ⅵは改定前と同じ、Ⅶは令和6年度に 100→250）
    {'name': '医療連携体制加算Ⅰ（看護1時間未満）',       'addon_type': 'individual', 'unit_count': 32,   'description': '医療的ケアを必要としない利用者への看護で、提供時間が1時間未満。'},
    {'name': '医療連携体制加算Ⅱ（看護1〜2時間未満）',     'addon_type': 'individual', 'unit_count': 63,   'description': '医療的ケアを必要としない利用者への看護で、提供時間が1時間以上2時間未満。'},
    {'name': '医療連携体制加算Ⅲ（看護2時間以上）',        'addon_type': 'individual', 'unit_count': 125,  'description': '医療的ケアを必要としない利用者への看護で、提供時間が2時間以上。'},
    {'name': '医療連携体制加算Ⅳ（医療的ケア4時間未満・1人）',      'addon_type': 'individual', 'unit_count': 800,  'description': '医療的ケアを必要とする利用者への看護（4時間未満）。利用者が1人のとき。'},
    {'name': '医療連携体制加算Ⅳ（医療的ケア4時間未満・2人）',      'addon_type': 'individual', 'unit_count': 500,  'description': '医療的ケアを必要とする利用者への看護（4時間未満）。利用者が2人のとき。'},
    {'name': '医療連携体制加算Ⅳ（医療的ケア4時間未満・3〜8人）',   'addon_type': 'individual', 'unit_count': 400,  'description': '医療的ケアを必要とする利用者への看護（4時間未満）。利用者が3人以上8人以下のとき。'},
    {'name': '医療連携体制加算Ⅴ（医療的ケア4時間以上・1人）',      'addon_type': 'individual', 'unit_count': 1600, 'description': '医療的ケアを必要とする利用者への看護（4時間以上）。利用者が1人のとき。'},
    {'name': '医療連携体制加算Ⅴ（医療的ケア4時間以上・2人）',      'addon_type': 'individual', 'unit_count': 960,  'description': '医療的ケアを必要とする利用者への看護（4時間以上）。利用者が2人のとき。'},
    {'name': '医療連携体制加算Ⅴ（医療的ケア4時間以上・3〜8人）',   'addon_type': 'individual', 'unit_count': 800,  'description': '医療的ケアを必要とする利用者への看護（4時間以上）。利用者が3人以上8人以下のとき。'},
    {'name': '医療連携体制加算Ⅵ（喀痰吸引等の指導）',      'addon_type': 'individual', 'unit_count': 500,  'description': '看護職員が介護職員等に喀痰吸引等の指導を行った場合。'},
    {'name': '医療連携体制加算Ⅶ（喀痰吸引等の実施）',      'addon_type': 'individual', 'unit_count': 250,  'description': '喀痰吸引等が必要な障害児に対して、認定特定行為業務従事者が医療機関等との連携により喀痰吸引等を行った場合（医療的ケア区分による基本報酬を算定している場合は算定しない）。令和6年度に 100→250。'},
    # ---- 体制加算（施設全体・日／月）。定員や区分で幅があるものは 0 にして、事業所ごとに加算設定で入れる ----
    {'name': '児童指導員等加配加算',               'addon_type': 'facility', 'unit_count': 0,   'description': '基準の人員に加えて児童指導員等またはその他の従業者を配置。児童発達支援事業所・放デイは定員区分ごとに、常勤専従・経験5年以上 75〜187、常勤専従・経験5年未満 59〜152、常勤換算・経験5年以上 49〜123、常勤換算・経験5年未満 43〜107、その他の従業者 36〜90（1日につき）。'},
    {'name': '専門的支援体制加算',                 'addon_type': 'facility', 'unit_count': 0,   'description': '基準の人員に加えて理学療法士等を配置している場合。児童発達支援事業所・放デイは定員区分に応じて 49〜123（1日につき）。'},
    {'name': '福祉専門職員配置等加算Ⅰ',           'addon_type': 'facility', 'unit_count': 15,  'description': '社会福祉士等の有資格者を一定割合以上配置している場合（1日につき）。'},
    {'name': '福祉専門職員配置等加算Ⅱ',           'addon_type': 'facility', 'unit_count': 10,  'description': '社会福祉士等の有資格者を一定割合配置している場合（Ⅰより基準が低い）。'},
    {'name': '福祉専門職員配置等加算Ⅲ',           'addon_type': 'facility', 'unit_count': 6,   'description': '常勤の従業者が一定割合以上、または勤続年数3年以上の常勤の従業者が一定割合以上の場合。'},
    {'name': '看護職員加配加算',                   'addon_type': 'facility', 'unit_count': 0,   'description': '看護職員を基準を超えて配置している場合（主として重症心身障害児を支援する事業所）。定員と区分により単位数が異なる。'},
    {'name': '中核機能強化事業所加算',             'addon_type': 'facility', 'unit_count': 0,   'description': '市町村が地域の中核拠点として位置付ける児童発達支援事業所・放デイで、専門人材を配置して関係機関との連携体制を確保しながら専門的・包括的な支援に取り組んだ場合。定員区分に応じて 75〜187（重症心身障害児の事業所は 125〜374）（1日につき）。'},
    {'name': '自立支援担当職員配置加算',           'addon_type': 'facility', 'unit_count': 0,   'description': '放デイ。進路相談・関係機関連携を担う職員を配置している場合。単位数は要確認。'},
    {'name': '福祉・介護職員等処遇改善加算',       'addon_type': 'facility', 'unit_count': 0,   'description': '職員の処遇改善のための体制加算。所定単位数に区分ごとの率を掛ける（単位数ではなく率）。'},
    {'name': '利用者負担上限額管理加算',           'addon_type': 'facility', 'unit_count': 150, 'description': '複数事業所を利用する利用者の負担上限額を管理する事業所に算定。月1回。'},
]


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
            if code == addon.code:
                code = ''
            if units is not None and units == addon.unit_count:
                units = None
            FacilityAddonSetting.objects.update_or_create(
                facility=facility,
                addon=addon,
                defaults={'is_enabled': str(addon.pk) in enabled_ids, 'code': code, 'unit_count': units},
            )
        messages.success(request, '加算設定を保存しました。')
        return redirect('facilities:settings')


# 改定や整理で名前を変えた加算（古い名前 → 新しい名前）。「標準の加算を読み込む」で付け替える
ADDON_RENAMES = {
    '専門的支援実施加算（個別実施分）': '専門的支援実施加算',
    '関係機関連携加算Ⅰ（計画作成時の会議等）': '関係機関連携加算Ⅰ（計画作成時の会議）',
    '関係機関連携加算Ⅱ（情報連携）': '関係機関連携加算Ⅱ（保育所・学校等との情報連携）',
    '関係機関連携加算Ⅲ（就学・就職時）': '関係機関連携加算Ⅳ（就学先・就職先との連絡調整）',
    '関係機関連携加算Ⅳ（医療機関等）': '関係機関連携加算Ⅲ（児童相談所・医療機関等との情報連携）',
    '家族支援加算（居宅訪問・1時間以上）': '家族支援加算Ⅰ（居宅訪問・1時間以上）',
    '家族支援加算（居宅訪問・1時間未満）': '家族支援加算Ⅰ（居宅訪問・1時間未満）',
    '家族支援加算（事業所で対面）': '家族支援加算Ⅰ（事業所等で対面）',
    '家族支援加算（オンライン）': '家族支援加算Ⅰ（オンライン）',
    '家族支援加算Ⅱ（グループ・事業所で対面）': '家族支援加算Ⅱ（グループ・事業所等で対面）',
    '個別サポート加算Ⅰ': '個別サポート加算Ⅰ（ケアニーズの高い児）',
    '個別サポート加算Ⅱ': '個別サポート加算Ⅱ（要保護・要支援児童）',
    '個別サポート加算Ⅲ': '個別サポート加算Ⅲ（不登校）',
    '強度行動障害児支援加算': '強度行動障害児支援加算Ⅰ（児基準20点以上）',
    '人工内耳装用児支援加算': '人工内耳装用児支援加算Ⅱ',
    '保育・教育等移行支援加算': '保育・教育等移行支援加算（退所前の取組）',
    '食事提供加算': '食事提供加算Ⅰ（栄養士の助言）',
    '送迎加算（重症心身障害児・往）': '送迎加算（重症心身障害児・片道の上乗せ）',
    '送迎加算（医療的ケア児等の個別送迎）': '送迎加算（医療的ケア児 その他・片道の上乗せ）',
    '中核機能強化加算': '中核機能強化事業所加算',
}
# 令和6年度改定で無くなった名前（同じ内容の加算が標準にあるものは上の付け替えで対応。ここは残っていても無効にするだけ）
ADDON_RETIRED = ['医療連携体制加算', '送迎加算（重症心身障害児・復）']


def load_default_addons():
    """
    標準の加算マスタを DB に合わせ込む。戻り値は (追加, 付け替え, 更新, 無効化) の件数。
    - 名前が無いものは追加する
    - 名前を変えた加算は付け替える（請求に使われている行はそのまま名前と単位数だけ変わる）
    - 標準にある名前で単位数・説明が違うものは標準の値にする（事業所ごとの上書きは FacilityAddonSetting にあるので影響しない）
    - 令和6年度改定で無くなった名前は無効にする
    """
    defaults = {item['name']: item for item in DEFAULT_ADDONS}
    renamed = 0
    for old, new in ADDON_RENAMES.items():
        row = AddonMaster.objects.filter(name=old).first()
        if row is None or AddonMaster.objects.filter(name=new).exists():
            continue
        item = defaults[new]
        row.name, row.unit_count, row.description = new, item['unit_count'], item['description']
        row.addon_type = item['addon_type']
        row.save(update_fields=['name', 'unit_count', 'description', 'addon_type'])
        renamed += 1
    added = updated = 0
    for item in DEFAULT_ADDONS:
        row = AddonMaster.objects.filter(name=item['name']).first()
        if row is None:
            AddonMaster.objects.create(**item)
            added += 1
        elif (row.unit_count, row.description, row.addon_type) != (item['unit_count'], item['description'], item['addon_type']):
            row.unit_count, row.description, row.addon_type = item['unit_count'], item['description'], item['addon_type']
            row.save(update_fields=['unit_count', 'description', 'addon_type'])
            updated += 1
    retired = AddonMaster.objects.filter(name__in=ADDON_RETIRED, is_active=True).update(is_active=False)
    return added, renamed, updated, retired


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

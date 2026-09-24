from decimal import Decimal

from django.db import models
from .uploads import logo_upload_to


# 地域区分別・1単位あたり単価（円）
# 障害福祉サービス等報酬告示に基づく（令和6年度改定時点）
REGION_UNIT_PRICE = {
    '1':     Decimal('11.40'),
    '2':     Decimal('11.12'),
    '3':     Decimal('11.05'),
    '4':     Decimal('10.90'),
    '5':     Decimal('10.70'),
    '6':     Decimal('10.42'),
    '7':     Decimal('10.21'),
    'other': Decimal('10.00'),
}


class Facility(models.Model):
    """
    施設基本情報。全テーブルの親となるテナント単位。
    """
    # 地域区分（障害福祉サービスの単位単価に影響する）
    REGION_CATEGORY_CHOICES = [
        ('1', '1級地（東京23区等）'), ('2', '2級地'), ('3', '3級地'),
        ('4', '4級地'), ('5', '5級地'), ('6', '6級地'), ('7', '7級地'),
        ('other', 'その他'),
    ]

    name = models.CharField(max_length=100, verbose_name='施設名')
    office_number = models.CharField(max_length=20, blank=True, verbose_name='事業所番号')
    postal_code = models.CharField(max_length=8, blank=True, verbose_name='郵便番号')
    address = models.CharField(max_length=200, blank=True, verbose_name='住所')
    address2 = models.CharField(max_length=200, blank=True, verbose_name='住所2（建物名など）')
    phone = models.CharField(max_length=20, blank=True, verbose_name='電話番号')
    # 運営会社（計画書中心の画面で「施設アカウント情報」に出す）
    company_name = models.CharField(max_length=100, blank=True, verbose_name='会社名')
    representative_name = models.CharField(max_length=100, blank=True, verbose_name='代表者名')
    representative_email = models.EmailField(blank=True, verbose_name='代表者メールアドレス')
    # 画面の型：標準（全機能）／計画書中心（利用者・完了期日・スタッフ・保護者・連絡帳・施設の6メニュー）
    LAYOUT_STANDARD = 'standard'
    LAYOUT_PLANBOOK = 'planbook'
    LAYOUT_CHOICES = [(LAYOUT_STANDARD, '標準（記録・予定・請求まで全部）'),
                      (LAYOUT_PLANBOOK, '計画書中心（シンプル：利用者・完了期日一覧・スタッフ・保護者・連絡帳・施設）')]
    layout = models.CharField(max_length=20, choices=LAYOUT_CHOICES, default=LAYOUT_STANDARD, verbose_name='画面の型')
    # 地域区分（請求単価計算に使用）
    region_category = models.CharField(
        max_length=10, choices=REGION_CATEGORY_CHOICES, default='other', verbose_name='地域区分'
    )
    # 通常終了時刻（延長支援加算の自動判定に使用）
    standard_close_time = models.TimeField(
        null=True, blank=True, verbose_name='通常終了時刻',
        help_text='例：17:00。延長支援加算の自動チェックに使います。'
    )
    # 基本報酬単位数（請求書PDF生成に使用）
    base_unit_count = models.IntegerField(
        null=True, blank=True, verbose_name='1日あたり基本報酬単位数'
    )
    # 重症心身障害児（重身）の基本報酬単位数（普通用と単位が異なる）
    base_unit_count_severe = models.IntegerField(
        null=True, blank=True, verbose_name='重身の1日あたり基本報酬単位数',
        help_text='重症心身障害児の利用者に使う単位数。空なら通常の単位数を使います。'
    )
    is_new_facility_r8 = models.BooleanField(
        default=False, verbose_name='令和8年6月以降新規指定事業所',
        help_text='令和8年6月1日以降に新規指定された事業所の場合はチェック'
    )
    # 呼び方の置き換え（メニューと見出しに反映）
    term_staff = models.CharField(max_length=20, default='職員', verbose_name='職員の呼び方',
                                  help_text='例：支援員／先生／職員')
    term_beneficiary = models.CharField(max_length=20, default='利用者', verbose_name='利用者の呼び方',
                                        help_text='例：利用児／園児／ご利用者様')
    # ロゴと配色
    logo = models.ImageField(upload_to=logo_upload_to, blank=True, null=True, verbose_name='ロゴ画像')
    brand_color = models.CharField(max_length=7, blank=True, verbose_name='コーポレートカラー',
                                   help_text='#4e7d89 のような16進数。空なら標準色')
    line_channel_access_token = models.TextField(blank=True, verbose_name='LINEチャネルアクセストークン')
    line_channel_secret = models.CharField(max_length=100, blank=True, verbose_name='LINEチャネルシークレット')
    # 使う機能（事業所によっては請求・LINE を使わない）
    use_billing = models.BooleanField(default=True, verbose_name='請求機能を使う')
    use_schedule = models.BooleanField(default=True, verbose_name='予定（来所予定・出欠）を使う',
                                       help_text='月のカレンダーに来所予定を入れて出欠を付ける画面。予約管理で枠を扱う事業所は使わなくてよい。')
    use_line = models.BooleanField(default=True, verbose_name='LINE連携を使う')
    use_reservation = models.BooleanField(default=False, verbose_name='予約管理を使う',
                                          help_text='1日の枠・キャンセル待ち・公式LINEからの申し込みを扱います。')
    use_therapy_record = models.BooleanField(default=False, verbose_name='療育記録を使う',
                                             help_text='利用者ごとの留意点と、1回ごとの療育の記録（やったこと①〜⑤・担当・本文）。用紙と同じ形で印刷できます。')
    # ログイン画面の「職員として新しく登録」で使うコード（空なら受け付けない）。登録した人は管理者が承認するまでログインできない
    staff_signup_code = models.CharField(max_length=12, blank=True, db_index=True, verbose_name='職員登録コード')
    # オンにすると、ログイン画面の「職員として新しく登録」をコードなしで受け付ける（承認は管理者が行う）
    staff_signup_open = models.BooleanField(default=False, verbose_name='職員登録コードなしで申し込みを受け付ける')
    # 日誌で AI が作る項目と、その順番（空なら標準の順番で全部）
    journal_sections = models.JSONField(default=list, blank=True, verbose_name='日誌の項目と順番')
    # 事業所固有の帳票様式（標準以外を選ぶと「事業所様式」メニューが出る）
    FORM_SET_STANDARD = 'standard'
    FORM_SET_HAPPINESS = 'happiness'
    FORM_SET_CHOICES = [(FORM_SET_STANDARD, '標準'), (FORM_SET_HAPPINESS, 'はぴねす様式（関係機関連携報告書・個別支援計画書 別紙1／詳細版・専門的支援実施計画書）')]
    form_set = models.CharField(max_length=20, choices=FORM_SET_CHOICES, default=FORM_SET_STANDARD, verbose_name='帳票様式')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    JOURNAL_SECTIONS = [
        ('activity',       '活動・めあて・観点・考察'),
        ('observation',    '観察・活動内容'),
        ('support',        '支援内容'),
        ('reaction',       '本人の反応'),
        ('parent_message', '保護者向けメッセージ'),
    ]
    JOURNAL_SECTION_LABELS = dict(JOURNAL_SECTIONS)

    class Meta:
        verbose_name = '施設'
        verbose_name_plural = '施設'

    def __str__(self):
        return self.name

    @property
    def is_planbook(self):
        return self.layout == self.LAYOUT_PLANBOOK

    @property
    def unit_price(self):
        """地域区分に応じた 1単位あたりの単価（円）"""
        return REGION_UNIT_PRICE.get(self.region_category, Decimal('10.00'))

    def base_units_for(self, beneficiary):
        """利用者に応じた基本報酬単位数（重身なら重身用。未設定なら普通用）"""
        if getattr(beneficiary, 'is_severe', False) and self.base_unit_count_severe:
            return self.base_unit_count_severe
        return self.base_unit_count

    def journal_section_keys(self):
        """日誌に出す項目のキーを優先順位の順で返す（設定が空なら標準の順番で全部）"""
        valid = [k for k, _ in self.JOURNAL_SECTIONS]
        keys = [k for k in (self.journal_sections or []) if k in valid]
        seen = []
        for k in keys:
            if k not in seen:
                seen.append(k)
        return seen or valid

    def journal_section_rows(self):
        """設定画面用：使う項目を順番どおりに、使わない項目をその後ろに"""
        enabled = self.journal_section_keys() if self.journal_sections else [k for k, _ in self.JOURNAL_SECTIONS]
        rows = [{'key': k, 'label': self.JOURNAL_SECTION_LABELS[k], 'enabled': True} for k in enabled]
        rows += [{'key': k, 'label': l, 'enabled': False} for k, l in self.JOURNAL_SECTIONS if k not in enabled]
        return rows

    def journal_text_keys(self):
        """AI 一括生成の対象（文章の項目だけ）"""
        return [k for k in self.journal_section_keys() if k != 'activity']


class SupportContentTag(models.Model):
    """
    支援内容タグマスタ。
    送迎・入浴支援など、日次記録で選択する支援種別（加算集計にも使用）。
    施設ごとに追加・編集可能。
    """
    facility = models.ForeignKey(
        Facility, on_delete=models.CASCADE,
        related_name='support_content_tags', verbose_name='施設'
    )
    name = models.CharField(max_length=50, verbose_name='タグ名')
    order = models.PositiveIntegerField(default=0, verbose_name='表示順')
    is_active = models.BooleanField(default=True, verbose_name='有効')
    # 実費請求する支援（教材費など）は単価を入れると請求書に自動集計される
    price = models.PositiveIntegerField(default=0, verbose_name='実費単価（円/回）',
                                        help_text='0 のときは実費請求しない')

    class Meta:
        verbose_name = '支援内容タグ'
        verbose_name_plural = '支援内容タグ'
        ordering = ['order', 'name']

    def __str__(self):
        return self.name


class AddonMaster(models.Model):
    """
    加算マスタ（全国共通・法令ベース）。
    放課後等デイサービスで算定できる加算の一覧。
    開発側が法令に基づいて初期投入し、施設側は選択するだけ。
    """
    ADDON_TYPE_CHOICES = [
        ('individual', '個別加算（利用者×日単位）'),
        ('facility',   '体制加算（施設全体・月単位）'),
    ]

    name       = models.CharField(max_length=200, verbose_name='加算名')
    # 国保連請求のサービスコード（例：615xxx）。事業所側で確認して入力する
    code       = models.CharField(max_length=10, blank=True, verbose_name='サービスコード')
    addon_type = models.CharField(
        max_length=20, choices=ADDON_TYPE_CHOICES, default='individual', verbose_name='加算種別'
    )
    unit_count  = models.IntegerField(default=0, verbose_name='単位数')
    description = models.TextField(blank=True, verbose_name='算定要件の概要')
    is_active   = models.BooleanField(default=True, verbose_name='有効')

    class Meta:
        verbose_name        = '加算マスタ'
        verbose_name_plural = '加算マスタ'
        ordering            = ['addon_type', 'name']

    def __str__(self):
        return f'{self.name}（{self.unit_count}単位）'

    @property
    def is_collaboration(self):
        """他事業所・関係機関との連携に関する加算か（日誌で連携内容の記入欄を出す）"""
        return '連携' in self.name

    @property
    def is_special_support(self):
        """専門的支援の実施に関する加算か（日誌で担当者・開始／終了時刻の欄を出す）"""
        return '専門的支援' in self.name


class FacilityAddonSetting(models.Model):
    """
    施設が算定する加算のON/OFF管理。
    AddonMasterの全加算から、各施設が実際に算定するものを選択して登録する。
    """
    facility   = models.ForeignKey(Facility, on_delete=models.CASCADE, verbose_name='施設')
    addon      = models.ForeignKey(AddonMaster, on_delete=models.CASCADE, verbose_name='加算')
    is_enabled = models.BooleanField(default=False, verbose_name='算定する')
    # 事業所ごとの上書き（区分や地域で単位数・コードが変わる加算のため）。空ならマスタの値
    code       = models.CharField(max_length=10, blank=True, verbose_name='サービスコード（事業所）')
    unit_count = models.IntegerField(null=True, blank=True, verbose_name='単位数（事業所）')

    class Meta:
        verbose_name        = '施設加算設定'
        verbose_name_plural = '施設加算設定'
        unique_together     = [['facility', 'addon']]

    def __str__(self):
        return f'{self.facility.name} - {self.addon.name}'


class AddonRow:
    """
    設定画面・日誌・請求で使う「この事業所での加算」の見え方。
    マスタの値に事業所の上書き（コード・単位数）を重ね、円換算も持つ。
    """

    def __init__(self, facility, addon, setting=None):
        self.facility = facility
        self.addon = addon
        self.setting = setting
        self.is_enabled = bool(setting and setting.is_enabled)
        self.code = (setting.code if setting and setting.code else addon.code) or ''
        self.unit_count = setting.unit_count if setting and setting.unit_count is not None else addon.unit_count
        self.override_code = setting.code if setting else ''
        self.override_units = setting.unit_count if setting else None

    @property
    def pk(self):
        return self.addon.pk

    @property
    def name(self):
        return self.addon.name

    @property
    def price_yen(self):
        """単位数 × 地域区分の単価（円・切り捨て）"""
        if not self.unit_count:
            return 0
        return int(Decimal(self.unit_count) * self.facility.unit_price)

    @property
    def is_collaboration(self):
        return self.addon.is_collaboration

    @property
    def is_special_support(self):
        return self.addon.is_special_support


def facility_addon_rows(facility, addon_type=None, enabled_only=False):
    """
    事業所の加算一覧（マスタ＋事業所の上書き）。
    enabled_only=True のときは「算定する」にした加算だけ。ただし、その種別で1件も設定が
    ないときは全件を返す（初めて使う事業所でも日誌に加算が出るように）。
    """
    addons = AddonMaster.objects.filter(is_active=True)
    if addon_type:
        addons = addons.filter(addon_type=addon_type)
    settings_map = {s.addon_id: s for s in FacilityAddonSetting.objects.filter(facility=facility)}
    rows = [AddonRow(facility, a, settings_map.get(a.pk)) for a in addons]
    if enabled_only:
        enabled = [r for r in rows if r.is_enabled]
        if enabled or any(r.setting is not None for r in rows):
            return enabled
    return rows


class EditingSession(models.Model):
    """いま編集フォームを開いている職員（同じ画面を開いた他の職員に「編集中」と知らせる）"""
    facility   = models.ForeignKey(Facility, on_delete=models.CASCADE)
    kind       = models.CharField(max_length=30)
    target_id  = models.PositiveIntegerField()
    user       = models.ForeignKey('accounts.StaffAccount', on_delete=models.CASCADE)
    started_at = models.DateTimeField(auto_now_add=True)
    touched_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('kind', 'target_id', 'user')]
        indexes = [models.Index(fields=['kind', 'target_id', 'touched_at'])]

    def __str__(self):
        return f'{self.user} が {self.kind}:{self.target_id} を編集中'

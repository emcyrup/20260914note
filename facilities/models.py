from django.db import models
from .uploads import logo_upload_to


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
    address = models.CharField(max_length=200, blank=True, verbose_name='住所')
    phone = models.CharField(max_length=20, blank=True, verbose_name='電話番号')
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
    use_line = models.BooleanField(default=True, verbose_name='LINE連携を使う')
    # 日誌で AI が作る項目と、その順番（空なら標準の順番で全部）
    journal_sections = models.JSONField(default=list, blank=True, verbose_name='日誌の項目と順番')
    # 事業所固有の帳票様式（標準以外を選ぶと「事業所様式」メニューが出る）
    FORM_SET_STANDARD = 'standard'
    FORM_SET_HAPPINESS = 'happiness'
    FORM_SET_CHOICES = [(FORM_SET_STANDARD, '標準'), (FORM_SET_HAPPINESS, 'はぴねす様式（関係機関連携報告書・個別支援計画書 別紙1／詳細版・専門的支援実施計画書）')]
    form_set = models.CharField(max_length=20, choices=FORM_SET_CHOICES, default=FORM_SET_STANDARD, verbose_name='帳票様式')
    created_at = models.DateTimeField(auto_now_add=True)

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


class FacilityAddonSetting(models.Model):
    """
    施設が算定する加算のON/OFF管理。
    AddonMasterの全加算から、各施設が実際に算定するものを選択して登録する。
    """
    facility   = models.ForeignKey(Facility, on_delete=models.CASCADE, verbose_name='施設')
    addon      = models.ForeignKey(AddonMaster, on_delete=models.CASCADE, verbose_name='加算')
    is_enabled = models.BooleanField(default=False, verbose_name='算定する')

    class Meta:
        verbose_name        = '施設加算設定'
        verbose_name_plural = '施設加算設定'
        unique_together     = [['facility', 'addon']]

    def __str__(self):
        return f'{self.facility.name} - {self.addon.name}'

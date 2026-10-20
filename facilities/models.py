from decimal import Decimal

from django.db import models
from .uploads import logo_upload_to


# 地域区分別・1単位あたり単価（円）
# 地域区分ごとの 1 単位の単価（令和6年度改定。障害児通所支援：児童発達支援・放課後等デイサービス）
# 上乗せ割合（1級地 20%・2級地 16%・3級地 15%・4級地 12%・5級地 10%・6級地 6%・7級地 3%）× 人件費割合 60%
REGION_UNIT_PRICE = {
    '1':     Decimal('11.20'),
    '2':     Decimal('10.96'),
    '3':     Decimal('10.90'),
    '4':     Decimal('10.72'),
    '5':     Decimal('10.60'),
    '6':     Decimal('10.36'),
    '7':     Decimal('10.18'),
    'other': Decimal('10.00'),
}
# 主として重症心身障害児を通わせる事業所（人件費割合 76%）
REGION_UNIT_PRICE_SEVERE = {
    '1':     Decimal('11.52'),
    '2':     Decimal('11.22'),
    '3':     Decimal('11.14'),
    '4':     Decimal('10.91'),
    '5':     Decimal('10.76'),
    '6':     Decimal('10.46'),
    '7':     Decimal('10.23'),
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
    LAYOUT_RYOIKU = 'ryoiku'
    LAYOUT_CHOICES = [(LAYOUT_STANDARD, '標準（記録・予定・請求まで全部）'),
                      (LAYOUT_PLANBOOK, '計画書中心（シンプル：利用者・完了期日一覧・スタッフ・保護者・連絡帳・施設）'),
                      (LAYOUT_RYOIKU, '療育（ゆあーず：基本機能＝ホーム・利用者情報・予約・療育記録・議事録・LINE連携／お試し＝記録・支援計画）')]
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
    # 基本報酬（billing/codes.py）：定員区分を選ぶと、時間区分（区分1〜3）ごとの単位数は表から決まる。
    # 空なら下の「1日あたり基本報酬単位数」を使う（主として重症心身障害児の事業所・医療的ケア児の区分など、表に無いとき）
    CAPACITY_CHOICES = [('', '（単位数を手で入れる）'), ('10', '定員10人以下'), ('20', '定員11〜20人'), ('21', '定員21人以上')]
    capacity_class = models.CharField(max_length=2, blank=True, choices=CAPACITY_CHOICES, verbose_name='定員区分（基本報酬）')
    time_kubun_weekday = models.PositiveSmallIntegerField(default=2, verbose_name='平日の時間区分の初期値')
    time_kubun_holiday = models.PositiveSmallIntegerField(default=3, verbose_name='学校休業日の時間区分の初期値')
    # 基本報酬の国保連サービスコード（事業所が使っているコード表の値。区分ごと）
    base_code_1 = models.CharField(max_length=10, blank=True, verbose_name='基本報酬のサービスコード（区分1）')
    base_code_2 = models.CharField(max_length=10, blank=True, verbose_name='基本報酬のサービスコード（区分2）')
    base_code_3 = models.CharField(max_length=10, blank=True, verbose_name='基本報酬のサービスコード（区分3）')
    # 主として重症心身障害児を通わせる事業所（地域区分の単価が違う）
    is_severe_facility = models.BooleanField(default=False, verbose_name='主として重症心身障害児を通わせる事業所')
    # 基本報酬単位数（定員区分が空のときの 1 日の単位数。請求書PDF生成に使用）
    base_unit_count = models.IntegerField(
        null=True, blank=True, verbose_name='1日あたり基本報酬単位数'
    )
    # 重症心身障害児（重身）の基本報酬単位数（普通用と単位が異なる）
    base_unit_count_severe = models.IntegerField(
        null=True, blank=True, verbose_name='重身の1日あたり基本報酬単位数（授業終了後）',
        help_text='重症心身障害児の利用者に使う単位数（授業終了後）。空なら通常の単位数を使います。'
    )
    # 重心型の基本報酬は時間区分が無く「授業終了後／学校休業日」の 2 つ（2026-10-16 はびりす）。学校休業日が空なら授業終了後と同じ
    base_unit_count_severe_holiday = models.IntegerField(null=True, blank=True, verbose_name='重身の1日あたり基本報酬単位数（学校休業日）')
    base_code_severe = models.CharField(max_length=10, blank=True, verbose_name='重身の基本報酬サービスコード（授業終了後）')
    base_code_severe_holiday = models.CharField(max_length=10, blank=True, verbose_name='重身の基本報酬サービスコード（学校休業日）')
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
    # 不正アクセスの通知（accounts/security.py。2026-10-20）：ロック・初めての場所からのログイン・深夜のログイン・管理者の操作を知らせる
    security_notify = models.BooleanField(default=True, verbose_name='不正アクセスの通知を送る',
                                          help_text='予約の増減を知らせる LINE グループと、管理者・児発管のメール（または下の宛先）へ')
    security_notify_email = models.EmailField(blank=True, verbose_name='通知のメール宛先',
                                              help_text='空なら管理者・児発管のメールと代表者メール')
    line_channel_access_token = models.TextField(blank=True, verbose_name='LINEチャネルアクセストークン')
    line_channel_secret = models.CharField(max_length=100, blank=True, verbose_name='LINEチャネルシークレット')
    # 使う機能（事業所によっては請求・LINE を使わない）
    use_billing = models.BooleanField(default=True, verbose_name='請求機能を使う')
    use_schedule = models.BooleanField(default=True, verbose_name='予定（来所予定・出欠）を使う',
                                       help_text='月のカレンダーに来所予定を入れて出欠を付ける画面。予約管理で枠を扱う事業所は使わなくてよい。')
    use_line = models.BooleanField(default=True, verbose_name='LINE連携を使う')
    # 日誌を「確定」で保存したとき、保護者向けメッセージを保護者の LINE へ自動で送る（利用者ごとに送らない設定ができる）
    line_auto_send = models.BooleanField(default=False, verbose_name='日誌を確定したら保護者へ LINE を自動で送る')
    # 日誌を確定するのに要る項目（records/quality.py の RULES のキー）と、観察・支援・反応の文の最低字数（0 は見ない）
    journal_required = models.JSONField(default=list, blank=True, verbose_name='日誌を確定するのに要る項目')
    journal_min_chars = models.PositiveSmallIntegerField(default=0, verbose_name='観察・支援・反応の最低字数')
    use_reservation = models.BooleanField(default=False, verbose_name='予約管理を使う',
                                          help_text='1日の枠・キャンセル待ち・公式LINEからの申し込みを扱います。')
    use_therapy_record = models.BooleanField(default=False, verbose_name='療育記録を使う',
                                             help_text='利用者ごとの留意点と、1回ごとの療育の記録（やったこと①〜⑤・担当・本文）。用紙と同じ形で印刷できます。')
    # 療育記録の音声入力を、留意点の欄ではなく「これまでの記録」の今日の記録へ直接入れる（整文してやったこと・事実・様子に分ける）。
    # 留意点の欄・AI のボタン（詳しくまとめる・短く要約・整文だけ）・見取り図は画面に出さない（2026-10-13 ゆあーず）
    therapy_voice_direct = models.BooleanField(default=False, verbose_name='療育記録：音声入力を今日の記録へ直接入れる')
    # 書類を AI で読み取ったあとの「療育記録の AI で参考にする」を「療育記録に反映する」と呼び、読み取った直後は印なしにする（2026-10-13 ゆあーず）
    knowledge_reflect = models.BooleanField(default=False, verbose_name='書類の読み取り：「療育記録に反映する」は初めは印なし')
    # 利用者情報の画面の追加（2026-10-13 ゆあーず）：前後の利用者へ送るボタン、資料の編集中に別の画面へ移るときの警告、
    # 「書類から分かっていること」の削除ボタン
    beneficiary_tools = models.BooleanField(default=False, verbose_name='利用者情報：前後の利用者へ送る・編集中の警告・分かったことの削除')
    # 予定づくりの追加（2026-10-13 ゆあーず）：共演NG の印をまとめて付ける、月間予定表の上に編集中の日時、
    # 月間予定表のドラッグで共演NG の人と同じ枠に入れられる（確かめてから。赤く表示）
    schedule_tools = models.BooleanField(default=False, verbose_name='予定づくり：共演NG の一括の印・編集中の日時・ドラッグで共演NG を赤く・名前で探す・月の予定のやり直し')
    # 療育記録の画面で、保存のときの言葉づかいのチェック（data-wording）をしない（ゆあーずの依頼 2026-10-13）
    therapy_no_wording = models.BooleanField(default=False, verbose_name='療育記録：保存のときの言葉づかいの注意を出さない')
    # 個別支援計画の AI（日誌からの下書き・欄ごとの整文）を出さず、台帳と療育記録の内容をそのまま欄に入れる（ゆあーずの依頼 2026-10-13）
    plan_no_ai = models.BooleanField(default=False, verbose_name='個別支援計画：AI の下書き・整文を出さず、療育記録とアセスメントを欄に入れる')
    use_transport = models.BooleanField(default=False, verbose_name='送迎・配車を使う',
                                        help_text='車両と運転手を登録し、利用者ごとの送迎（迎え・送り・場所）から日ごとの配車表を作ります。')
    use_dev_assessment = models.BooleanField(default=False, verbose_name='5領域アセスメントを使う',
                                             help_text='健康・生活／運動・感覚／認知・行動／言語・コミュニケーション／人間関係・社会性の5領域を5段階で評価する用紙。利用者情報から作り、A4で印刷できます。')
    use_survey = models.BooleanField(default=False, verbose_name='アンケート・自己評価を使う',
                                     help_text='保護者評価アンケート（ログインなしの回答ページ・LINE や QR で配る・集計）と事業所の自己評価、公表用の PDF。ガイドラインで年 1 回の実施と公表が求められているもの。')
    use_daily_ops = models.BooleanField(default=False, verbose_name='毎日の運営を使う',
                                        help_text='クラス（小集団）の週間の活動計画と当日の記録（各児の療育記録に写す）、健康・生活の記録（体温・食事・排せつ・午睡・服薬と引き渡しカード）、発達検査の結果の推移。')
    # ログイン画面の「職員として新しく登録」で使うコード（空なら受け付けない）。登録した人は管理者が承認するまでログインできない
    staff_signup_code = models.CharField(max_length=12, blank=True, db_index=True, verbose_name='職員登録コード')
    # 「お試し」の機能（記録の AI・支援計画の AI）を使える回数。0 なら制限なし。使った回数は trial_ai_used
    trial_ai_limit = models.PositiveSmallIntegerField(default=0, verbose_name='お試しの AI の回数の上限（0＝制限なし）')
    trial_ai_used = models.PositiveIntegerField(default=0, verbose_name='お試しの AI を使った回数')
    # 音声入力（サーバーの Whisper）に渡す「よく使う言葉」。職員や利用者の名前・活動名は自動で足すので、それ以外の専門用語など
    speech_words = models.TextField(blank=True, verbose_name='音声入力でよく使う言葉')
    # 言葉づかいの辞書（ai_assist/wording.py）。1 行に「言葉=言いかえ」。行頭「-」で標準の辞書から外す
    word_rules = models.TextField(blank=True, verbose_name='言葉づかいの辞書（保存前に知らせる言葉）')
    # オンにすると、ログイン画面の「職員として新しく登録」をコードなしで受け付ける（承認は管理者が行う）
    staff_signup_open = models.BooleanField(default=False, verbose_name='職員登録コードなしで申し込みを受け付ける')
    # 日誌で AI が作る項目と、その順番（空なら標準の順番で全部）
    journal_sections = models.JSONField(default=list, blank=True, verbose_name='日誌の項目と順番')
    # 事業所固有の帳票様式（標準以外を選ぶと「事業所様式」メニューが出る）
    FORM_SET_STANDARD = 'standard'
    FORM_SET_HAPPINESS = 'happiness'
    FORM_SET_CHOICES = [(FORM_SET_STANDARD, '標準'), (FORM_SET_HAPPINESS, 'はびりす様式（関係機関連携報告書・個別支援計画書 別紙1／詳細版・専門的支援実施計画書）')]
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
    def is_ryoiku(self):
        return self.layout == self.LAYOUT_RYOIKU

    @property
    def unit_price(self):
        """地域区分に応じた 1 単位あたりの単価（円）。主として重症心身障害児の事業所は別の表"""
        table = REGION_UNIT_PRICE_SEVERE if self.is_severe_facility else REGION_UNIT_PRICE
        return table.get(self.region_category, Decimal('10.00'))

    def severe_units_apply(self, beneficiary):
        """この利用者に重心型の基本報酬（授業終了後／学校休業日）を使うか"""
        return bool(getattr(beneficiary, 'is_severe', False) and self.base_unit_count_severe)

    def base_code(self, kubun, beneficiary=None):
        """
        基本報酬のサービスコード（時間区分ごと）。施設設定で入れた値があればそれ、無ければ定員区分と利用者の医療的ケア区分から
        コード表（billing/codes.py）の値。どちらも無ければ空。重心の利用者は重心用のコード（授業終了後／学校休業日）
        """
        if kubun not in (1, 2, 3):
            return ''
        if self.severe_units_apply(beneficiary):
            from billing.codes import SEVERE_HOLIDAY, severe_kind
            return self.base_code_severe_holiday or self.base_code_severe if severe_kind(kubun) == SEVERE_HOLIDAY else self.base_code_severe
        manual = getattr(self, f'base_code_{kubun}', '')
        care = getattr(beneficiary, 'medical_care_level', 0) or 0
        if manual and not care:
            return manual
        from billing.codes import table_code
        return table_code(self.capacity_class, kubun, care) or manual

    def base_units_for(self, beneficiary, kubun=None):
        """
        その利用者の 1 日の基本報酬の単位数。
        重身の利用者で重身の単位数があればそれ。定員区分と時間区分があれば表（billing/codes.py。医療的ケア区分も見る）、
        無ければ手入力の単位数
        """
        if self.severe_units_apply(beneficiary):
            from billing.codes import SEVERE_HOLIDAY, severe_kind
            if kubun and severe_kind(kubun) == SEVERE_HOLIDAY and self.base_unit_count_severe_holiday:
                return self.base_unit_count_severe_holiday
            return self.base_unit_count_severe
        if self.capacity_class and kubun:
            from billing.codes import table_units
            units = table_units(self.capacity_class, kubun, getattr(beneficiary, 'medical_care_level', 0) or 0)
            if units is not None:
                return units
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
        # 定員区分で単位数が変わる体制加算は、事業所の上書きが無ければコード表（billing/codes.py）の値
        from billing.codes import capacity_addon
        by_cap = capacity_addon(addon.name, facility.capacity_class) if facility.capacity_class else None
        self.code = (setting.code if setting and setting.code else (by_cap[0] if by_cap else addon.code)) or ''
        self.unit_count = (setting.unit_count if setting and setting.unit_count is not None
                           else (by_cap[1] if by_cap else addon.unit_count))
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

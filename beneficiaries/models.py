import datetime
import secrets

from django.db import models
from django.utils import timezone
from facilities.models import Facility
from facilities.uploads import assessment_upload_to, certificate_upload_to, document_upload_to


LINE_CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'  # 見間違えやすい 0/O/1/I を除く
LINE_CODE_LENGTH = 8
LINE_CODE_TTL = datetime.timedelta(hours=72)


def _generate_line_code():
    """LINE連携用の登録コード（8文字の英数字・総当たりできない大きさ）を生成する"""
    return ''.join(secrets.choice(LINE_CODE_ALPHABET) for _ in range(LINE_CODE_LENGTH))


def _line_code_expiry():
    return timezone.now() + LINE_CODE_TTL


def to_hiragana(s):
    """カタカナ → ひらがな（長音・記号はそのまま）。前後の空白と全角スペースは取る"""
    return ''.join(chr(ord(ch) - 0x60) if 0x30A1 <= ord(ch) <= 0x30F6 else ch for ch in (s or '')).replace('\u3000', ' ').strip()


# 50 音の行（利用者一覧の絞り込み）。濁音・半濁音・小書きも同じ行に入れる
KANA_ROWS = [
    ('あ', 'あいうえおぁぃぅぇぉ'), ('か', 'かきくけこがぎぐげごゕゖ'), ('さ', 'さしすせそざじずぜぞ'),
    ('た', 'たちつてとだぢづでどっ'), ('な', 'なにぬねの'), ('は', 'はひふへほばびぶべぼぱぴぷぺぽ'),
    ('ま', 'まみむめも'), ('や', 'やゆよゃゅょ'), ('ら', 'らりるれろ'), ('わ', 'わをんゐゑゎ'),
]


class Beneficiary(models.Model):
    """
    利用者（子ども）の基本情報。
    全レコードに facility を持たせることでテナント分離を実現する。
    """

    GENDER_MALE = 'male'
    GENDER_FEMALE = 'female'
    GENDER_OTHER = 'other'
    GENDER_CHOICES = [
        (GENDER_MALE, '男'),
        (GENDER_FEMALE, '女'),
        (GENDER_OTHER, 'その他'),
    ]

    DISABILITY_CLASS_1 = '1'
    DISABILITY_CLASS_2 = '2'
    DISABILITY_CLASS_CHOICES = [
        (DISABILITY_CLASS_1, '1級'),
        (DISABILITY_CLASS_2, '2級'),
    ]

    STATUS_ACTIVE = 'active'
    STATUS_INACTIVE = 'inactive'
    STATUS_GRADUATED = 'graduated'
    STATUS_CHOICES = [
        (STATUS_ACTIVE, '在籍中'),
        (STATUS_INACTIVE, '退所'),
        (STATUS_GRADUATED, '卒業'),
    ]
    # 利用をやめた人（退所・卒業）。一覧では在籍中と分けて見る
    LEFT_STATUSES = (STATUS_INACTIVE, STATUS_GRADUATED)

    facility = models.ForeignKey(
        Facility,
        on_delete=models.PROTECT,
        related_name='beneficiaries',
        verbose_name='所属施設',
    )
    last_name = models.CharField(max_length=50, verbose_name='姓')
    first_name = models.CharField(max_length=50, verbose_name='名')
    last_name_kana = models.CharField(max_length=50, blank=True, verbose_name='姓（かな）')
    first_name_kana = models.CharField(max_length=50, blank=True, verbose_name='名（かな）')
    date_of_birth = models.DateField(verbose_name='生年月日')
    gender = models.CharField(
        max_length=10, choices=GENDER_CHOICES, default=GENDER_MALE, verbose_name='性別'
    )
    disability_class = models.CharField(
        max_length=1, choices=DISABILITY_CLASS_CHOICES, blank=True, verbose_name='障害区分'
    )
    disability_type = models.CharField(max_length=100, blank=True, verbose_name='障害種別')
    # 重症心身障害児（重身）。日誌の種類と基本報酬単位数の切り替えに使う
    is_severe = models.BooleanField(default=False, verbose_name='重症心身障害児（重身）')
    notes = models.TextField(blank=True, verbose_name='備考')
    # --- 住まいと学校（計画書中心の画面のプロフィール） ---
    GRADE_CHOICES = [
        ('', '選択'), ('pre', '未就学'),
        ('e1', '小1'), ('e2', '小2'), ('e3', '小3'), ('e4', '小4'), ('e5', '小5'), ('e6', '小6'),
        ('j1', '中1'), ('j2', '中2'), ('j3', '中3'), ('h1', '高1'), ('h2', '高2'), ('h3', '高3'), ('other', 'その他'),
    ]
    postal_code = models.CharField(max_length=8, blank=True, verbose_name='郵便番号')
    address = models.CharField(max_length=200, blank=True, verbose_name='住所')
    mobile_phone = models.CharField(max_length=20, blank=True, verbose_name='携帯電話番号')
    home_phone = models.CharField(max_length=20, blank=True, verbose_name='自宅電話番号')
    school_name = models.CharField(max_length=100, blank=True, verbose_name='通学学校名')
    grade = models.CharField(max_length=10, choices=GRADE_CHOICES, blank=True, verbose_name='学年')
    admission_date = models.DateField(null=True, blank=True, verbose_name='入所日')
    discharge_date = models.DateField(null=True, blank=True, verbose_name='退所日')
    # 他システムや紙の記録がある（新規の利用者ではない）
    has_prior_records = models.BooleanField(default=False, verbose_name='他システム・紙の記録がある')
    # 利用予定曜日（月〜土）。Phase 3 の予定一括生成で使用する
    weekday_mon = models.BooleanField(default=False, verbose_name='月')
    weekday_tue = models.BooleanField(default=False, verbose_name='火')
    weekday_wed = models.BooleanField(default=False, verbose_name='水')
    weekday_thu = models.BooleanField(default=False, verbose_name='木')
    weekday_fri = models.BooleanField(default=False, verbose_name='金')
    weekday_sat = models.BooleanField(default=False, verbose_name='土')
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default=STATUS_ACTIVE, verbose_name='在籍状況'
    )
    # 同じ日に一緒にできない利用者（相性など）。片方に入れればもう片方にも付く（対称）。
    # 予約を作る・動かす・月間予定表を組むときに、同じ日の同じ時間枠に入れない（reservations/services.py の pair_conflicts）
    cannot_pair = models.ManyToManyField('self', blank=True, symmetrical=True, verbose_name='同じ時間にできない利用者')
    # きょうだい。同じ時間枠に入った日は、予約ごとに「きょうだいと1枠にまとめる」（Reservation.share_seat）を選べる。
    # 予約の連絡先（Customer）が同じ子もきょうだいとして扱う（reservations/services.py の sibling_map）
    siblings = models.ManyToManyField('self', blank=True, symmetrical=True, verbose_name='きょうだい')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '利用者'
        verbose_name_plural = '利用者'
        ordering = ['last_name_kana', 'first_name_kana']

    def __str__(self):
        return f'{self.last_name} {self.first_name}'

    def save(self, *args, **kwargs):
        # ふりがなはひらがなにそろえる（カタカナ・全角スペース混じりでも 50 音順に並ぶように）
        self.last_name_kana = to_hiragana(self.last_name_kana)
        self.first_name_kana = to_hiragana(self.first_name_kana)
        super().save(*args, **kwargs)

    @property
    def full_name(self):
        return f'{self.last_name} {self.first_name}'

    @property
    def full_name_kana(self):
        return f'{self.last_name_kana} {self.first_name_kana}'

    def pair_names(self):
        """同じ時間にできない利用者の名前（在籍中だけ）"""
        return [b.full_name for b in self.cannot_pair.filter(status=self.STATUS_ACTIVE).order_by('last_name_kana', 'first_name_kana')]

    @property
    def latest_certificate(self):
        """最新の受給者証（prefetch してあればクエリを増やさない）"""
        return max(self.recipient_certificates.all(), key=lambda c: (c.valid_until, c.pk), default=None)

    @property
    def manager_office(self):
        """この利用者の上限管理事業所（登録があれば）"""
        return self.offices.filter(is_manager=True).first()

    @property
    def is_copayment_manager_here(self):
        """当施設がこの利用者の上限管理事業所か"""
        return self.offices.filter(is_manager=True, is_this_office=True).exists()

    @property
    def other_offices(self):
        """当施設以外の利用事業所"""
        return self.offices.filter(is_this_office=False)

    @property
    def scheduled_weekdays_display(self):
        """利用予定曜日を「月・水・金」のような表示用文字列で返す"""
        days = []
        if self.weekday_mon: days.append('月')
        if self.weekday_tue: days.append('火')
        if self.weekday_wed: days.append('水')
        if self.weekday_thu: days.append('木')
        if self.weekday_fri: days.append('金')
        if self.weekday_sat: days.append('土')
        return '・'.join(days) if days else '未設定'

    @property
    def scheduled_weekday_numbers(self):
        """利用予定曜日をPythonのweekday番号（月=0〜土=5）のリストで返す（Phase 3 で使用）"""
        days = []
        if self.weekday_mon: days.append(0)
        if self.weekday_tue: days.append(1)
        if self.weekday_wed: days.append(2)
        if self.weekday_thu: days.append(3)
        if self.weekday_fri: days.append(4)
        if self.weekday_sat: days.append(5)
        return days


class BeneficiaryOffice(models.Model):
    """
    利用者が利用している事業所（複数可）。
    上限額管理では、どの事業所が上限管理事業所かを決めておく必要があるためフラグを持つ。
    当施設が上限管理事業所のときは、他事業所へ「利用者負担上限額管理結果票」を送付できる。
    """
    beneficiary = models.ForeignKey(
        Beneficiary, on_delete=models.CASCADE, related_name='offices', verbose_name='利用者'
    )
    name = models.CharField(max_length=200, verbose_name='事業所名')
    office_number = models.CharField(max_length=20, blank=True, verbose_name='事業所番号')
    is_this_office = models.BooleanField(default=False, verbose_name='当施設')
    is_manager = models.BooleanField(default=False, verbose_name='上限管理事業所')
    address = models.CharField(max_length=200, blank=True, verbose_name='住所')
    phone = models.CharField(max_length=20, blank=True, verbose_name='電話番号')
    fax = models.CharField(max_length=20, blank=True, verbose_name='FAX')
    contact_name = models.CharField(max_length=100, blank=True, verbose_name='担当者名')
    note = models.CharField(max_length=200, blank=True, verbose_name='備考')
    order = models.PositiveSmallIntegerField(default=0, verbose_name='表示順')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '利用事業所'
        verbose_name_plural = '利用事業所'
        ordering = ['-is_this_office', 'order', 'pk']

    def __str__(self):
        return f'{self.beneficiary.full_name} - {self.name}'

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # 上限管理事業所は利用者につき1つ
        if self.is_manager:
            BeneficiaryOffice.objects.filter(beneficiary=self.beneficiary, is_manager=True).exclude(pk=self.pk).update(is_manager=False)


class Guardian(models.Model):
    """
    保護者情報。1人の利用者に複数の保護者を登録できる。
    LINE User ID は Webhook 経由で自動取得する（手入力も可）。
    """

    RELATION_FATHER = 'father'
    RELATION_MOTHER = 'mother'
    RELATION_OTHER = 'other'
    RELATION_CHOICES = [
        (RELATION_FATHER, '父'),
        (RELATION_MOTHER, '母'),
        (RELATION_OTHER, 'その他'),
    ]

    beneficiary = models.ForeignKey(
        Beneficiary,
        on_delete=models.CASCADE,
        related_name='guardians',
        verbose_name='利用者',
    )
    last_name = models.CharField(max_length=50, verbose_name='姓')
    first_name = models.CharField(max_length=50, verbose_name='名')
    relation = models.CharField(
        max_length=10, choices=RELATION_CHOICES, default=RELATION_MOTHER, verbose_name='続柄'
    )
    kana = models.CharField(max_length=100, blank=True, verbose_name='ふりがな')
    phone = models.CharField(max_length=20, blank=True, verbose_name='電話番号')
    phone2 = models.CharField(max_length=20, blank=True, verbose_name='電話番号2')
    email = models.EmailField(blank=True, verbose_name='メールアドレス')
    # 取り込みで受け取った、画面に欄の無い情報（連絡先の名称・請求先など）。{見出し: 値} の形で保持して詳細に出す
    extra = models.JSONField(default=dict, blank=True, verbose_name='取り込んだ情報')
    line_user_id = models.CharField(max_length=100, blank=True, verbose_name='LINE User ID')
    line_linked = models.BooleanField(default=False, verbose_name='LINE連携済み')
    # LINE連携用の登録コード（8文字の英数字・重複不可・有効期限つき）。保護者がLINEでこのコードを送ると自動連携する
    line_registration_code = models.CharField(
        max_length=10, default=_generate_line_code, unique=True, verbose_name='LINE登録コード'
    )
    line_code_expires_at = models.DateTimeField(default=_line_code_expiry, null=True, blank=True, verbose_name='登録コードの有効期限')
    is_primary = models.BooleanField(default=False, verbose_name='主連絡先')
    memo = models.CharField(max_length=200, blank=True, verbose_name='メモ')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '保護者'
        verbose_name_plural = '保護者'

    @property
    def full_name(self):
        return f'{self.last_name} {self.first_name}'.strip()

    @property
    def extra_items(self):
        """取り込んだ情報を (見出し, 値) の順で。値が無いものは出さない"""
        return [(k, v) for k, v in (self.extra or {}).items() if v not in ('', None)]

    @property
    def line_code_valid(self):
        """未連携で、期限内のコードか"""
        return (not self.line_linked and bool(self.line_registration_code)
                and self.line_code_expires_at is not None and self.line_code_expires_at > timezone.now())

    def issue_line_code(self):
        """登録コードを発行し直す（古いコードは無効になる）"""
        self.line_registration_code = _generate_line_code()
        self.line_code_expires_at = _line_code_expiry()

    def mark_line_linked(self, line_user_id):
        """連携完了。使ったコードは二度と使えないよう置き換え、期限を消す"""
        self.line_user_id = line_user_id
        self.line_linked = True
        self.line_registration_code = _generate_line_code()
        self.line_code_expires_at = None

    def __str__(self):
        return f'{self.last_name} {self.first_name}（{self.get_relation_display()}）'

    def save(self, *args, **kwargs):
        # 他の保護者と登録コードが重複している場合は一意なコードになるまで再生成する
        # unique=True の制約と合わせて二重で重複を防ぐ
        while Guardian.objects.filter(
            line_registration_code=self.line_registration_code
        ).exclude(pk=self.pk).exists():
            self.line_registration_code = _generate_line_code()
        super().save(*args, **kwargs)


class RecipientCertificate(models.Model):
    """
    受給者証の情報。有効期限ごとに新しいレコードを作成する。
    OCR で写真から自動入力することを想定している。
    """

    beneficiary = models.ForeignKey(
        Beneficiary,
        on_delete=models.CASCADE,
        related_name='recipient_certificates',
        verbose_name='利用者',
    )
    # 受給者証番号
    certificate_number = models.CharField(max_length=20, blank=True, verbose_name='受給者証番号')
    # 支給量（放課後等デイサービスの月あたり利用可能日数）
    granted_days = models.PositiveSmallIntegerField(default=0, verbose_name='支給量（日/月）')
    # 負担上限月額
    monthly_cap = models.PositiveIntegerField(default=0, verbose_name='負担上限月額（円）')
    # 有効期間
    valid_from = models.DateField(verbose_name='有効期間（開始）')
    valid_until = models.DateField(verbose_name='有効期間（終了）')
    # 市区町村・相談支援事業所
    municipality = models.CharField(max_length=50, blank=True, verbose_name='市区町村')
    support_office = models.CharField(max_length=100, blank=True, verbose_name='相談支援事業所')
    # OCRで読み取った際の元画像
    scanned_image = models.ImageField(
        upload_to=certificate_upload_to, blank=True, verbose_name='スキャン画像'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '受給者証'
        verbose_name_plural = '受給者証'
        ordering = ['-valid_until']

    def __str__(self):
        return f'{self.beneficiary.full_name} — {self.valid_from}〜{self.valid_until}'


class BeneficiaryAssessment(models.Model):
    """
    利用者台帳に付けるアセスメント・面談の記録・発達検査や医療の情報など。
    文章だけでも、書類（PDF・写真）だけでも残せる。個別支援計画のアセスメント（support_plans）とは別に、
    計画を作る前の聞き取りや、ほかの機関から受け取った書類をためておく場所。
    """
    KIND_ASSESSMENT = 'assessment'
    KIND_INTERVIEW = 'interview'
    KIND_TEST = 'test'
    KIND_MEDICAL = 'medical'
    KIND_OTHER = 'other'
    KIND_CHOICES = [
        (KIND_ASSESSMENT, 'アセスメント'),
        (KIND_INTERVIEW, '面談・聞き取り'),
        (KIND_TEST, '発達検査・評価'),
        (KIND_MEDICAL, '医療・関係機関の情報'),
        (KIND_OTHER, 'その他'),
    ]

    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.CASCADE, related_name='assessments',
                                    verbose_name='利用者')
    date = models.DateField(verbose_name='日付')
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default=KIND_ASSESSMENT, verbose_name='種類')
    title = models.CharField(max_length=100, verbose_name='件名')
    content = models.TextField(blank=True, verbose_name='内容')
    file = models.FileField(upload_to=assessment_upload_to, blank=True, verbose_name='書類（PDF・写真）')
    file_name = models.CharField(max_length=200, blank=True, verbose_name='書類の元の名前')
    created_by = models.ForeignKey('accounts.StaffAccount', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+', verbose_name='登録者')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'アセスメント・資料'
        verbose_name_plural = 'アセスメント・資料'
        ordering = ['-date', '-pk']

    def __str__(self):
        return f'{self.beneficiary.full_name} {self.date} {self.title}'

    @property
    def is_image(self):
        return bool(self.file) and self.file.name.lower().rsplit('.', 1)[-1] in ('jpg', 'jpeg', 'png', 'webp', 'gif', 'heic')


DOCUMENT_EXTENSIONS = ('jpg', 'jpeg', 'png', 'webp', 'gif', 'heic', 'pdf', 'xlsx', 'xls', 'csv')
DOCUMENT_MAX_BYTES = 20 * 1024 * 1024


class BeneficiaryDocument(models.Model):
    """
    利用者の基本情報に付ける書類・画像（受給者証や契約書の写真、診断書の PDF、Excel・CSV など）。
    ドラッグ＆ドロップで何枚でも入れられる。中身は読み取らず、そのまま保管して開けるようにするだけ。
    """

    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.CASCADE, related_name='documents', verbose_name='利用者')
    file = models.FileField(upload_to=document_upload_to, verbose_name='ファイル')
    file_name = models.CharField(max_length=200, blank=True, verbose_name='元のファイル名')
    title = models.CharField(max_length=100, blank=True, verbose_name='件名')
    uploaded_by = models.ForeignKey('accounts.StaffAccount', on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='+', verbose_name='登録者')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = '利用者の書類'
        verbose_name_plural = '利用者の書類'
        ordering = ['-created_at', '-pk']

    def __str__(self):
        return f'{self.beneficiary.full_name} {self.label}'

    @property
    def ext(self):
        return (self.file_name or self.file.name or '').lower().rsplit('.', 1)[-1]

    @property
    def is_image(self):
        return self.ext in ('jpg', 'jpeg', 'png', 'webp', 'gif')

    @property
    def kind(self):
        """画面のアイコン用：image / pdf / sheet / other"""
        if self.is_image or self.ext == 'heic':
            return 'image'
        if self.ext == 'pdf':
            return 'pdf'
        if self.ext in ('xlsx', 'xls', 'csv'):
            return 'sheet'
        return 'other'

    @property
    def label(self):
        return self.title or self.file_name or self.file.name.rsplit('/', 1)[-1]


class BeneficiaryKnowledge(models.Model):
    """
    診断書・意見書・検査結果などの書類を AI で読み取った内容（利用者ごと）。

    職員が確認画面で直してから保存すると（status=saved）、要約・支援で気をつけること・文字起こしを
    小さく分けて（KnowledgeChunk）保存し、療育記録の AI（留意点の要約・記録の文）が、その子の分だけを
    検索して参考にする（RAG）。台帳（障害種別・重身・備考・留意点）への反映は、確認画面で印を付けたものだけ。
    """
    STATUS_DRAFT = 'draft'
    STATUS_SAVED = 'saved'
    STATUS_CHOICES = [(STATUS_DRAFT, '確認待ち'), (STATUS_SAVED, '保存済み')]

    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.CASCADE, related_name='knowledge', verbose_name='利用者')
    document = models.ForeignKey('BeneficiaryDocument', on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='knowledge', verbose_name='読み取った書類')
    assessment = models.ForeignKey('BeneficiaryAssessment', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='knowledge', verbose_name='読み取ったアセスメント・資料')
    kind = models.CharField(max_length=50, blank=True, verbose_name='書類の種類')
    title = models.CharField(max_length=100, blank=True, verbose_name='件名')
    doc_date = models.DateField(null=True, blank=True, verbose_name='書類の日付')
    issuer = models.CharField(max_length=100, blank=True, verbose_name='発行元')
    summary = models.TextField(blank=True, verbose_name='要約')
    points = models.TextField(blank=True, verbose_name='支援で気をつけること')
    text = models.TextField(blank=True, verbose_name='書類の文字')
    proposals = models.JSONField(default=dict, blank=True, verbose_name='台帳への反映案')
    applied = models.JSONField(default=list, blank=True, verbose_name='台帳に反映したもの')
    use_in_ai = models.BooleanField(default=True, verbose_name='療育記録の AI で参考にする')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_DRAFT, verbose_name='状態')
    created_by = models.ForeignKey('accounts.StaffAccount', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+', verbose_name='読み取った職員')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '書類から分かっていること'
        verbose_name_plural = '書類から分かっていること'
        ordering = ['-created_at', '-pk']

    def __str__(self):
        return f'{self.beneficiary.full_name} {self.label}'

    @property
    def label(self):
        return self.title or self.kind or '読み取った書類'

    @property
    def points_list(self):
        return [ln.strip().lstrip('・-*•').strip() for ln in (self.points or '').splitlines() if ln.strip().lstrip('・-*•').strip()]

    @property
    def is_saved(self):
        return self.status == self.STATUS_SAVED


class KnowledgeChunk(models.Model):
    """書類から分かっていることの検索用の小片（文字バイグラムの出現数。ai_assist.retrieval と同じ考え方）"""
    PART_SUMMARY = 'summary'
    PART_POINTS = 'points'
    PART_TEXT = 'text'

    knowledge = models.ForeignKey(BeneficiaryKnowledge, on_delete=models.CASCADE, related_name='chunks')
    part = models.CharField(max_length=10, default=PART_TEXT)
    index = models.PositiveIntegerField(default=0)
    text = models.TextField()
    terms = models.JSONField(default=dict)
    length = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['knowledge', 'index']


class DevelopmentAssessment(models.Model):
    """
    5領域のアセスメント（評価シート）。
    こども家庭庁のガイドラインにある 5 領域（健康・生活／運動・感覚／認知・行動／言語・コミュニケーション／人間関係・社会性）
    ごとに、いまの様子を 5 段階で評価し、様子と支援の方針を書く。本人・家族の希望とまとめを添えて A4 で印刷できる。
    支援計画の前のアセスメントや、半年ごとの見直しに使う（支援計画の AI アセスメント support_plans.Assessment とは別）。
    """
    DOMAINS = [
        ('health', '健康・生活'),
        ('motor', '運動・感覚'),
        ('cognition', '認知・行動'),
        ('language', '言語・コミュニケーション'),
        ('social', '人間関係・社会性'),
    ]
    DOMAIN_HINTS = {
        'health': '健康状態・生活リズム・食事・排せつ・着替え・身の回りのこと',
        'motor': '姿勢・粗大運動（走る・跳ぶ）・微細運動（はさみ・鉛筆）・感覚の過敏や鈍さ',
        'cognition': '注意・集中・見通し・ルールの理解・数や文字・気持ちの切り替え・行動のコントロール',
        'language': '言葉の理解・話す・伝える・やりとり・絵カードやジェスチャーなどの手段',
        'social': '大人や友だちとの関わり・集団への参加・順番やルール・自己理解・情緒の安定',
    }
    RATINGS = [
        (1, '全面的な支援が必要'),
        (2, '多くの支援が必要'),
        (3, '一部の支援・声かけが必要'),
        (4, '見守りがあればできる'),
        (5, 'ひとりでできる'),
    ]
    RATING_LABELS = dict(RATINGS)
    TEXT_MAX = 4000

    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.CASCADE, related_name='development_assessments',
                                    verbose_name='利用者')
    date = models.DateField(verbose_name='実施日')
    assessed_by = models.ForeignKey('accounts.StaffAccount', on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='+', verbose_name='評価者')
    interviewed_with = models.CharField(max_length=100, blank=True, verbose_name='聞き取りの相手',
                                        help_text='保護者（母）・学校の先生など')
    # {領域キー: {'rating': 1〜5 か None, 'now': いまの様子, 'goal': 支援の方針}}
    domains = models.JSONField(default=dict, blank=True, verbose_name='領域ごとの評価')
    strengths = models.TextField(blank=True, verbose_name='本人の強み・好きなこと・得意なこと')
    concerns = models.TextField(blank=True, verbose_name='気になること・課題')
    wishes_child = models.TextField(blank=True, verbose_name='本人の希望')
    wishes_family = models.TextField(blank=True, verbose_name='家族の希望')
    summary = models.TextField(blank=True, verbose_name='まとめ・支援の方向性')
    created_by = models.ForeignKey('accounts.StaffAccount', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+', verbose_name='作成者')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '5領域アセスメント'
        verbose_name_plural = '5領域アセスメント'
        ordering = ['-date', '-pk']

    def __str__(self):
        return f'{self.beneficiary.full_name} {self.date} 5領域アセスメント'

    def rating(self, key):
        v = (self.domains or {}).get(key, {}).get('rating')
        return v if v in self.RATING_LABELS else None

    def rows(self, previous=None):
        """画面・印刷用：領域ごとの dict（key, label, hint, rating, rating_label, now, goal, prev＝前回の評価）"""
        out = []
        for key, label in self.DOMAINS:
            d = (self.domains or {}).get(key, {})
            r = self.rating(key)
            out.append({'key': key, 'label': label, 'hint': self.DOMAIN_HINTS[key], 'rating': r,
                        'rating_label': self.RATING_LABELS.get(r, ''), 'now': d.get('now', ''), 'goal': d.get('goal', ''),
                        'prev': previous.rating(key) if previous else None})
        return out

    @property
    def rating_marks(self):
        """一覧に出す短い形：[('健康・生活', 3), ...]（未評価は None）"""
        return [(label, self.rating(key)) for key, label in self.DOMAINS]

    @classmethod
    def domains_from_post(cls, post):
        """フォームの rating_<key>・now_<key>・goal_<key> を domains の形にする"""
        out = {}
        for key, _ in cls.DOMAINS:
            try:
                r = int(post.get(f'rating_{key}') or 0)
            except (TypeError, ValueError):
                r = 0
            out[key] = {'rating': r if r in cls.RATING_LABELS else None,
                        'now': (post.get(f'now_{key}') or '').strip()[:cls.TEXT_MAX],
                        'goal': (post.get(f'goal_{key}') or '').strip()[:cls.TEXT_MAX]}
        return out

    def previous(self):
        """この用紙の前に作った用紙（前回の評価を並べて見るため）"""
        return (DevelopmentAssessment.objects.filter(beneficiary_id=self.beneficiary_id)
                .filter(models.Q(date__lt=self.date) | models.Q(date=self.date, pk__lt=self.pk or 0))
                .order_by('-date', '-pk').first())


class DevelopmentTest(models.Model):
    """
    発達検査・知能検査の結果（新版K式・遠城寺式・KIDS・WISC など）。
    検査ごとの領域の発達年齢（か月）と指数（DQ・IQ など）を持ち、利用者情報で推移をグラフにする。
    検査結果の書類（アセスメント・資料や書類・画像）は AI で読み取って下書きにできる（beneficiaries/dev_tests.py）。
    """
    TESTS = [
        ('kshiki', '新版K式発達検査'),
        ('enjoji', '遠城寺式乳幼児分析的発達検査'),
        ('kids', 'KIDS 乳幼児発達スケール'),
        ('tsumori', '津守・稲毛式乳幼児精神発達診断'),
        ('tanaka', '田中ビネー知能検査'),
        ('wisc', 'WISC（ウィスク）'),
        ('wppsi', 'WPPSI（ウィプシ）'),
        ('other', 'その他'),
    ]
    TEST_LABELS = dict(TESTS)
    # 検査ごとの標準の領域（画面の初期の行）
    DOMAINS = {
        'kshiki': ['姿勢・運動（P-M）', '認知・適応（C-A）', '言語・社会（L-S）'],
        'enjoji': ['移動運動', '手の運動', '基本的習慣', '対人関係', '発語', '言語理解'],
        'kids': ['運動', '操作', '理解言語', '表出言語', '概念', '対子ども社会性', '対成人社会性', 'しつけ', '食事'],
        'tsumori': ['運動', '探索・操作', '社会', '食事・排泄・生活習慣', '理解・言語'],
        'tanaka': [],
        'wisc': ['言語理解', '視空間', '流動性推理', 'ワーキングメモリー', '処理速度'],
        'wppsi': ['言語理解', '知覚推理', '処理速度'],
        'other': [],
    }
    QUOTIENT_LABEL = {'wisc': 'IQ・指標得点', 'wppsi': 'IQ・指標得点', 'tanaka': 'IQ'}

    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.CASCADE, related_name='development_tests', verbose_name='利用者')
    test = models.CharField(max_length=10, choices=TESTS, default='kshiki', verbose_name='検査')
    test_other = models.CharField(max_length=60, blank=True, verbose_name='検査名（その他）')
    date = models.DateField(verbose_name='検査日')
    examiner = models.CharField(max_length=100, blank=True, verbose_name='実施した機関・検査者')
    ca_months = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='生活年齢（か月）')
    overall_age_months = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='全体の発達年齢（か月）')
    overall_quotient = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='全体の指数（DQ・IQ）')
    # [{'label': '認知・適応（C-A）', 'age_months': 30, 'quotient': 85}, …]
    results = models.JSONField(default=list, blank=True, verbose_name='領域ごとの結果')
    note = models.TextField(blank=True, verbose_name='所見・メモ')
    source_label = models.CharField(max_length=200, blank=True, verbose_name='読み取った書類')
    created_by = models.ForeignKey('accounts.StaffAccount', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '発達検査'
        verbose_name_plural = '発達検査'
        ordering = ['-date', '-pk']

    def __str__(self):
        return f'{self.beneficiary.full_name} {self.date} {self.test_name}'

    @property
    def test_name(self):
        return self.test_other if self.test == 'other' and self.test_other else self.TEST_LABELS.get(self.test, '')

    @property
    def quotient_label(self):
        return self.QUOTIENT_LABEL.get(self.test, 'DQ（発達指数）')

    @staticmethod
    def age_label(months):
        if months is None or months == '':
            return ''
        months = int(months)
        return f'{months // 12}歳{months % 12}か月'

    @property
    def ca_label(self):
        return self.age_label(self.ca_months)

    @property
    def overall_age_label(self):
        return self.age_label(self.overall_age_months)

    def result_rows(self):
        return [dict(r, age_label=self.age_label(r.get('age_months'))) for r in (self.results or [])]

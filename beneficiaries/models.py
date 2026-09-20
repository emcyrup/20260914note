import datetime
import secrets

from django.db import models
from django.utils import timezone
from facilities.models import Facility
from facilities.uploads import certificate_upload_to


LINE_CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'  # 見間違えやすい 0/O/1/I を除く
LINE_CODE_LENGTH = 8
LINE_CODE_TTL = datetime.timedelta(hours=72)


def _generate_line_code():
    """LINE連携用の登録コード（8文字の英数字・総当たりできない大きさ）を生成する"""
    return ''.join(secrets.choice(LINE_CODE_ALPHABET) for _ in range(LINE_CODE_LENGTH))


def _line_code_expiry():
    return timezone.now() + LINE_CODE_TTL


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
    STATUS_CHOICES = [
        (STATUS_ACTIVE, '在籍中'),
        (STATUS_INACTIVE, '退所'),
    ]

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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '利用者'
        verbose_name_plural = '利用者'
        ordering = ['last_name_kana', 'first_name_kana']

    def __str__(self):
        return f'{self.last_name} {self.first_name}'

    @property
    def full_name(self):
        return f'{self.last_name} {self.first_name}'

    @property
    def full_name_kana(self):
        return f'{self.last_name_kana} {self.first_name_kana}'

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
    phone = models.CharField(max_length=20, blank=True, verbose_name='電話番号')
    email = models.EmailField(blank=True, verbose_name='メールアドレス')
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
        return f'{self.last_name} {self.first_name}'

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

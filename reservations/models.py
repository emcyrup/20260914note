"""
予約管理（なゆた由来）。

1日あたりの枠を、職員のカレンダー入力と公式LINEからの申し込みの両方で埋める。
予約は「利用者1人につき1件」（1件＝枠1つ）で、人数は持たない。
きょうだいで2人来る日は、利用者ごとに2件の予約になる。
"""
from django.db import models

from beneficiaries.models import Beneficiary
from facilities.models import Facility

from .tokens import MAX_LENGTH as TOKEN_MAX_LENGTH, new_calendar_token, new_customer_token


WEEKDAYS = [(0, '月'), (1, '火'), (2, '水'), (3, '木'), (4, '金'), (5, '土'), (6, '日')]


class ReservationSetting(models.Model):
    """事業所ごとの予約の決まりごと"""

    facility = models.OneToOneField(Facility, on_delete=models.CASCADE,
                                    related_name='reservation_setting', verbose_name='施設')
    capacity = models.PositiveSmallIntegerField(default=10, verbose_name='1日の枠（人）')
    allow_waitlist = models.BooleanField(default=True, verbose_name='満枠のときキャンセル待ちで受ける')
    closed_weekdays = models.JSONField(default=list, blank=True, verbose_name='休業曜日')
    signature = models.CharField(max_length=100, blank=True, verbose_name='通知の署名',
                                 help_text='通知の末尾に付きます（例：放課後等デイサービス なゆた）')
    auto_send = models.BooleanField(default=False, verbose_name='反映したら送信待ちをその場で送る')
    notify_group_id = models.CharField(max_length=100, blank=True, verbose_name='増減を知らせるLINEグループ')
    notify_group_label = models.CharField(max_length=100, blank=True, verbose_name='そのグループの呼び名')

    # ---- 顧客向けの予定表（ログインなしで見えるページ）----
    public_token = models.CharField(max_length=TOKEN_MAX_LENGTH, default=new_calendar_token, unique=True,
                                    verbose_name='予定表の公開アドレス')
    public_calendar = models.BooleanField(default=True, verbose_name='顧客向けの予定表を公開する')
    public_booking = models.BooleanField(default=True, verbose_name='顧客が自分で予約できるようにする')
    booking_from_days = models.PositiveSmallIntegerField(default=1, verbose_name='何日先から受け付けるか',
                                                         help_text='0 なら当日ぶんも受け付けます。')
    booking_until_days = models.PositiveSmallIntegerField(default=60, verbose_name='何日先まで受け付けるか')

    # ---- LINE から直接受け付けるか（既定は職員が確かめてから反映）----
    line_auto_apply = models.BooleanField(default=False, verbose_name='公式LINEの申し込みをその場で反映する')
    group_auto_apply = models.BooleanField(default=True, verbose_name='スタッフのグループの投稿を反映する')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '予約の設定'
        verbose_name_plural = '予約の設定'

    def __str__(self):
        return f'{self.facility} の予約設定（1日{self.capacity}人）'

    def closed_weekday_numbers(self):
        return [n for n in (self.closed_weekdays or []) if isinstance(n, int) and 0 <= n <= 6]

    def weekday_rows(self):
        closed = self.closed_weekday_numbers()
        return [{'num': n, 'label': l, 'closed': n in closed} for n, l in WEEKDAYS]

    @property
    def sign_text(self):
        return self.signature or self.facility.name

    def booking_window(self, today=None):
        """顧客が自分で予約できる日の範囲"""
        import datetime as _dt
        today = today or _dt.date.today()
        return (today + _dt.timedelta(days=self.booking_from_days),
                today + _dt.timedelta(days=self.booking_until_days))

    def reissue_public_token(self):
        self.public_token = new_calendar_token()


class ClosedDate(models.Model):
    """臨時休業日（その日の枠を 0 にする）"""

    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name='reservation_closed_dates')
    date = models.DateField(verbose_name='休業日')
    reason = models.CharField(max_length=100, blank=True, verbose_name='理由')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = '臨時休業日'
        verbose_name_plural = '臨時休業日'
        unique_together = [('facility', 'date')]
        ordering = ['date']

    def __str__(self):
        return f'{self.date} 休業'


class Customer(models.Model):
    """
    顧客（連絡先）。保護者等。1人の顧客が複数の利用者を担当できる。
    保護者台帳（Guardian）は利用者に1対多でぶら下がるのに対し、こちらは予約の連絡先として独立している。
    """

    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name='reservation_customers')
    name = models.CharField(max_length=100, verbose_name='お名前')
    kana = models.CharField(max_length=100, blank=True, verbose_name='ふりがな')
    phone = models.CharField(max_length=20, blank=True, verbose_name='電話番号')
    line_user_id = models.CharField(max_length=100, blank=True, verbose_name='LINEのユーザーID')
    notify_enabled = models.BooleanField(default=True, verbose_name='LINEで通知する')
    note = models.CharField(max_length=200, blank=True, verbose_name='備考')
    children = models.ManyToManyField(Beneficiary, blank=True, related_name='reservation_customers',
                                      verbose_name='担当する利用者')
    token = models.CharField(max_length=TOKEN_MAX_LENGTH, default=new_customer_token, unique=True,
                             verbose_name='顧客ページのアドレス')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '顧客（予約の連絡先）'
        verbose_name_plural = '顧客（予約の連絡先）'
        ordering = ['kana', 'name']
        constraints = [
            models.UniqueConstraint(fields=['facility', 'line_user_id'], name='uniq_customer_line_user',
                                    condition=~models.Q(line_user_id='')),
        ]

    def __str__(self):
        return self.name

    @property
    def can_notify(self):
        return bool(self.line_user_id) and self.notify_enabled

    def reissue_token(self):
        """顧客ページのアドレスを作り直す（前のアドレスは使えなくなる）"""
        self.token = new_customer_token()

    def child_names(self):
        return '、'.join(b.full_name for b in self.children.all())


class Reservation(models.Model):
    """予約（利用者1人につき1件）"""

    STATUS_CONFIRMED = 'confirmed'
    STATUS_WAITLIST = 'waitlist'
    STATUS_CANCELLED = 'cancelled'
    STATUS_DECLINED = 'declined'
    STATUS_CHOICES = [
        (STATUS_CONFIRMED, '予約'),
        (STATUS_WAITLIST, 'キャンセル待ち'),
        (STATUS_CANCELLED, '取消'),
        (STATUS_DECLINED, 'お断り'),
    ]
    ACTIVE_STATUSES = (STATUS_CONFIRMED, STATUS_WAITLIST)

    SOURCE_STAFF = 'staff'
    SOURCE_LINE = 'line'
    SOURCE_WEB = 'web'
    SOURCE_GROUP = 'group'
    SOURCE_CHOICES = [(SOURCE_STAFF, '職員'), (SOURCE_LINE, '公式LINE'),
                      (SOURCE_WEB, '顧客ページ'), (SOURCE_GROUP, 'スタッフのグループ')]

    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name='reservations')
    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.CASCADE, related_name='reservations',
                                    verbose_name='利用者')
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='reservations', verbose_name='連絡先')
    date = models.DateField(verbose_name='予約日')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_CONFIRMED, verbose_name='状態')
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default=SOURCE_STAFF, verbose_name='入口')
    note = models.CharField(max_length=200, blank=True, verbose_name='備考')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    cancelled_at = models.DateTimeField(null=True, blank=True, verbose_name='取消日時')

    class Meta:
        verbose_name = '予約'
        verbose_name_plural = '予約'
        ordering = ['date', 'created_at']
        constraints = [
            # 同じ日・同じ利用者で有効な予約は1件だけ（取消・お断りは何件でも残せる）
            models.UniqueConstraint(fields=['beneficiary', 'date'], name='uniq_active_reservation',
                                    condition=models.Q(status__in=('confirmed', 'waitlist'))),
        ]
        indexes = [models.Index(fields=['facility', 'date', 'status'])]

    def __str__(self):
        return f'{self.date} {self.beneficiary.full_name}（{self.get_status_display()}）'

    @property
    def is_active(self):
        return self.status in self.ACTIVE_STATUSES


class ReservationNotice(models.Model):
    """公式LINEへ送る通知。積んでおき、職員が「まとめて送る」で送信する"""

    KIND_ACCEPTED = 'accepted'
    KIND_WAITLISTED = 'waitlisted'
    KIND_PROMOTED = 'promoted'
    KIND_CANCELLED = 'cancelled'
    KIND_MOVED = 'moved'
    KIND_DECLINED = 'declined'
    KIND_REMINDER = 'reminder'
    KIND_VACANCY = 'vacancy'
    KIND_GROUP = 'group'
    KIND_CHOICES = [
        (KIND_ACCEPTED, '受付'),
        (KIND_WAITLISTED, 'キャンセル待ち'),
        (KIND_PROMOTED, '繰り上げ確定'),
        (KIND_CANCELLED, '取消'),
        (KIND_MOVED, '日にちの変更'),
        (KIND_DECLINED, 'お断り'),
        (KIND_REMINDER, '前日のお知らせ'),
        (KIND_VACANCY, '空き枠'),
        (KIND_GROUP, '予約の増減（グループ）'),
    ]

    STATUS_PENDING = 'pending'
    STATUS_SENT = 'sent'
    STATUS_FAILED = 'failed'
    STATUS_MANUAL = 'manual'
    STATUS_CHOICES = [
        (STATUS_PENDING, '送信待ち'),
        (STATUS_SENT, '送信ずみ'),
        (STATUS_FAILED, '送信できず'),
        (STATUS_MANUAL, '手渡し（コピーして送る）'),
    ]

    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name='reservation_notices')
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='notices', verbose_name='宛先の顧客')
    reservation = models.ForeignKey(Reservation, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='notices', verbose_name='対象の予約')
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, verbose_name='種類')
    date = models.DateField(null=True, blank=True, verbose_name='対象日')
    to_line_id = models.CharField(max_length=100, blank=True, verbose_name='送信先のLINE ID')
    body = models.TextField(verbose_name='文面')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING, verbose_name='状態')
    error_message = models.TextField(blank=True, verbose_name='エラー内容')
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True, verbose_name='送信日時')

    class Meta:
        verbose_name = '予約の通知'
        verbose_name_plural = '予約の通知'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.get_kind_display()} {self.date or ""}（{self.get_status_display()}）'

    @property
    def is_group(self):
        return self.kind == self.KIND_GROUP


class LineInbox(models.Model):
    """
    公式LINEの受信箱。届いた文はここに積み、職員が読み取り結果を確かめて反映する（自動では確定しない）。
    取り込みずみにした行は、本文と表示名をその場で消して二重受信の判定だけ残す。
    """

    SOURCE_USER = 'user'
    SOURCE_GROUP = 'group'
    SOURCE_ROOM = 'room'
    SOURCE_CHOICES = [(SOURCE_USER, '個別'), (SOURCE_GROUP, 'グループ'), (SOURCE_ROOM, '複数人トーク')]

    STATUS_PENDING = 'pending'
    STATUS_DONE = 'done'
    STATUS_IGNORED = 'ignored'
    STATUS_CHOICES = [(STATUS_PENDING, '未処理'), (STATUS_DONE, '反映ずみ'), (STATUS_IGNORED, '見送り')]

    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name='line_inbox')
    message_id = models.CharField(max_length=100, verbose_name='メッセージID')
    source_type = models.CharField(max_length=10, choices=SOURCE_CHOICES, default=SOURCE_USER)
    line_user_id = models.CharField(max_length=100, blank=True)
    group_id = models.CharField(max_length=100, blank=True)
    display_name = models.CharField(max_length=100, blank=True, verbose_name='表示名')
    text = models.TextField(blank=True, verbose_name='本文')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING)
    received_at = models.DateTimeField(auto_now_add=True)
    handled_at = models.DateTimeField(null=True, blank=True)
    handled_by = models.ForeignKey('accounts.StaffAccount', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+')
    result_note = models.CharField(max_length=200, blank=True, verbose_name='処理の結果')

    class Meta:
        verbose_name = 'LINEの受信'
        verbose_name_plural = 'LINEの受信'
        ordering = ['-received_at']
        unique_together = [('facility', 'message_id')]

    def __str__(self):
        return f'{self.received_at:%m/%d %H:%M} {self.display_name or self.line_user_id}'

    def redact(self):
        """取り込みずみ：本文と表示名を消す（個人情報を書き溜めない）"""
        self.text = ''
        self.display_name = ''

"""
予約管理（なゆた由来）。

1日あたりの枠を、職員のカレンダー入力と公式LINEからの申し込みの両方で埋める。
予約は「利用者1人につき1件」（1件＝枠1つ）で、人数は持たない。
きょうだいで2人来る日は、利用者ごとに2件の予約になる。
"""
import datetime

from django.db import models

from beneficiaries.models import Beneficiary
from facilities.models import Facility
from facilities.uploads import request_scan_upload_to

from .tokens import MAX_LENGTH as TOKEN_MAX_LENGTH, new_calendar_token, new_customer_token


WEEKDAYS = [(0, '月'), (1, '火'), (2, '水'), (3, '木'), (4, '金'), (5, '土'), (6, '日')]


def default_break_hours():
    return [12]


def hour_label(hour):
    return f'{hour}:00'


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
    public_request = models.BooleanField(default=True, verbose_name='空き状況のページから申し込みを受ける')
    booking_from_days = models.PositiveSmallIntegerField(default=1, verbose_name='何日先から受け付けるか',
                                                         help_text='0 なら当日ぶんも受け付けます。')
    booking_until_days = models.PositiveSmallIntegerField(default=60, verbose_name='何日先まで受け付けるか')

    # ---- LINE から直接受け付けるか（既定は職員が確かめてから反映）----
    notify_vacancy = models.BooleanField(default=True, verbose_name='満席から空きが出たら、顧客へお知らせする')

    # ---- 予約の受け方 ----
    MODE_AUTO = 'auto'
    MODE_APPROVE = 'approve'
    MODE_CHOICES = [
        (MODE_AUTO, '来た順に自動で確定する'),
        (MODE_APPROVE, '職員が確認してから確定する'),
    ]
    booking_mode = models.CharField(max_length=10, choices=MODE_CHOICES, default=MODE_AUTO,
                                    verbose_name='予約の受け方')
    group_auto_apply = models.BooleanField(default=True, verbose_name='スタッフのグループの投稿を反映する')

    # ---- 時間枠で予約する（りょういく：1枠45分・1枠3人・月予約利用希望から月間予定表を作る）----
    slot_mode = models.BooleanField(default=False, verbose_name='時間枠で予約する',
                                    help_text='1日の枠ではなく、1時間ごとの枠（1枠45分）に人数の上限を置きます。'
                                              '月予約利用希望から月間予定表を作れます。')
    slot_capacity = models.PositiveSmallIntegerField(default=3, verbose_name='1枠の人数')
    slot_minutes = models.PositiveSmallIntegerField(default=45, verbose_name='1枠の長さ（分）')
    weekday_first_hour = models.PositiveSmallIntegerField(default=10, verbose_name='平日の最初の枠（時）')
    weekday_last_hour = models.PositiveSmallIntegerField(default=18, verbose_name='平日の最後の枠（時）')
    holiday_first_hour = models.PositiveSmallIntegerField(default=9, verbose_name='土日祝の最初の枠（時）')
    holiday_last_hour = models.PositiveSmallIntegerField(default=17, verbose_name='土日祝の最後の枠（時）')
    break_hours = models.JSONField(default=default_break_hours, blank=True, verbose_name='枠を置かない時刻',
                                   help_text='昼休みなど。例：12')
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

    @property
    def is_auto(self):
        """来た順に自動で確定する（満席なら、その場でキャンセル待ちかお断り）"""
        return self.booking_mode == self.MODE_AUTO

    def reissue_public_token(self):
        self.public_token = new_calendar_token()

    # ---- 時間枠 ----
    def break_hour_numbers(self):
        return [n for n in (self.break_hours or []) if isinstance(n, int) and 0 <= n <= 23]

    def slot_hours(self, day):
        """その日の枠の開始時刻（時）。休業曜日は空。平日と土日祝で時間帯が違う"""
        if not self.slot_mode or day.weekday() in self.closed_weekday_numbers():
            return []
        from config.jp_holidays import is_weekend_or_holiday
        if is_weekend_or_holiday(day):
            first, last = self.holiday_first_hour, self.holiday_last_hour
        else:
            first, last = self.weekday_first_hour, self.weekday_last_hour
        skip = self.break_hour_numbers()
        return [h for h in range(first, last + 1) if h not in skip]

    def all_slot_hours(self):
        """平日・土日祝を合わせた、枠のある時刻ぜんぶ（利用希望の用紙の列に使う）"""
        skip = self.break_hour_numbers()
        first = min(self.weekday_first_hour, self.holiday_first_hour)
        last = max(self.weekday_last_hour, self.holiday_last_hour)
        return [h for h in range(first, last + 1) if h not in skip]

    def slot_capacity_of(self, day):
        """その日の枠の合計人数（時間枠のとき）"""
        return len(self.slot_hours(day)) * self.slot_capacity

    @property
    def hours_text(self):
        """利用希望の用紙に書く時間帯の説明"""
        return (f'平日 {self.weekday_first_hour}:00〜{self.weekday_last_hour}:00枠、'
                f'土・日・祝 {self.holiday_first_hour}:00〜{self.holiday_last_hour}:00枠'
                f'（1枠{self.slot_minutes}分）')

    @property
    def closed_weekdays_text(self):
        labels = dict(WEEKDAYS)
        return '・'.join(f'{labels[n]}曜日' for n in self.closed_weekday_numbers())


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


class BookingRequest(models.Model):
    """
    空き状況のページ（アドレスを知っている人なら誰でも開ける）から届いた申し込み。

    ここでは予約にしない。職員が「どの利用者か」を確かめて反映したときだけ予約になる
    （知らない名前で枠が埋まらないように）。
    """

    STATUS_PENDING = 'pending'
    STATUS_DONE = 'done'
    STATUS_DECLINED = 'declined'
    STATUS_CHOICES = [(STATUS_PENDING, '未確認'), (STATUS_DONE, '予約にした'), (STATUS_DECLINED, '見送り')]

    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name='booking_requests')
    date = models.DateField(verbose_name='希望日')
    name = models.CharField(max_length=100, verbose_name='お申し込みの方のお名前')
    kana = models.CharField(max_length=100, blank=True, verbose_name='ふりがな')
    phone = models.CharField(max_length=20, blank=True, verbose_name='電話番号')
    child_name = models.CharField(max_length=100, blank=True, verbose_name='お子さまのお名前')
    note = models.CharField(max_length=200, blank=True, verbose_name='ご要望')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING, verbose_name='状態')
    reservation = models.ForeignKey('Reservation', on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='+', verbose_name='できた予約')
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='booking_requests', verbose_name='顧客台帳の相手')
    created_at = models.DateTimeField(auto_now_add=True)
    handled_at = models.DateTimeField(null=True, blank=True)
    handled_by = models.ForeignKey('accounts.StaffAccount', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+')
    result_note = models.CharField(max_length=200, blank=True, verbose_name='処理の結果')

    class Meta:
        verbose_name = '予約の申し込み'
        verbose_name_plural = '予約の申し込み'
        ordering = ['-created_at']
        indexes = [models.Index(fields=['facility', 'status'])]

    def __str__(self):
        return f'{self.date} {self.name}（{self.get_status_display()}）'

    @property
    def is_pending(self):
        return self.status == self.STATUS_PENDING


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
    SOURCE_REQUEST = 'request'
    SOURCE_CHOICES = [(SOURCE_STAFF, '職員'), (SOURCE_LINE, '公式LINE'),
                      (SOURCE_WEB, '顧客ページ'), (SOURCE_GROUP, 'スタッフのグループ'),
                      (SOURCE_REQUEST, '月予約利用希望')]

    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name='reservations')
    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.CASCADE, related_name='reservations',
                                    null=True, blank=True, verbose_name='利用者')
    # 台帳にまだいない方のお申し込み。職員があとから利用者に結びつける
    guest_name = models.CharField(max_length=100, blank=True, verbose_name='お名前（台帳に未登録）')
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='reservations', verbose_name='連絡先')
    date = models.DateField(verbose_name='予約日')
    # 時間枠で予約する事業所だけ使う（1日の枠の事業所では空）
    start_time = models.TimeField(null=True, blank=True, verbose_name='開始時刻')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_CONFIRMED, verbose_name='状態')
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default=SOURCE_STAFF, verbose_name='入口')
    note = models.CharField(max_length=200, blank=True, verbose_name='備考')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    cancelled_at = models.DateTimeField(null=True, blank=True, verbose_name='取消日時')

    class Meta:
        verbose_name = '予約'
        verbose_name_plural = '予約'
        ordering = ['date', 'start_time', 'created_at']
        constraints = [
            # 同じ日・同じ利用者で有効な予約は1件だけ（取消・お断りは何件でも残せる）
            models.UniqueConstraint(fields=['beneficiary', 'date'], name='uniq_active_reservation',
                                    condition=models.Q(status__in=('confirmed', 'waitlist'))),
            # 台帳に未登録の方も、同じ日・同じお名前で二重にならないようにする
            models.UniqueConstraint(fields=['facility', 'guest_name', 'date'], name='uniq_active_guest_reservation',
                                    condition=models.Q(status__in=('confirmed', 'waitlist'),
                                                       beneficiary__isnull=True) & ~models.Q(guest_name='')),
            models.CheckConstraint(condition=models.Q(beneficiary__isnull=False) | ~models.Q(guest_name=''),
                                   name='reservation_has_someone'),
        ]
        indexes = [models.Index(fields=['facility', 'date', 'status'])]

    def __str__(self):
        return f'{self.date} {self.display_name}（{self.get_status_display()}）'

    @property
    def display_name(self):
        """台帳にいればその名前、いなければお申し込みのお名前"""
        return self.beneficiary.full_name if self.beneficiary_id else self.guest_name

    @property
    def is_guest(self):
        return not self.beneficiary_id

    @property
    def hour(self):
        return self.start_time.hour if self.start_time else None

    @property
    def time_label(self):
        return f'{self.start_time.hour}:{self.start_time.minute:02d}' if self.start_time else ''

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
    KIND_WISH = 'wish'
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
        (KIND_WISH, '利用希望の入力のお願い'),
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


class MonthlyRequest(models.Model):
    """
    月予約利用希望（時間枠で予約する事業所）。

    利用者ごと・月ごとに1枚。「可能な日時の枠に○」を付けた用紙を職員が転記するか、
    顧客が自分のページから送る。ここから月間予定表（予約）を作る。
    wishes は {"2026-10-03": "all", "2026-10-05": [10, 11]} の形（"all" は終日）。
    """

    SOURCE_STAFF = 'staff'
    SOURCE_WEB = 'web'
    SOURCE_PHOTO = 'photo'
    SOURCE_CHOICES = [(SOURCE_STAFF, '職員が転記'), (SOURCE_WEB, '顧客ページ'), (SOURCE_PHOTO, '用紙の写真から')]

    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name='monthly_requests')
    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.CASCADE, related_name='monthly_requests',
                                    verbose_name='利用者')
    year = models.PositiveSmallIntegerField(verbose_name='年')
    month = models.PositiveSmallIntegerField(verbose_name='月')
    desired_count = models.PositiveSmallIntegerField(default=0, verbose_name='希望利用回数')
    wishes = models.JSONField(default=dict, blank=True, verbose_name='可能な日時')
    note = models.CharField(max_length=200, blank=True, verbose_name='備考')
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default=SOURCE_STAFF, verbose_name='入口')
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='monthly_requests', verbose_name='送った顧客')
    created_by = models.ForeignKey('accounts.StaffAccount', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '月予約利用希望'
        verbose_name_plural = '月予約利用希望'
        unique_together = [('beneficiary', 'year', 'month')]
        ordering = ['-year', '-month', 'beneficiary__last_name_kana']

    def __str__(self):
        return f'{self.year}年{self.month}月 {self.beneficiary.full_name}（希望{self.desired_count}回）'

    @property
    def label(self):
        return f'{self.year}年{self.month}月'

    def wish_of(self, day):
        """その日の希望：'all'（終日）・時刻のリスト・None（希望なし）"""
        value = (self.wishes or {}).get(day.isoformat())
        if value == 'all':
            return 'all'
        if isinstance(value, list):
            hours = sorted({h for h in value if isinstance(h, int)})
            return hours or None
        return None

    def wish_hours(self, day, setting):
        """その日に○の付いた枠の時刻（休業日・枠のない時刻は除く）"""
        wish = self.wish_of(day)
        hours = setting.slot_hours(day)
        if wish is None:
            return []
        if wish == 'all':
            return list(hours)
        return [h for h in wish if h in hours]

    def wished_days(self):
        out = []
        for key, value in (self.wishes or {}).items():
            if value == 'all' or (isinstance(value, list) and value):
                try:
                    out.append(datetime.date.fromisoformat(key))
                except ValueError:
                    continue
        return sorted(out)

    def slot_count(self, setting):
        return sum(len(self.wish_hours(d, setting)) for d in self.wished_days())


class RequestScan(models.Model):
    """
    紙の「月予約利用希望」を撮った写真。AI が○の位置を読み取り、職員が確かめてから利用希望にする。
    """
    STATUS_PENDING = 'pending'
    STATUS_EXTRACTED = 'extracted'
    STATUS_IMPORTED = 'imported'
    STATUS_CHOICES = [(STATUS_PENDING, '未読み取り'), (STATUS_EXTRACTED, '確認待ち'), (STATUS_IMPORTED, '反映ずみ')]

    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name='request_scans')
    year = models.PositiveSmallIntegerField(verbose_name='年')
    month = models.PositiveSmallIntegerField(verbose_name='月')
    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='request_scans', verbose_name='利用者')
    image = models.ImageField(upload_to=request_scan_upload_to, verbose_name='用紙の写真')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING, verbose_name='状態')
    extracted = models.JSONField(default=dict, blank=True, verbose_name='読み取り結果')
    error = models.TextField(blank=True, verbose_name='エラー')
    request = models.ForeignKey(MonthlyRequest, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='scans', verbose_name='反映した利用希望')
    uploaded_by = models.ForeignKey('accounts.StaffAccount', on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    extracted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = '利用希望の用紙（写真）'
        verbose_name_plural = '利用希望の用紙（写真）'
        ordering = ['created_at']

    def __str__(self):
        return f'{self.year}年{self.month}月 用紙の写真 #{self.pk}（{self.get_status_display()}）'

    @property
    def read_name(self):
        return (self.extracted or {}).get('name') or ''

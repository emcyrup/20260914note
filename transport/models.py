"""
送迎・配車（施設設定で「送迎・配車を使う」にした事業所。オウルなど）。

- Vehicle（車両）・Driver（運転手）：事業所ごとに登録する。運転手はログインする職員でなくてよいので別に持つ
- TransportProfile（利用者ごとの送迎の設定）：迎え／送りのある・なし、場所、いつもの車両、注意
- TransportAssignment（その日の配車）：予約 × 迎え／送り ごとに、車両・運転手・時刻・場所・備考。
  配車表は「その日の確定した予約 × 送迎の設定」から作り、この表は決めたぶんだけ持つ。便（グループ）は作らず一覧で見る
"""
from django.db import models

from beneficiaries.models import Beneficiary
from facilities.models import Facility

DIRECTION_PICKUP = 'pickup'
DIRECTION_DROPOFF = 'dropoff'
DIRECTIONS = [(DIRECTION_PICKUP, '迎え'), (DIRECTION_DROPOFF, '送り')]
DIRECTION_LABELS = dict(DIRECTIONS)


class Vehicle(models.Model):
    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name='vehicles')
    name = models.CharField(max_length=50, verbose_name='車両名', help_text='例：ハイエース（白）・軽ワゴン')
    capacity = models.PositiveSmallIntegerField(default=0, verbose_name='乗れる人数（運転手を除く）',
                                                help_text='0 なら未設定。配車表で人数が超えると色が付きます')
    plate = models.CharField(max_length=30, blank=True, verbose_name='ナンバー・色など')
    default_driver = models.ForeignKey('Driver', on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='default_vehicles', verbose_name='いつもの運転手')
    note = models.CharField(max_length=200, blank=True, verbose_name='備考',
                            help_text='チャイルドシートの数・車いす対応など')
    is_active = models.BooleanField(default=True, verbose_name='使っている')
    order = models.PositiveSmallIntegerField(default=0, verbose_name='並び順')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '車両'
        verbose_name_plural = '車両'
        ordering = ['order', 'pk']

    def __str__(self):
        return self.name


class Driver(models.Model):
    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name='drivers')
    name = models.CharField(max_length=50, verbose_name='運転手の名前')
    phone = models.CharField(max_length=20, blank=True, verbose_name='電話番号')
    note = models.CharField(max_length=200, blank=True, verbose_name='備考', help_text='出られる曜日・時間など')
    is_active = models.BooleanField(default=True, verbose_name='在籍中')
    order = models.PositiveSmallIntegerField(default=0, verbose_name='並び順')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '運転手'
        verbose_name_plural = '運転手'
        ordering = ['order', 'pk']

    def __str__(self):
        return self.name


class TransportProfile(models.Model):
    """利用者ごとの送迎の設定（利用者情報の「送迎」欄）"""
    beneficiary = models.OneToOneField(Beneficiary, on_delete=models.CASCADE, related_name='transport', verbose_name='利用者')
    pickup = models.BooleanField(default=False, verbose_name='迎えあり')
    pickup_place = models.CharField(max_length=200, blank=True, verbose_name='迎えの場所', help_text='学校名・自宅など')
    pickup_time = models.TimeField(null=True, blank=True, verbose_name='迎えの目安の時刻')
    dropoff = models.BooleanField(default=False, verbose_name='送りあり')
    dropoff_place = models.CharField(max_length=200, blank=True, verbose_name='送りの場所')
    dropoff_time = models.TimeField(null=True, blank=True, verbose_name='送りの目安の時刻')
    default_vehicle = models.ForeignKey(Vehicle, on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name='regulars', verbose_name='いつもの車両')
    note = models.CharField(max_length=200, blank=True, verbose_name='送迎の注意',
                            help_text='チャイルドシート・酔いやすい・引き渡しの相手など')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '送迎の設定'
        verbose_name_plural = '送迎の設定'

    def __str__(self):
        return f'{self.beneficiary} の送迎'

    def has(self, direction):
        return self.pickup if direction == DIRECTION_PICKUP else self.dropoff

    def place(self, direction):
        return self.pickup_place if direction == DIRECTION_PICKUP else self.dropoff_place

    def time(self, direction):
        return self.pickup_time if direction == DIRECTION_PICKUP else self.dropoff_time

    @property
    def summary(self):
        """一覧向けの短い形：「迎え（○○小）・送り（自宅）」。送迎が無ければ空"""
        parts = []
        if self.pickup:
            parts.append('迎え' + (f'（{self.pickup_place}）' if self.pickup_place else ''))
        if self.dropoff:
            parts.append('送り' + (f'（{self.dropoff_place}）' if self.dropoff_place else ''))
        return '・'.join(parts)


class TransportAssignment(models.Model):
    """その日の配車（予約 × 迎え／送り）。設定どおりで変えていない日は行が無くてもよい"""
    reservation = models.ForeignKey('reservations.Reservation', on_delete=models.CASCADE, related_name='transports')
    direction = models.CharField(max_length=10, choices=DIRECTIONS, verbose_name='迎え／送り')
    vehicle = models.ForeignKey(Vehicle, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='車両')
    driver = models.ForeignKey(Driver, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='運転手')
    time = models.TimeField(null=True, blank=True, verbose_name='時刻')
    place = models.CharField(max_length=200, blank=True, verbose_name='場所（この日だけ変えるとき）')
    skip = models.BooleanField(default=False, verbose_name='この日は送迎なし')
    added = models.BooleanField(default=False, verbose_name='設定に無いがこの日だけ送迎する')
    note = models.CharField(max_length=200, blank=True, verbose_name='備考')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '配車'
        verbose_name_plural = '配車'
        constraints = [models.UniqueConstraint(fields=['reservation', 'direction'], name='uniq_transport_assignment')]

    def __str__(self):
        return f'{self.reservation} {self.get_direction_display()}'

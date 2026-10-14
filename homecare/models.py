"""
居宅介護（障害福祉サービスのホームヘルプ）。画面の型「居宅介護」（Facility.LAYOUT_KYOTAKU）の事業所で使う。

- HomeCareProfile：利用者ごとの居宅介護の設定（障害支援区分・サービスの種類ごとの支給量（時間/月）・援助の留意点）
- Visit          ：1回の訪問。予定（日・開始〜終了・サービスの種類）と、サービス提供記録（実際の時刻・したこと・体調・様子）を1件で持つ
- CarePlan       ：居宅介護計画（本人・家族の希望・課題・目標・週間のサービス内容・留意事項・説明と同意）
"""
import datetime
import math

from django.db import models

from beneficiaries.models import Beneficiary
from facilities.models import Facility

# サービスの種類（実績記録票の「サービス内容」と同じ並び）
TYPE_BODY = 'body'
TYPE_HOUSE = 'house'
TYPE_ESCORT_BODY = 'escort_body'
TYPE_ESCORT = 'escort'
TYPE_RIDE = 'ride'
SERVICE_TYPES = [
    (TYPE_BODY, '身体介護'),
    (TYPE_HOUSE, '家事援助'),
    (TYPE_ESCORT_BODY, '通院等介助（身体介護を伴う）'),
    (TYPE_ESCORT, '通院等介助（身体介護を伴わない）'),
    (TYPE_RIDE, '通院等乗降介助'),
]
SERVICE_SHORT = {TYPE_BODY: '身体', TYPE_HOUSE: '家事', TYPE_ESCORT_BODY: '通院(身)', TYPE_ESCORT: '通院', TYPE_RIDE: '乗降'}

# サービス提供記録の「したこと」（印を付ける）。身体介護と家事援助に分けて並べる
TASK_GROUPS = [
    ('身体介護', [
        ('excretion', '排せつ介助'), ('meal', '食事介助'), ('bath', '入浴介助'), ('wipe', '清拭・部分浴'),
        ('dress', '更衣介助'), ('groom', '整容（洗面・歯みがき・整髪）'), ('position', '体位変換'),
        ('transfer', '移乗・移動介助'), ('medicine', '服薬の確認'), ('outing', '外出・通院の介助'),
    ]),
    ('家事援助', [
        ('clean', '掃除'), ('laundry', '洗濯'), ('cook', '調理'), ('serve', '配膳・下膳'),
        ('shopping', '買い物'), ('clothes', '衣類の整理・補修'), ('bed', 'ベッドメイク'), ('pickup', '薬の受け取り'),
    ]),
    ('そのほか', [
        ('talk', '相談・助言'), ('check', '安否・体調の確認'), ('contact', '家族・関係機関への連絡'),
    ]),
]
TASK_LABELS = {k: label for _, items in TASK_GROUPS for k, label in items}

SUPPORT_LEVELS = [('', '—'), ('none', '非該当'), ('1', '区分1'), ('2', '区分2'), ('3', '区分3'),
                  ('4', '区分4'), ('5', '区分5'), ('6', '区分6')]


class HomeCareProfile(models.Model):
    beneficiary = models.OneToOneField(Beneficiary, on_delete=models.CASCADE, related_name='homecare_profile',
                                       verbose_name='利用者')
    support_level = models.CharField(max_length=4, choices=SUPPORT_LEVELS, blank=True, verbose_name='障害支援区分')
    # {サービスの種類: 支給量（時間/月）}。受給者証の「居宅介護」の支給量
    grants = models.JSONField(default=dict, blank=True, verbose_name='支給量（時間/月）')
    cautions = models.TextField(blank=True, verbose_name='援助の留意点',
                                help_text='体の状態・してはいけないこと・家の中のきまりなど。記録の画面の上に出ます')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '居宅介護の設定'
        verbose_name_plural = '居宅介護の設定'

    def grant_of(self, kind):
        try:
            v = float((self.grants or {}).get(kind) or 0)
        except (TypeError, ValueError):
            return 0
        return v if v > 0 else 0


class Visit(models.Model):
    STATUS_PLANNED = 'planned'
    STATUS_DONE = 'done'
    STATUS_CANCELLED = 'cancelled'
    STATUS_CHOICES = [(STATUS_PLANNED, '予定'), (STATUS_DONE, '実施'), (STATUS_CANCELLED, '中止')]
    CONDITION_CHOICES = [('', '—'), ('good', '良い'), ('usual', 'ふつう'), ('poor', 'よくない')]

    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name='homecare_visits')
    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.CASCADE, related_name='homecare_visits',
                                    verbose_name='利用者')
    date = models.DateField(verbose_name='日付')
    start_time = models.TimeField(verbose_name='開始（予定）')
    end_time = models.TimeField(verbose_name='終了（予定）')
    service_type = models.CharField(max_length=20, choices=SERVICE_TYPES, default=TYPE_BODY, verbose_name='サービスの種類')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PLANNED, verbose_name='状態')
    plan_note = models.CharField(max_length=200, blank=True, verbose_name='予定のメモ', help_text='例：通院（〇〇病院）・入浴あり')
    # ---- サービス提供記録
    actual_start = models.TimeField(null=True, blank=True, verbose_name='開始（実際）')
    actual_end = models.TimeField(null=True, blank=True, verbose_name='終了（実際）')
    tasks = models.JSONField(default=list, blank=True, verbose_name='したこと')
    temperature = models.DecimalField(max_digits=3, decimal_places=1, null=True, blank=True, verbose_name='体温')
    bp_high = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='血圧（上）')
    bp_low = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='血圧（下）')
    pulse = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='脈拍')
    condition = models.CharField(max_length=10, choices=CONDITION_CHOICES, blank=True, verbose_name='顔色・体調')
    body = models.TextField(blank=True, verbose_name='様子・特記事項')
    cancel_reason = models.CharField(max_length=200, blank=True, verbose_name='中止の理由')
    staff = models.ForeignKey('accounts.StaffAccount', on_delete=models.SET_NULL, null=True, blank=True,
                              related_name='+', verbose_name='担当')
    recorded_at = models.DateTimeField(null=True, blank=True, verbose_name='記録した日時')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '訪問'
        verbose_name_plural = '訪問'
        ordering = ['date', 'start_time', 'pk']
        indexes = [models.Index(fields=['facility', 'date'])]

    def __str__(self):
        return f'{self.date} {self.time_label} {self.beneficiary.full_name}'

    @staticmethod
    def _minutes(a, b):
        if a is None or b is None:
            return 0
        m = (datetime.datetime.combine(datetime.date.min, b) - datetime.datetime.combine(datetime.date.min, a)).seconds // 60
        return m

    @property
    def time_label(self):
        return f'{self.start_time:%H:%M}〜{self.end_time:%H:%M}'

    @property
    def actual_label(self):
        if self.actual_start and self.actual_end:
            return f'{self.actual_start:%H:%M}〜{self.actual_end:%H:%M}'
        return ''

    @property
    def planned_minutes(self):
        return self._minutes(self.start_time, self.end_time)

    @property
    def planned_hours(self):
        return self.planned_minutes / 60

    @property
    def actual_minutes(self):
        return self._minutes(self.actual_start, self.actual_end)

    @property
    def billing_hours(self):
        """
        算定時間（時間。0.5 刻み）の目安：計画（予定）の時間を 30 分単位で数える。30 分未満でも 0.5。
        実際が予定より短いときは実際の時間（30 分単位に切り捨て、最低 0.5）。正式な算定は請求のきまりで確かめる
        """
        minutes = self.planned_minutes
        if self.actual_start and self.actual_end and 0 < self.actual_minutes < minutes:
            minutes = self.actual_minutes
        if minutes <= 0:
            return 0
        return max(0.5, math.floor(minutes / 30) / 2)

    @property
    def task_labels(self):
        return [TASK_LABELS[t] for t in (self.tasks or []) if t in TASK_LABELS]

    @property
    def is_recorded(self):
        return self.status == self.STATUS_DONE

    @property
    def short_type(self):
        return SERVICE_SHORT.get(self.service_type, '')


class CarePlan(models.Model):
    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name='homecare_plans')
    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.CASCADE, related_name='homecare_plans',
                                    verbose_name='利用者')
    created_on = models.DateField(verbose_name='作成日')
    period_start = models.DateField(null=True, blank=True, verbose_name='計画期間（開始）')
    period_end = models.DateField(null=True, blank=True, verbose_name='計画期間（終了）')
    wishes = models.TextField(blank=True, verbose_name='本人・家族の希望')
    situation = models.TextField(blank=True, verbose_name='心身の状況・生活の状況')
    issues = models.TextField(blank=True, verbose_name='解決すべき課題')
    long_goal = models.TextField(blank=True, verbose_name='長期目標')
    short_goal = models.TextField(blank=True, verbose_name='短期目標')
    # [{'days': '月・木', 'start': '10:00', 'end': '11:00', 'type': 'body', 'content': '入浴介助・更衣'}]
    services = models.JSONField(default=list, blank=True, verbose_name='サービス内容（週間）')
    notes = models.TextField(blank=True, verbose_name='留意事項')
    explained_on = models.DateField(null=True, blank=True, verbose_name='説明・同意日')
    agreed_by = models.CharField(max_length=100, blank=True, verbose_name='同意した人')
    created_by = models.ForeignKey('accounts.StaffAccount', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+', verbose_name='作成者')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '居宅介護計画'
        verbose_name_plural = '居宅介護計画'
        ordering = ['-created_on', '-pk']

    def __str__(self):
        return f'{self.beneficiary.full_name} {self.created_on} 居宅介護計画'

    SERVICE_ROWS = 8

    def service_rows(self):
        """画面・印刷用：SERVICE_ROWS 行（空の行も）"""
        rows = [dict(r) for r in (self.services or []) if isinstance(r, dict)]
        labels = dict(SERVICE_TYPES)
        for r in rows:
            r['type_label'] = labels.get(r.get('type', ''), '')
        return rows + [{} for _ in range(max(0, self.SERVICE_ROWS - len(rows)))]

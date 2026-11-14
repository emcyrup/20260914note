"""
毎日の運営（施設設定で「毎日の運営を使う」にした事業所。オウルなど）。

- ClassGroup・GroupSession：クラス（小集団）と、その日の活動（計画＝ねらい・活動・準備物、記録＝全体の様子と一人ずつのひとこと）。
  記録は「各児の療育記録に写す」で therapy.TherapyRecord に 1 人 1 件ずつ写す（写した記録は record_ids で覚え、写し直すと上書き）
- HealthLog：利用者ごと・日ごとの健康と生活の記録（体温・食事・排せつ・午睡・服薬・機嫌・引き渡し）
- HealthProfile：アレルギー・服薬・発作など、毎日の記録の画面と引き渡しカードに赤く出す注意
"""
import datetime

from django.db import models

from beneficiaries.models import Beneficiary
from facilities.models import Facility

WEEK_JP = ['月', '火', '水', '木', '金', '土', '日']


class ClassGroup(models.Model):
    COLORS = [('#4e7d89', '青緑'), ('#c0703b', 'だいだい'), ('#6a8f3c', '緑'), ('#8a5fa8', '紫'), ('#b3475f', '赤'), ('#3d6fb0', '青')]

    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name='class_groups')
    name = models.CharField(max_length=50, verbose_name='クラス名', help_text='例：ひよこ組・午前の小集団')
    color = models.CharField(max_length=10, default='#4e7d89', verbose_name='色')
    members = models.ManyToManyField(Beneficiary, blank=True, related_name='class_groups', verbose_name='メンバー')
    weekdays = models.JSONField(default=list, blank=True, verbose_name='活動する曜日', help_text='0=月 … 5=土')
    start_time = models.TimeField(null=True, blank=True, verbose_name='いつもの開始時刻')
    note = models.CharField(max_length=200, blank=True, verbose_name='メモ')
    is_active = models.BooleanField(default=True, verbose_name='使っている')
    order = models.PositiveSmallIntegerField(default=0, verbose_name='並び順')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'クラス'
        verbose_name_plural = 'クラス'
        ordering = ['order', 'pk']

    def __str__(self):
        return self.name

    @property
    def weekdays_label(self):
        return '・'.join(WEEK_JP[d] for d in sorted(self.weekdays or []) if 0 <= d <= 6)


class GroupSession(models.Model):
    """クラスのその日の活動（計画と記録）"""
    group = models.ForeignKey(ClassGroup, on_delete=models.CASCADE, related_name='sessions')
    date = models.DateField(verbose_name='日付')
    start_time = models.TimeField(null=True, blank=True, verbose_name='開始時刻')
    # 計画
    aim = models.CharField(max_length=200, blank=True, verbose_name='ねらい')
    activities = models.JSONField(default=list, blank=True, verbose_name='活動（①〜⑤）')
    materials = models.CharField(max_length=300, blank=True, verbose_name='準備物')
    staff_name = models.CharField(max_length=100, blank=True, verbose_name='担当')
    # 記録
    body = models.TextField(blank=True, verbose_name='全体の様子')
    present = models.JSONField(default=list, blank=True, verbose_name='参加した利用者（ID）')
    notes = models.JSONField(default=dict, blank=True, verbose_name='一人ずつのひとこと')   # {'利用者ID': '…'}
    record_ids = models.JSONField(default=dict, blank=True, verbose_name='写した療育記録')    # {'利用者ID': TherapyRecord の ID}
    applied_at = models.DateTimeField(null=True, blank=True, verbose_name='療育記録に写した日時')
    created_by = models.ForeignKey('accounts.StaffAccount', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'クラスの活動'
        verbose_name_plural = 'クラスの活動'
        ordering = ['date', 'start_time', 'pk']
        constraints = [models.UniqueConstraint(fields=['group', 'date'], name='uniq_group_session_day')]

    def __str__(self):
        return f'{self.date} {self.group.name}'

    @property
    def activity_list(self):
        return [a for a in (self.activities or []) if isinstance(a, str) and a.strip()]

    @property
    def has_plan(self):
        return bool(self.aim or self.activity_list or self.materials)

    @property
    def has_record(self):
        return bool(self.body.strip() or any((v or '').strip() for v in (self.notes or {}).values()))

    @property
    def weekday(self):
        return WEEK_JP[self.date.weekday()]


class HealthProfile(models.Model):
    """毎日の記録と引き渡しカードに出す注意（アレルギー・服薬・発作など）"""
    beneficiary = models.OneToOneField(Beneficiary, on_delete=models.CASCADE, related_name='health_profile')
    allergies = models.CharField(max_length=300, blank=True, verbose_name='アレルギー・食べられないもの')
    medications = models.CharField(max_length=300, blank=True, verbose_name='服薬（薬の名前・時間）')
    seizure = models.CharField(max_length=300, blank=True, verbose_name='発作・医療的ケア')
    other = models.CharField(max_length=300, blank=True, verbose_name='その他の注意')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '健康の注意'
        verbose_name_plural = '健康の注意'

    def __str__(self):
        return f'{self.beneficiary} の健康の注意'

    @property
    def alerts(self):
        out = []
        for label, v in (('アレルギー', self.allergies), ('服薬', self.medications), ('発作・医療的ケア', self.seizure), ('注意', self.other)):
            if v.strip():
                out.append(f'{label}：{v.strip()}')
        return out


class HealthLog(models.Model):
    MEAL_CHOICES = [('', '－'), ('all', '完食'), ('most', 'ほぼ食べた'), ('half', '半分'), ('little', '少し'), ('none', '食べない'), ('na', 'なし（食事の時間なし）')]
    MOOD_CHOICES = [('', '－'), ('good', 'ごきげん'), ('normal', 'ふつう'), ('tired', '疲れぎみ'), ('bad', '不機嫌・体調不良')]
    MOOD_ICONS = {'good': '😊', 'normal': '🙂', 'tired': '😪', 'bad': '😣'}
    FEVER = 37.5

    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name='health_logs')
    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.CASCADE, related_name='health_logs', verbose_name='利用者')
    date = models.DateField(verbose_name='日付')
    temp_arrival = models.DecimalField(max_digits=3, decimal_places=1, null=True, blank=True, verbose_name='体温（来所時）')
    temp_other = models.DecimalField(max_digits=3, decimal_places=1, null=True, blank=True, verbose_name='体温（そのほか）')
    meal = models.CharField(max_length=10, choices=MEAL_CHOICES, blank=True, verbose_name='食事・おやつ')
    meal_note = models.CharField(max_length=200, blank=True, verbose_name='食事のメモ')
    urine = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='おしっこ（回）')
    stool = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='うんち（回）')
    toilet_note = models.CharField(max_length=200, blank=True, verbose_name='排せつのメモ', help_text='トイレで成功・おむつ交換・便の様子など')
    nap_minutes = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='午睡（分）')
    medication_given = models.BooleanField(default=False, verbose_name='薬を飲ませた')
    medication_note = models.CharField(max_length=200, blank=True, verbose_name='服薬のメモ')
    mood = models.CharField(max_length=10, choices=MOOD_CHOICES, blank=True, verbose_name='機嫌・体調')
    note = models.TextField(blank=True, verbose_name='その他（けが・ひやりとしたこと・連絡）')
    handed_to = models.CharField(max_length=50, blank=True, verbose_name='引き渡した相手', help_text='母・父・祖母・送迎 など')
    handed_at = models.TimeField(null=True, blank=True, verbose_name='引き渡しの時刻')
    recorded_by = models.ForeignKey('accounts.StaffAccount', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '健康の記録'
        verbose_name_plural = '健康の記録'
        ordering = ['-date', 'beneficiary__last_name_kana']
        constraints = [models.UniqueConstraint(fields=['beneficiary', 'date'], name='uniq_health_log_day')]

    def __str__(self):
        return f'{self.date} {self.beneficiary} 健康の記録'

    @property
    def fever(self):
        return any(t is not None and float(t) >= self.FEVER for t in (self.temp_arrival, self.temp_other))

    @property
    def is_empty(self):
        return not any([self.temp_arrival, self.temp_other, self.meal, self.meal_note, self.urine is not None, self.stool is not None,
                        self.toilet_note, self.nap_minutes, self.medication_given, self.medication_note, self.mood, self.note,
                        self.handed_to, self.handed_at])

    @property
    def mood_icon(self):
        return self.MOOD_ICONS.get(self.mood, '')

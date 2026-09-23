"""
療育記録（発達支援ルーム　ゆあーず）。

紙の「療育記録」用紙をそのまま画面にしたもの。
- 利用者ごとに「留意点」（用紙の上の枠）
- 1回の療育ごとに 日付・時刻・担当・その日にやったこと ①〜⑤・記録の本文
印刷は用紙と同じ A4 縦・1枚に5回ぶん。
"""
from django.db import models

from beneficiaries.models import Beneficiary
from facilities.models import Facility

ACTIVITY_MAX = 5
CIRCLED = ['①', '②', '③', '④', '⑤']


class TherapyProfile(models.Model):
    """利用者ごとの留意点（用紙の上の枠）"""

    beneficiary = models.OneToOneField(Beneficiary, on_delete=models.CASCADE, related_name='therapy_profile',
                                       verbose_name='利用者')
    cautions = models.TextField(blank=True, verbose_name='留意点')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '療育の留意点'
        verbose_name_plural = '療育の留意点'

    def __str__(self):
        return f'{self.beneficiary.full_name} の留意点'


class TherapyRecord(models.Model):
    """1回の療育の記録"""

    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name='therapy_records')
    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.CASCADE, related_name='therapy_records',
                                    verbose_name='利用者')
    date = models.DateField(verbose_name='日付')
    time = models.TimeField(null=True, blank=True, verbose_name='時刻')
    staff = models.ForeignKey('accounts.StaffAccount', on_delete=models.SET_NULL, null=True, blank=True,
                              related_name='therapy_records', verbose_name='担当')
    staff_name = models.CharField(max_length=50, blank=True, verbose_name='担当（名前）',
                                  help_text='アカウントのない職員や、退職した職員の名前を残すとき')
    activities = models.JSONField(default=list, blank=True, verbose_name='やったこと（①〜⑤）')
    body = models.TextField(blank=True, verbose_name='記録')
    reservation = models.ForeignKey('reservations.Reservation', on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='therapy_records', verbose_name='もとになった予約')
    created_by = models.ForeignKey('accounts.StaffAccount', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '療育記録'
        verbose_name_plural = '療育記録'
        ordering = ['-date', '-time', '-pk']
        indexes = [models.Index(fields=['facility', 'date'])]

    def __str__(self):
        return f'{self.date} {self.beneficiary.full_name} 療育記録'

    @property
    def staff_label(self):
        if self.staff_id and self.staff is not None:
            return str(self.staff)
        return self.staff_name

    @property
    def activity_list(self):
        """['①ウレタン棒', '②アンパンマンブロック', ...]"""
        items = [a for a in (self.activities or []) if isinstance(a, str) and a.strip()]
        return [f'{CIRCLED[i]}{a.strip()}' for i, a in enumerate(items[:ACTIVITY_MAX])]

    @property
    def activity_text(self):
        return ' '.join(self.activity_list)

    @property
    def time_label(self):
        return f'{self.time.hour}時{self.time.minute:02d}分' if self.time else ''

    @property
    def date_label(self):
        from reservations.services import WEEK_JP
        return f'{self.date.year}年{self.date.month}月{self.date.day}日 {WEEK_JP[self.date.weekday()]}曜日'

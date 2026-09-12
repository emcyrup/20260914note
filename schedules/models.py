from django.db import models
from facilities.models import Facility
from beneficiaries.models import Beneficiary


class ScheduledVisit(models.Model):
    """
    利用者の来所予定を1日1レコードで管理する。
    「一括生成」機能では、利用者の登録曜日をもとに1ヶ月分をまとめて作成する。
    """

    STATUS_SCHEDULED = 'scheduled'
    STATUS_ATTENDED  = 'attended'
    STATUS_ABSENT    = 'absent'
    STATUS_TRANSFERRED = 'transferred'
    STATUS_CHOICES = [
        (STATUS_SCHEDULED,   '予定'),
        (STATUS_ATTENDED,    '来所'),
        (STATUS_ABSENT,      '欠席'),
        (STATUS_TRANSFERRED, '振替'),
    ]

    facility    = models.ForeignKey(Facility,    on_delete=models.PROTECT, verbose_name='施設')
    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.CASCADE,
                                    related_name='scheduled_visits', verbose_name='利用者')
    date        = models.DateField(verbose_name='来所日')
    status      = models.CharField(max_length=20, choices=STATUS_CHOICES,
                                   default=STATUS_SCHEDULED, verbose_name='状態')
    has_pickup  = models.BooleanField(default=False, verbose_name='送迎あり（迎え）')
    has_dropoff = models.BooleanField(default=False, verbose_name='送迎あり（送り）')
    notes       = models.TextField(blank=True, verbose_name='備考')
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = '来所予定'
        verbose_name_plural = '来所予定'
        ordering            = ['date', 'beneficiary__last_name_kana']
        unique_together = [['beneficiary', 'date']]

    def __str__(self):
        return f'{self.date} {self.beneficiary.full_name} ({self.get_status_display()})'

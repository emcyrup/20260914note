"""
議事録。会議・打ち合わせ・面談などで話したことを音声入力で残し、AI で見出しと箇条書きに整理する。
利用者（児童）には結びつけない。事業所ごとに新しい KEEP 件だけ残す。
"""
from django.db import models

from facilities.models import Facility

KEEP = 10   # 事業所ごとに残す件数（これを超えたら古いものから消す）


class Minutes(models.Model):
    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name='minutes')
    title = models.CharField(max_length=100, verbose_name='件名')
    held_on = models.DateField(verbose_name='日付')
    transcript = models.TextField(blank=True, verbose_name='話した内容（文字起こし・メモ）')
    summary = models.TextField(blank=True, verbose_name='議事録（整理したもの）')
    created_by = models.ForeignKey('accounts.StaffAccount', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+', verbose_name='作成者')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '議事録'
        verbose_name_plural = '議事録'
        ordering = ['-created_at', '-pk']

    def __str__(self):
        return f'{self.held_on} {self.title}'

    @classmethod
    def prune(cls, facility, keep=KEEP):
        """新しい keep 件を残して、古いものを消す。消した件数を返す"""
        old = list(cls.objects.filter(facility=facility).order_by('-created_at', '-pk').values_list('pk', flat=True)[keep:])
        if not old:
            return 0
        cls.objects.filter(pk__in=old).delete()
        return len(old)

"""
議事録。会議・打ち合わせ・面談などで話したことを音声入力で残し、AI で見出しと箇条書きに整理する。
利用者（児童）には結びつけない。件数の上限は設けず、すべて残す（消すのは職員が一覧から。2026-11-06 ゆあーず）。
"""
from django.db import models

from facilities.models import Facility

KEEP = 10   # 議事録の画面の右の「履歴」に出す件数（残す件数の上限ではない。全件は「一覧」で見る）
LIST_PER_PAGE = 50


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

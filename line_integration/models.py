"""
LINE連携のモデル定義
- LineDeliveryLog: LINE配信履歴（送信日時・結果・エラー内容）
"""

from django.db import models


class LineDeliveryLog(models.Model):
    """
    LINE配信履歴。
    保護者向けメッセージのLINE送信結果を記録する。
    失敗した場合に再送できるよう、エラー内容も保持する。
    """
    facility = models.ForeignKey(
        'facilities.Facility', on_delete=models.PROTECT, verbose_name='施設'
    )
    guardian = models.ForeignKey(
        'beneficiaries.Guardian', on_delete=models.SET_NULL,
        null=True, verbose_name='送信先保護者'
    )
    daily_record = models.ForeignKey(
        'records.DailyRecord', on_delete=models.SET_NULL,
        null=True, blank=True, verbose_name='関連日次記録'
    )
    content = models.TextField(verbose_name='送信内容')
    sent_at = models.DateTimeField(auto_now_add=True, verbose_name='送信日時')
    is_success = models.BooleanField(default=False, verbose_name='送信成功')
    error_message = models.TextField(blank=True, verbose_name='エラー内容')

    class Meta:
        verbose_name = 'LINE配信履歴'
        verbose_name_plural = 'LINE配信履歴'
        ordering = ['-sent_at']

    def __str__(self):
        status = '成功' if self.is_success else '失敗'
        return f'{self.sent_at.strftime("%Y-%m-%d %H:%M")} {self.guardian}（{status}）'

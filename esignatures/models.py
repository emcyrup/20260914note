"""
電子サイン記録のモデル定義
- EsignatureRecord: 署名者・日時・署名画像・対象記録を紐づけて保存する
"""

from django.db import models
from facilities.uploads import signature_upload_to


class EsignatureRecord(models.Model):
    """
    電子サイン記録。
    タブレットのタッチ描画で取得した署名を画像として保存する。
    「誰が・いつ・何に対してサインしたか」を記録する。
    """
    TARGET_TYPE_CHOICES = [
        ('daily_record', '日次記録'),
        ('support_plan', '個別支援計画'),
        ('monitoring',   'モニタリング'),
    ]

    facility       = models.ForeignKey('facilities.Facility', on_delete=models.PROTECT, verbose_name='施設')
    signer_name    = models.CharField(max_length=100, verbose_name='署名者氏名')
    relationship   = models.CharField(max_length=50, blank=True, verbose_name='続柄')
    signed_at      = models.DateTimeField(auto_now_add=True, verbose_name='署名日時')
    signature_image = models.ImageField(upload_to=signature_upload_to, verbose_name='署名画像')
    # 署名対象の種別とID（日次記録・支援計画・モニタリングに汎用対応）
    target_type    = models.CharField(max_length=30, choices=TARGET_TYPE_CHOICES, verbose_name='対象種別')
    target_id      = models.PositiveIntegerField(verbose_name='対象ID')
    created_by     = models.ForeignKey('accounts.StaffAccount', on_delete=models.SET_NULL,
                                       null=True, verbose_name='操作者')

    class Meta:
        verbose_name        = '電子サイン記録'
        verbose_name_plural = '電子サイン記録'
        ordering            = ['-signed_at']

    def __str__(self):
        return f'{self.signer_name}（{self.get_target_type_display()} ID:{self.target_id}）{self.signed_at.date()}'

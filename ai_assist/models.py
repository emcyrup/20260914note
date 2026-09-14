"""
AI支援：算定要件資料・加算提案・（チャットは状態を持たない）
"""
from django.db import models
from facilities.uploads import reference_doc_upload_to


class ReferenceDocument(models.Model):
    """加算提案・チャットが参照する算定要件資料（PDF）"""
    facility    = models.ForeignKey('facilities.Facility', on_delete=models.CASCADE,
                                    related_name='reference_documents', verbose_name='施設')
    title       = models.CharField(max_length=200, verbose_name='資料名')
    file        = models.FileField(upload_to=reference_doc_upload_to, verbose_name='PDFファイル')
    uploaded_by = models.ForeignKey('accounts.StaffAccount', on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='+', verbose_name='アップロード者')
    uploaded_at = models.DateTimeField(auto_now_add=True, verbose_name='アップロード日時')
    indexed_at  = models.DateTimeField(null=True, blank=True, verbose_name='ベクトル化日時')
    page_count  = models.PositiveIntegerField(default=0, verbose_name='ページ数')
    chunk_count = models.PositiveIntegerField(default=0, verbose_name='チャンク数')
    error       = models.TextField(blank=True, verbose_name='エラー')

    class Meta:
        verbose_name = '算定要件資料'
        verbose_name_plural = '算定要件資料'
        ordering = ['-uploaded_at']

    def __str__(self):
        return self.title

    @property
    def is_indexed(self):
        return self.indexed_at is not None


class DocumentChunk(models.Model):
    """資料を検索しやすい長さに分けた断片。terms は文字バイグラムの出現回数"""
    document = models.ForeignKey(ReferenceDocument, on_delete=models.CASCADE, related_name='chunks')
    index    = models.PositiveIntegerField()
    page     = models.PositiveIntegerField(default=1)
    text     = models.TextField()
    terms    = models.JSONField(default=dict)
    length   = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['document', 'index']

    def __str__(self):
        return f'{self.document.title} #{self.index}'


class AddonSuggestion(models.Model):
    """日誌の内容から AI が提案した加算"""
    STATUS_PENDING   = 'pending'
    STATUS_ADOPTED   = 'adopted'
    STATUS_DISMISSED = 'dismissed'
    STATUS_CHOICES = [
        (STATUS_PENDING,   '未対応'),
        (STATUS_ADOPTED,   '採用'),
        (STATUS_DISMISSED, '見送り'),
    ]

    facility     = models.ForeignKey('facilities.Facility', on_delete=models.CASCADE, verbose_name='施設')
    daily_record = models.ForeignKey('records.DailyRecord', on_delete=models.CASCADE,
                                     related_name='addon_suggestions', verbose_name='日誌')
    addon        = models.ForeignKey('facilities.AddonMaster', on_delete=models.CASCADE, verbose_name='加算')
    reason       = models.TextField(blank=True, verbose_name='提案理由')
    evidence     = models.CharField(max_length=200, blank=True, verbose_name='根拠資料')
    status       = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING, verbose_name='状態')
    created_at   = models.DateTimeField(auto_now_add=True)
    decided_at   = models.DateTimeField(null=True, blank=True)
    decided_by   = models.ForeignKey('accounts.StaffAccount', on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='+', verbose_name='判断した職員')

    class Meta:
        verbose_name = 'AI加算提案'
        verbose_name_plural = 'AI加算提案'
        unique_together = [['daily_record', 'addon']]
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.daily_record} → {self.addon.name}（{self.get_status_display()}）'

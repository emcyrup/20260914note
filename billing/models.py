"""
請求系のモデル定義

- BillingMatrixEntry  : 利用者×日付の請求対象状態（請求マトリックスの1セル）
- BillingMatrixAddon  : セルに適用された加算
- BillingRecord       : 確定した月次請求データ
- CopaymentManagement : 利用者負担上限額管理（月次）
- CopaymentOfficeRecord: 上限額管理における各事業所の利用実績
"""

from django.db import models


class BillingMatrixEntry(models.Model):
    """
    請求マトリックスの1セル（利用者×日付）。
    請求マトリックス画面でセルをタップして状態と加算を管理する。
    """
    STATUS_ATTENDED    = 'attended'
    STATUS_ABSENT      = 'absent'
    STATUS_TRANSFERRED = 'transferred'
    STATUS_CHOICES = [
        (STATUS_ATTENDED,    '利用'),
        (STATUS_ABSENT,      '欠席'),
        (STATUS_TRANSFERRED, '振替'),
    ]

    facility    = models.ForeignKey('facilities.Facility',    on_delete=models.PROTECT, verbose_name='施設')
    beneficiary = models.ForeignKey('beneficiaries.Beneficiary', on_delete=models.CASCADE, verbose_name='利用者')
    date        = models.DateField(verbose_name='日付')
    status      = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_ATTENDED, verbose_name='状態'
    )
    is_finalized = models.BooleanField(default=False, verbose_name='請求確定済み')
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = '請求マトリックスエントリー'
        verbose_name_plural = '請求マトリックスエントリー'
        unique_together     = [['facility', 'beneficiary', 'date']]
        ordering            = ['date', 'beneficiary__last_name_kana']

    def __str__(self):
        return f'{self.date} {self.beneficiary.full_name}（{self.get_status_display()}）'


class BillingMatrixAddon(models.Model):
    """
    請求マトリックスの1セルに適用された加算。
    手動追加・AI提案採用のどちらの場合もこのモデルに記録する。
    """
    entry      = models.ForeignKey(
        BillingMatrixEntry, on_delete=models.CASCADE,
        related_name='addons', verbose_name='請求エントリー'
    )
    addon      = models.ForeignKey(
        'facilities.AddonMaster', on_delete=models.PROTECT, verbose_name='加算'
    )
    is_applied = models.BooleanField(default=True, verbose_name='適用')

    class Meta:
        verbose_name        = '請求加算'
        verbose_name_plural = '請求加算'
        unique_together     = [['entry', 'addon']]

    def __str__(self):
        return f'{self.entry} - {self.addon.name}'


class BillingRecord(models.Model):
    """確定した月次請求データ。"""
    facility    = models.ForeignKey('facilities.Facility',    on_delete=models.PROTECT, verbose_name='施設')
    beneficiary = models.ForeignKey('beneficiaries.Beneficiary', on_delete=models.CASCADE, verbose_name='利用者')
    year_month  = models.CharField(max_length=7, verbose_name='対象年月（YYYY-MM）')
    total_units = models.IntegerField(default=0, verbose_name='合計単位数')
    copayment   = models.IntegerField(default=0, verbose_name='利用者負担額')
    is_finalized = models.BooleanField(default=False, verbose_name='確定済み')
    finalized_by = models.ForeignKey(
        'accounts.StaffAccount', on_delete=models.SET_NULL,
        null=True, blank=True, verbose_name='確定者'
    )
    finalized_at = models.DateTimeField(null=True, blank=True, verbose_name='確定日時')
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name        = '請求記録'
        verbose_name_plural = '請求記録'
        unique_together     = [['facility', 'beneficiary', 'year_month']]
        ordering            = ['-year_month']

    def __str__(self):
        return f'{self.year_month} {self.beneficiary.full_name} 請求'


class CopaymentManagement(models.Model):
    """
    利用者負担上限額管理（月次）。
    利用者が複数事業所を利用する場合に、上限月額を超えないよう各事業所の負担額を調整する。
    国保連請求様式第四に対応。
    """
    MANAGEMENT_RESULT_CHOICES = [
        ('1', '管理事業所が利用者負担額を充当したため、他事業所の徴収なし'),
        ('2', '利用者負担額の合算額が、負担上限月額以下のため、調整なし'),
        ('3', '利用者負担額の合算額が、負担上限月額を超過するため、調整あり'),
    ]

    facility    = models.ForeignKey('facilities.Facility',    on_delete=models.PROTECT, verbose_name='施設')
    beneficiary = models.ForeignKey('beneficiaries.Beneficiary', on_delete=models.CASCADE, verbose_name='利用者')
    year_month  = models.CharField(max_length=7, verbose_name='対象年月（YYYY-MM）')
    is_upper_limit_manager = models.BooleanField(default=True, verbose_name='当施設が上限額管理事業所')
    management_result = models.CharField(
        max_length=1, choices=MANAGEMENT_RESULT_CHOICES, default='2', verbose_name='管理結果'
    )
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = '上限額管理'
        verbose_name_plural = '上限額管理'
        unique_together     = [['facility', 'beneficiary', 'year_month']]
        ordering            = ['-year_month', 'beneficiary__last_name_kana']

    def __str__(self):
        return f'{self.year_month} {self.beneficiary.full_name} 上限額管理'

    @property
    def total_copayment(self):
        """全事業所の管理後利用者負担額の合計"""
        return sum(r.adjusted_copayment for r in self.office_records.all())

    @property
    def this_office_copayment(self):
        """当施設の管理後利用者負担額"""
        record = self.office_records.filter(is_this_office=True).first()
        return record.adjusted_copayment if record else 0


class CopaymentOfficeRecord(models.Model):
    """
    上限額管理における各事業所の利用実績。
    1件の CopaymentManagement に対し、当施設＋他事業所の分が紐づく。
    """
    management = models.ForeignKey(
        CopaymentManagement, on_delete=models.CASCADE,
        related_name='office_records', verbose_name='上限額管理'
    )
    is_this_office      = models.BooleanField(default=False, verbose_name='当施設')
    office_name         = models.CharField(max_length=200, verbose_name='事業所名')
    office_number       = models.CharField(max_length=20, blank=True, verbose_name='事業所番号')
    total_cost          = models.IntegerField(default=0, verbose_name='総費用額（円）')
    original_copayment  = models.IntegerField(default=0, verbose_name='利用者負担額（調整前）')
    adjusted_copayment  = models.IntegerField(default=0, verbose_name='利用者負担額（調整後）')

    class Meta:
        verbose_name        = '事業所別利用実績'
        verbose_name_plural = '事業所別利用実績'
        ordering            = ['-is_this_office', 'office_name']

    def __str__(self):
        return f'{self.management} - {self.office_name}'

from django.db import models
from facilities.models import Facility, SupportContentTag
from beneficiaries.models import Beneficiary
from accounts.models import StaffAccount


class ActivityTag(models.Model):
    """
    活動タグ。「体操」「工作」「外出」「おやつ」「食事」など施設独自のタグを管理する。
    日誌に複数付けることができ、アセスメント生成・実費請求集計にも使われる。
    """
    facility = models.ForeignKey(
        Facility, on_delete=models.CASCADE,
        related_name='activity_tags', verbose_name='施設'
    )
    name = models.CharField(max_length=30, verbose_name='タグ名')
    display_order = models.PositiveSmallIntegerField(default=0, verbose_name='表示順')
    is_active = models.BooleanField(default=True, verbose_name='有効')
    # 実費単価（おやつ・食事など保護者請求する場合に設定。0なら実費請求なし）
    price = models.PositiveIntegerField(
        default=0, verbose_name='実費単価（円/回）',
        help_text='おやつ・食事など実費請求する場合に1回あたりの金額を入力。不要なら0のまま。'
    )

    class Meta:
        verbose_name = '活動タグ'
        verbose_name_plural = '活動タグ'
        ordering = ['display_order', 'name']

    def __str__(self):
        return self.name


class DailyRecord(models.Model):
    """
    日次記録（日誌）。利用者1人につき1日1レコード。
    メモ書き（観察・支援・反応）をClaude APIで整えて記録文章を生成する。
    """

    STATUS_DRAFT     = 'draft'
    STATUS_CONFIRMED = 'confirmed'
    STATUS_CHOICES = [
        (STATUS_DRAFT,     '下書き'),
        (STATUS_CONFIRMED, '確定'),
    ]

    facility    = models.ForeignKey(Facility,    on_delete=models.PROTECT, verbose_name='施設')
    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.CASCADE,
                                    related_name='daily_records', verbose_name='利用者')
    date        = models.DateField(verbose_name='記録日')
    author      = models.ForeignKey(StaffAccount, on_delete=models.PROTECT,
                                    null=True, blank=True,
                                    related_name='authored_records', verbose_name='記録者')

    # 入退室時間（延長支援加算の計算に使用）
    entry_time = models.TimeField(null=True, blank=True, verbose_name='入室時間')
    exit_time  = models.TimeField(null=True, blank=True, verbose_name='退室時間')

    # 体調
    HEALTH_GOOD    = 'good'
    HEALTH_NORMAL  = 'normal'
    HEALTH_SPECIAL = 'special'
    HEALTH_CHOICES = [
        (HEALTH_GOOD,    '良好'),
        (HEALTH_NORMAL,  '普通'),
        (HEALTH_SPECIAL, '特記あり'),
    ]
    health_condition = models.CharField(
        max_length=10, choices=HEALTH_CHOICES, default=HEALTH_GOOD, verbose_name='体調'
    )
    health_note = models.TextField(blank=True, verbose_name='体調詳細')

    # 活動タグ（複数選択可）
    activity_tags = models.ManyToManyField(
        ActivityTag, blank=True, related_name='records', verbose_name='活動タグ'
    )

    # --- 活動・めあて・考察（紙の業務日誌の「活動：／めあて：」に相当） ---
    # 活動名を入力すると、めあて（観察の観点）と考察の下書きをAIで生成できる
    activity_name       = models.CharField(max_length=100, blank=True, verbose_name='活動')
    activity_aim        = models.TextField(blank=True, verbose_name='めあて・観点')
    activity_reflection = models.TextField(blank=True, verbose_name='考察')

    # 支援内容タグ（複数選択可・加算集計に使用）
    support_tags = models.ManyToManyField(
        SupportContentTag, blank=True, related_name='records', verbose_name='支援内容タグ'
    )

    # 関連する5領域（アセスメント・モニタリングで使用）
    domain_health_life        = models.BooleanField(default=False, verbose_name='健康・生活')
    domain_motor_sensory      = models.BooleanField(default=False, verbose_name='運動・感覚')
    domain_cognition_behavior = models.BooleanField(default=False, verbose_name='認知・行動')
    domain_language_comm      = models.BooleanField(default=False, verbose_name='言語・コミュニケーション')
    domain_social             = models.BooleanField(default=False, verbose_name='人間関係・社会性')

    # --- メモ書き欄（職員が手短に書く） ---
    # AIが整えるときはこのメモを使う
    observation_memo = models.TextField(blank=True, verbose_name='観察メモ')
    support_memo     = models.TextField(blank=True, verbose_name='支援メモ')
    reaction_memo    = models.TextField(blank=True, verbose_name='反応メモ')

    # --- 整えた記録文章（AI生成または手入力の最終版） ---
    observation_text = models.TextField(blank=True, verbose_name='観察記録')
    support_text     = models.TextField(blank=True, verbose_name='支援記録')
    reaction_text    = models.TextField(blank=True, verbose_name='反応記録')

    # 保護者向けメッセージ下書き（Phase 5 でLINE配信に使う）
    parent_message_draft = models.TextField(blank=True, verbose_name='保護者向けメッセージ下書き')

    status     = models.CharField(max_length=10, choices=STATUS_CHOICES,
                                  default=STATUS_DRAFT, verbose_name='状態')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = '日誌'
        verbose_name_plural = '日誌'
        ordering            = ['-date']
        # 1利用者1日1レコードのみ
        unique_together = [['beneficiary', 'date']]

    def __str__(self):
        return f'{self.date} {self.beneficiary.full_name} ({self.get_status_display()})'

    @property
    def has_ai_content(self):
        """AIで整えた文章が1つ以上あるか"""
        return bool(self.observation_text or self.support_text or self.reaction_text)


class DailyRecordPhoto(models.Model):
    """
    日誌に添付する写真。1件の日誌につき最大5枚まで保存できる。
    LINE送信時に保護者へ画像メッセージとして配信する。
    """
    facility     = models.ForeignKey(Facility, on_delete=models.CASCADE, verbose_name='施設')
    daily_record = models.ForeignKey(
        DailyRecord, on_delete=models.CASCADE,
        related_name='photos', verbose_name='日誌'
    )
    photo        = models.ImageField(
        upload_to='daily_record_photos/%Y/%m/', verbose_name='写真'
    )
    order        = models.PositiveSmallIntegerField(default=0, verbose_name='表示順')
    uploaded_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name        = '日誌写真'
        verbose_name_plural = '日誌写真'
        ordering            = ['order', 'uploaded_at']

    def __str__(self):
        return f'{self.daily_record} - 写真{self.order + 1}'


class StaffMemo(models.Model):
    """
    スタッフが業務中にダッシュボードからワンクリックで書き留めるメモ。
    タイムスタンプ・投稿者を自動記録する。音声入力にも対応。
    """
    facility   = models.ForeignKey(Facility, on_delete=models.CASCADE, verbose_name='施設')
    author     = models.ForeignKey(
        StaffAccount, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='memos', verbose_name='投稿者'
    )
    content    = models.TextField(verbose_name='内容')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='投稿日時')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新日時')

    class Meta:
        verbose_name        = 'スタッフメモ'
        verbose_name_plural = 'スタッフメモ'
        ordering            = ['-created_at']

    def __str__(self):
        return f'{self.created_at:%Y-%m-%d %H:%M} {self.author or "不明"}'

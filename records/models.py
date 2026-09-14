from django.db import models
from facilities.models import Facility, SupportContentTag
from beneficiaries.models import Beneficiary
from accounts.models import StaffAccount
from facilities.uploads import paper_scan_upload_to, photo_upload_to


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
    activity_aim        = models.TextField(blank=True, verbose_name='めあて')
    # 観察の観点。[{"text": "順番を待てるか", "answer": "yes" | "no" | null}, ...]
    activity_viewpoints = models.JSONField(default=list, blank=True, verbose_name='観点（はい／いいえ）')
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

    VIEWPOINT_ANSWERS = {'yes': 'はい', 'no': 'いいえ'}

    @staticmethod
    def clean_viewpoints(value):
        """画面から届いた観点（JSON文字列 or list）を正規化して返す"""
        import json as _json
        if isinstance(value, str):
            try:
                value = _json.loads(value) if value.strip() else []
            except ValueError:
                return []
        if not isinstance(value, list):
            return []
        out = []
        for v in value[:12]:
            if isinstance(v, str):
                v = {'text': v}
            if not isinstance(v, dict):
                continue
            text = str(v.get('text', '')).strip()[:100]
            if not text:
                continue
            answer = v.get('answer')
            out.append({'text': text, 'answer': answer if answer in ('yes', 'no') else None})
        return out

    @property
    def viewpoint_rows(self):
        """テンプレート用：観点と回答ラベル"""
        return [{'text': v.get('text', ''), 'answer': v.get('answer'),
                 'label': self.VIEWPOINT_ANSWERS.get(v.get('answer'), '未確認')}
                for v in (self.activity_viewpoints or [])]

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
        upload_to=photo_upload_to, verbose_name='写真'
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


class RecordTemplate(models.Model):
    """
    日誌テンプレート。ある利用者の日誌を元に作り、他の利用者の日誌入力に流用する。
    本文中の利用者名は保存時に {名前} に置き換え、使うときにその利用者の名前に戻す。
    """
    NAME_PLACEHOLDER = '{名前}'

    facility   = models.ForeignKey('facilities.Facility', on_delete=models.CASCADE,
                                   related_name='record_templates', verbose_name='施設')
    name       = models.CharField(max_length=100, verbose_name='テンプレート名')
    source_beneficiary = models.ForeignKey('beneficiaries.Beneficiary', on_delete=models.SET_NULL, null=True, blank=True,
                                           related_name='+', verbose_name='元になった利用者')
    created_by = models.ForeignKey(StaffAccount, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+', verbose_name='作成者')
    activity_name       = models.CharField(max_length=100, blank=True, verbose_name='活動')
    activity_aim        = models.TextField(blank=True, verbose_name='めあて')
    activity_viewpoints = models.JSONField(default=list, blank=True, verbose_name='観点')
    activity_reflection = models.TextField(blank=True, verbose_name='考察')
    observation_memo    = models.TextField(blank=True, verbose_name='メモ')
    observation_text    = models.TextField(blank=True, verbose_name='観察・活動内容')
    support_text        = models.TextField(blank=True, verbose_name='支援内容')
    reaction_text       = models.TextField(blank=True, verbose_name='本人の反応')
    parent_message_draft = models.TextField(blank=True, verbose_name='保護者向けメッセージ')
    domains       = models.JSONField(default=list, blank=True, verbose_name='5領域')
    activity_tags = models.ManyToManyField(ActivityTag, blank=True, verbose_name='活動タグ')
    support_tags  = models.ManyToManyField('facilities.SupportContentTag', blank=True, verbose_name='支援内容タグ')
    use_count  = models.PositiveIntegerField(default=0, verbose_name='使用回数')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    TEXT_FIELDS = ('activity_name', 'activity_aim', 'activity_reflection', 'observation_memo',
                   'observation_text', 'support_text', 'reaction_text', 'parent_message_draft')
    DOMAIN_KEYS = ('domain_health_life', 'domain_motor_sensory', 'domain_cognition_behavior',
                   'domain_language_comm', 'domain_social')

    class Meta:
        verbose_name = '日誌テンプレート'
        verbose_name_plural = '日誌テンプレート'
        ordering = ['-updated_at']

    def __str__(self):
        return self.name

    @staticmethod
    def anonymize(text, beneficiary):
        """利用者の名前（姓名・名・姓、かな）を {名前} に置き換える"""
        if not text:
            return text
        names = [beneficiary.full_name, beneficiary.full_name.replace(' ', ''),
                 beneficiary.first_name, beneficiary.last_name,
                 beneficiary.full_name_kana.replace(' ', ''), beneficiary.first_name_kana, beneficiary.last_name_kana]
        for n in sorted({n for n in names if n and len(n) >= 2}, key=len, reverse=True):
            text = text.replace(n, RecordTemplate.NAME_PLACEHOLDER)
        return text

    @classmethod
    def from_record(cls, record, name, user=None):
        b = record.beneficiary
        t = cls(facility=record.facility, name=name[:100] or (record.activity_name or f'{record.date} の日誌'),
                source_beneficiary=b, created_by=user)
        for f in cls.TEXT_FIELDS:
            setattr(t, f, cls.anonymize(getattr(record, f) or '', b))
        # 観点は文だけ引き継ぎ、はい／いいえは空に
        t.activity_viewpoints = [{'text': cls.anonymize(v.get('text', ''), b), 'answer': None}
                                 for v in (record.activity_viewpoints or []) if v.get('text')]
        t.domains = [k for k in cls.DOMAIN_KEYS if getattr(record, k, False)]
        t.save()
        t.activity_tags.set(record.activity_tags.all())
        t.support_tags.set(record.support_tags.all())
        return t

    def apply_for(self, beneficiary):
        """利用者に合わせて {名前} を戻した入力値を返す（画面のフォームに流し込む）"""
        name = beneficiary.first_name or beneficiary.full_name
        def fill(s):
            return (s or '').replace(self.NAME_PLACEHOLDER, name)
        data = {f: fill(getattr(self, f)) for f in self.TEXT_FIELDS}
        data['activity_viewpoints'] = [{'text': fill(v.get('text', '')), 'answer': None} for v in (self.activity_viewpoints or [])]
        data['domains'] = list(self.domains or [])
        data['activity_tag_ids'] = list(self.activity_tags.values_list('pk', flat=True))
        data['support_tag_ids'] = list(self.support_tags.values_list('pk', flat=True))
        data['name'] = self.name
        return data


class PaperScan(models.Model):
    """
    紙の日誌をカメラで撮った画像。AI が読み取った内容を職員が確認・修正してから日誌にする。
    """
    STATUS_PENDING   = 'pending'    # 取り込んだだけ
    STATUS_EXTRACTED = 'extracted'  # AI が読み取り済み（確認待ち）
    STATUS_IMPORTED  = 'imported'   # 日誌として保存済み
    STATUS_CHOICES = [
        (STATUS_PENDING,   '未読み取り'),
        (STATUS_EXTRACTED, '確認待ち'),
        (STATUS_IMPORTED,  '日誌にした'),
    ]

    facility    = models.ForeignKey(Facility, on_delete=models.CASCADE, verbose_name='施設')
    uploaded_by = models.ForeignKey(StaffAccount, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='取り込んだ人')
    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='paper_scans', verbose_name='利用者')
    image       = models.ImageField(upload_to=paper_scan_upload_to, verbose_name='画像')
    status      = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING, verbose_name='状態')
    extracted   = models.JSONField(default=dict, blank=True, verbose_name='読み取り結果')
    error       = models.TextField(blank=True, verbose_name='エラー')
    record      = models.ForeignKey(DailyRecord, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='paper_scans', verbose_name='作成した日誌')
    created_at  = models.DateTimeField(auto_now_add=True)
    extracted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = '紙の日誌（取り込み）'
        verbose_name_plural = '紙の日誌（取り込み）'
        ordering = ['-created_at']

    def __str__(self):
        return f'紙の日誌 #{self.pk}（{self.get_status_display()}）'

    @property
    def guessed_date(self):
        return (self.extracted or {}).get('date') or ''

    @property
    def guessed_name(self):
        return (self.extracted or {}).get('beneficiary_name') or ''

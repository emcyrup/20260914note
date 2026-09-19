"""
計画書中心の画面（シンプル）で使う記録。

- Interview   : 面談記録（期ごと・支援期間・面談内容の表・提供時間の時間割・完了ロック）
- ContactNote : 連絡帳（保護者ごとのやりとり。職員の記入は LINE 連携があれば送信し、保護者からの LINE も積む）
個別支援計画そのもの（原案・スタッフ会議・計画書・モニタリング）は support_plans のモデルをそのまま使う。
"""
from django.db import models
from django.utils import timezone


WEEKDAYS = [('mon', '月'), ('tue', '火'), ('wed', '水'), ('thu', '木'), ('fri', '金'), ('sat', '土'), ('sun', '日・祝')]
WEEKDAY_KEYS = [k for k, _ in WEEKDAYS]

# 面談内容の表の行（領域）
INTERVIEW_ROWS = [
    ('home',   '家庭での様子'),
    ('school', '学校等での様子'),
    ('social', '対人など協調性'),
]

# 目標（支援内容）の支援提供種別
GOAL_CATEGORIES = [
    ('self',       '本人支援'),
    ('family',     '家族支援'),
    ('transition', '移行支援'),
    ('community',  '地域支援・地域連携'),
]
GOAL_CATEGORY_LABELS = dict(GOAL_CATEGORIES)

# 目標の項目と印刷できる文字数（計画書の様式に合わせる）
GOAL_FIELDS = [
    ('staff',        '担当者 提供機関(施設)', 70),
    ('notes',        '留意事項・算定する加算', 210),
    ('content',      '短期目標(課題ニーズ)', 210),
    ('target',       '到達目標', 210),
    ('support',      '具体的な支援内容', 210),
    ('timing',       '見込み達成時期', 12),
    ('eval_timing',  '評価時期', 12),
]
GOAL_LIMITS = {k: n for k, _, n in GOAL_FIELDS}
DOMAINS = [
    ('health',    '健康・生活'),
    ('motor',     '運動・感覚'),
    ('cognition', '認知・行動'),
    ('language',  '言語・コミュニケーション'),
    ('social',    '人間関係・社会性'),
]
DOMAIN_LABELS = dict(DOMAINS)


def _minutes(start, end):
    """'15:00','17:30' → 150。どちらか空なら 0"""
    try:
        sh, sm = (int(x) for x in start.split(':')[:2])
        eh, em = (int(x) for x in end.split(':')[:2])
    except (ValueError, AttributeError):
        return 0
    n = (eh * 60 + em) - (sh * 60 + sm)
    return n if n > 0 else 0


class Interview(models.Model):
    """面談記録（1つの計画＝1期につき1件）"""
    plan = models.OneToOneField('support_plans.SupportPlan', on_delete=models.CASCADE, related_name='interview',
                                verbose_name='計画')
    # これからの支援期間
    period_start = models.DateField(null=True, blank=True, verbose_name='支援期間（開始）')
    period_end   = models.DateField(null=True, blank=True, verbose_name='支援期間（終了）')
    # 作成
    interview_date = models.DateField(null=True, blank=True, verbose_name='面談実施日')
    created_date   = models.DateField(null=True, blank=True, verbose_name='作成日')
    author = models.ForeignKey('accounts.StaffAccount', on_delete=models.SET_NULL, null=True, blank=True,
                               related_name='+', verbose_name='作成者（児童発達支援管理責任者）')
    participants = models.ManyToManyField('accounts.StaffAccount', blank=True, related_name='+',
                                          verbose_name='当面談に参加したスタッフ')
    # 面談内容（領域 × 親御様からの現状報告／スタッフからの支援策など決定事項）
    home_parent   = models.TextField(blank=True, verbose_name='家庭での様子（親御様から）')
    home_staff    = models.TextField(blank=True, verbose_name='家庭での様子（スタッフの支援策）')
    school_parent = models.TextField(blank=True, verbose_name='学校等での様子（親御様から）')
    school_staff  = models.TextField(blank=True, verbose_name='学校等での様子（スタッフの支援策）')
    social_parent = models.TextField(blank=True, verbose_name='対人など協調性（親御様から）')
    social_staff  = models.TextField(blank=True, verbose_name='対人など協調性（スタッフの支援策）')
    future_wishes = models.TextField(blank=True, verbose_name='将来の展望・親御様からの要望')
    other_wishes  = models.TextField(blank=True, verbose_name='その他施設への親御様からの要望')
    # 提供時間と特記事項（曜日ごと）
    # {"mon": {"start": "15:00", "end": "17:30", "pickup": "", "dropoff": "",
    #          "ext_before_start": "", "ext_before_end": "", "ext_after_start": "", "ext_after_end": ""}, ...}
    schedule = models.JSONField(default=dict, blank=True, verbose_name='提供時間（曜日ごと）')
    notes = models.TextField(blank=True, verbose_name='特記事項')
    extension_reason = models.TextField(blank=True, verbose_name='延長を必要とする理由')
    # 完了（編集ロック）
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name='完了日時')
    completed_by = models.ForeignKey('accounts.StaffAccount', on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='+', verbose_name='完了者')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '面談記録'
        verbose_name_plural = '面談記録'

    def __str__(self):
        return f'{self.plan} 面談記録'

    @property
    def is_completed(self):
        return self.completed_at is not None

    def missing_for_completion(self):
        """完了に必要な項目のうち、まだ入っていないもの"""
        missing = []
        if not (self.period_start and self.period_end):
            missing.append('支援期間')
        if not self.interview_date:
            missing.append('面談実施日')
        if not self.author_id:
            missing.append('作成者')
        if not self.created_date:
            missing.append('作成日')
        if self.pk and not self.participants.exists():
            missing.append('参加スタッフ')
        return missing

    def complete(self, user):
        self.completed_at = timezone.now()
        self.completed_by = user
        self.save(update_fields=['completed_at', 'completed_by', 'updated_at'])

    def reopen(self):
        self.completed_at = None
        self.completed_by = None
        self.save(update_fields=['completed_at', 'completed_by', 'updated_at'])

    def interview_rows(self):
        return [{'key': k, 'label': label, 'parent': getattr(self, f'{k}_parent'), 'staff': getattr(self, f'{k}_staff')}
                for k, label in INTERVIEW_ROWS]

    def schedule_rows(self):
        """テンプレート用：曜日ごとの提供時間（分数つき）"""
        rows = []
        for key, label in WEEKDAYS:
            d = (self.schedule or {}).get(key, {}) or {}
            rows.append({
                'key': key, 'label': label,
                'start': d.get('start', ''), 'end': d.get('end', ''), 'minutes': _minutes(d.get('start', ''), d.get('end', '')),
                'pickup': d.get('pickup', ''), 'dropoff': d.get('dropoff', ''),
                'ext_before_start': d.get('ext_before_start', ''), 'ext_before_end': d.get('ext_before_end', ''),
                'ext_after_start': d.get('ext_after_start', ''), 'ext_after_end': d.get('ext_after_end', ''),
                'ext_minutes': _minutes(d.get('ext_before_start', ''), d.get('ext_before_end', ''))
                               + _minutes(d.get('ext_after_start', ''), d.get('ext_after_end', '')),
            })
        return rows

    @staticmethod
    def clean_schedule(post):
        """画面から届いた曜日ごとの入力を dict にする（入力があった曜日だけ）"""
        out = {}
        for key in WEEKDAY_KEYS:
            d = {}
            for f in ('start', 'end', 'pickup', 'dropoff', 'ext_before_start', 'ext_before_end', 'ext_after_start', 'ext_after_end'):
                v = (post.get(f'sched_{key}_{f}') or '').strip()[:100]
                if v:
                    d[f] = v
            if d:
                out[key] = d
        return out


class ContactNote(models.Model):
    """連絡帳の1件（保護者ごとのやりとり）"""
    FROM_STAFF = 'staff'
    FROM_GUARDIAN = 'guardian'
    FROM_CHOICES = [(FROM_STAFF, '施設から'), (FROM_GUARDIAN, '保護者から')]

    facility = models.ForeignKey('facilities.Facility', on_delete=models.CASCADE, verbose_name='施設')
    guardian = models.ForeignKey('beneficiaries.Guardian', on_delete=models.CASCADE, related_name='contact_notes',
                                 verbose_name='保護者')
    sender = models.CharField(max_length=10, choices=FROM_CHOICES, default=FROM_STAFF, verbose_name='送信者')
    author = models.ForeignKey('accounts.StaffAccount', on_delete=models.SET_NULL, null=True, blank=True,
                               related_name='+', verbose_name='記入した職員')
    body = models.TextField(verbose_name='内容')
    line_sent = models.BooleanField(default=False, verbose_name='LINEで送った')
    line_error = models.CharField(max_length=200, blank=True, verbose_name='LINE送信エラー')
    is_read = models.BooleanField(default=True, verbose_name='既読（保護者からの分）')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='日時')

    class Meta:
        verbose_name = '連絡帳'
        verbose_name_plural = '連絡帳'
        ordering = ['created_at', 'pk']
        indexes = [models.Index(fields=['facility', 'guardian', 'created_at'])]

    def __str__(self):
        return f'{self.guardian} {self.get_sender_display()} {self.created_at:%Y-%m-%d %H:%M}'

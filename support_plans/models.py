"""
個別支援計画（5ステップ制）

  1. アセスメント（情報収集・面談）
  2. 個別支援計画（原案）の作成
  3. 担当者会議の開催（個別支援会議）
  4. 説明・同意・交付
  5. モニタリング（実施状況の把握・見直し）

各ステップは前のステップが「完了」しないと開けない。
どのステップに何が残っているかは requirements() で判定し、画面のチェックリストに出す。
"""
from datetime import date

from django.db import models
from django.utils import timezone


class SupportPlan(models.Model):
    STEP_ASSESSMENT = 1
    STEP_DRAFT      = 2
    STEP_MEETING    = 3
    STEP_CONSENT    = 4
    STEP_MONITORING = 5
    STEPS = [
        (STEP_ASSESSMENT, 'アセスメント'),
        (STEP_DRAFT,      '計画（原案）の作成'),
        (STEP_MEETING,    '担当者会議'),
        (STEP_CONSENT,    '説明・同意・交付'),
        (STEP_MONITORING, 'モニタリング'),
    ]
    STEP_DESCRIPTIONS = {
        STEP_ASSESSMENT: '利用者本人や家族と面談し、心身の状況・環境・希望する生活を把握する',
        STEP_DRAFT:      'アセスメントをもとに支援の方針と長期・短期目標、達成時期と具体的な支援内容を盛り込む',
        STEP_MEETING:    '支援に関わる担当者と、原則として本人を集めて原案への意見を求め、必要に応じて修正する',
        STEP_CONSENT:    '計画を分かりやすく説明し、文書で同意（署名）をもらい、本人と相談支援事業所に交付する',
        STEP_MONITORING: 'サービス開始後も定期的に面談し、計画通りか・目標が達成できているかを確認する（3〜6か月に1回以上）',
    }
    STEP_ICONS = {
        STEP_ASSESSMENT: 'bi-clipboard2-check',
        STEP_DRAFT:      'bi-file-earmark-text',
        STEP_MEETING:    'bi-people',
        STEP_CONSENT:    'bi-pen',
        STEP_MONITORING: 'bi-graph-up',
    }

    STATUS_IN_PROGRESS = 'in_progress'   # 作成中（ステップ1〜4）
    STATUS_ACTIVE      = 'active'        # サービス提供中（ステップ5）
    STATUS_CLOSED      = 'closed'        # 次の計画に引き継いで終了
    STATUS_CHOICES = [
        (STATUS_IN_PROGRESS, '作成中'),
        (STATUS_ACTIVE,      '実施中'),
        (STATUS_CLOSED,      '終了'),
    ]

    facility    = models.ForeignKey('facilities.Facility', on_delete=models.PROTECT,
                                    related_name='support_plans', verbose_name='施設')
    beneficiary = models.ForeignKey('beneficiaries.Beneficiary', on_delete=models.PROTECT,
                                    related_name='support_plans', verbose_name='利用者')
    title       = models.CharField(max_length=100, verbose_name='計画名')
    current_step = models.PositiveSmallIntegerField(choices=STEPS, default=STEP_ASSESSMENT,
                                                    verbose_name='現在のステップ')
    status      = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_IN_PROGRESS,
                                   verbose_name='状態')
    manager     = models.ForeignKey('accounts.StaffAccount', on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='managed_support_plans', verbose_name='児童発達支援管理責任者')
    predecessor = models.OneToOneField('self', on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='successor', verbose_name='前の計画')
    created_by  = models.ForeignKey('accounts.StaffAccount', on_delete=models.SET_NULL, null=True,
                                    related_name='+', verbose_name='作成者')
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)
    closed_at   = models.DateTimeField(null=True, blank=True, verbose_name='終了日時')

    class Meta:
        verbose_name = '個別支援計画'
        verbose_name_plural = '個別支援計画'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.beneficiary} {self.title}'

    # ---- ステップの取得 ------------------------------------------------
    def get_step(self, n):
        """ステップ番号 → 対応する記録オブジェクト（無ければ作る）"""
        if n == self.STEP_ASSESSMENT:
            return Assessment.objects.get_or_create(plan=self)[0]
        if n == self.STEP_DRAFT:
            return PlanDraft.objects.get_or_create(plan=self)[0]
        if n == self.STEP_MEETING:
            return StaffMeeting.objects.get_or_create(plan=self)[0]
        if n == self.STEP_CONSENT:
            return ConsentDelivery.objects.get_or_create(plan=self)[0]
        if n == self.STEP_MONITORING:
            return Monitoring.objects.get_or_create(plan=self)[0]
        raise ValueError(n)

    # ---- 進捗 ---------------------------------------------------------
    def is_step_done(self, n):
        if n == self.STEP_MONITORING:
            return False  # モニタリングは継続するステップ
        return n < self.current_step

    def can_open_step(self, n):
        return 1 <= n <= self.current_step

    def can_reopen_step(self, n):
        """直前に完了したステップだけ「完了を取り消して修正」できる"""
        if n != self.current_step - 1 or self.status == self.STATUS_CLOSED:
            return False
        if self.current_step == self.STEP_MONITORING and self.monitoring_records.exists():
            return False
        return True

    def step_progress(self):
        """画面用：全ステップの状態と残り項目"""
        rows = []
        for n, label in self.STEPS:
            step = self.get_step(n)
            reqs = step.requirements()
            remaining = [r for r in reqs if not r['done']]
            if self.is_step_done(n):
                state = 'done'
            elif n == self.current_step:
                state = 'current'
            else:
                state = 'locked'
            rows.append({
                'n': n, 'label': label, 'state': state,
                'description': self.STEP_DESCRIPTIONS[n], 'icon': self.STEP_ICONS[n],
                'requirements': reqs, 'remaining': remaining,
                'done_count': len(reqs) - len(remaining), 'total': len(reqs),
                'completed_at': step.completed_at, 'completed_by': step.completed_by,
                'can_reopen': self.can_reopen_step(n),
            })
        return rows

    def complete_step(self, n, user):
        """要件を満たしていればステップを完了し、次へ進める。戻り値は残り項目のリスト"""
        if n != self.current_step:
            raise ValueError('現在のステップ以外は完了できません')
        step = self.get_step(n)
        remaining = [r['label'] for r in step.requirements() if not r['done']]
        if remaining:
            return remaining
        step.completed_at = timezone.now()
        step.completed_by = user
        step.save(update_fields=['completed_at', 'completed_by'])
        if n < self.STEP_MONITORING:
            self.current_step = n + 1
            if self.current_step == self.STEP_MONITORING:
                self.status = self.STATUS_ACTIVE
                # 前の計画は新しい計画のサービス開始で終了
                if self.predecessor_id and self.predecessor.status != self.STATUS_CLOSED:
                    self.predecessor.status = self.STATUS_CLOSED
                    self.predecessor.closed_at = timezone.now()
                    self.predecessor.save(update_fields=['status', 'closed_at'])
            self.save(update_fields=['current_step', 'status', 'updated_at'])
        return []

    def reopen_step(self, n):
        if not self.can_reopen_step(n):
            raise ValueError('このステップは取り消せません')
        step = self.get_step(n)
        step.completed_at = None
        step.completed_by = None
        step.save(update_fields=['completed_at', 'completed_by'])
        self.current_step = n
        if self.status == self.STATUS_ACTIVE:
            self.status = self.STATUS_IN_PROGRESS
        self.save(update_fields=['current_step', 'status', 'updated_at'])

    @property
    def progress_percent(self):
        done = self.current_step - 1
        if self.current_step == self.STEP_MONITORING and self.monitoring_records.exists():
            done = 5
        return int(done / 5 * 100)

    @property
    def service_start_date(self):
        c = getattr(self, 'consent', None)
        return c.service_start_date if c else None

    @property
    def next_monitoring_due(self):
        """次回モニタリング期限（最後のモニタリングの next_due、無ければサービス開始から間隔分）"""
        last = self.monitoring_records.order_by('-date').first()
        if last and last.next_due:
            return last.next_due
        start = self.service_start_date
        if start:
            m = self.get_step(self.STEP_MONITORING)
            return _add_months(start, m.interval_months)
        return None

    @property
    def monitoring_overdue(self):
        due = self.next_monitoring_due
        return bool(due and self.status == self.STATUS_ACTIVE and due < date.today())


def _add_months(d, months):
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                      31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)


class StepBase(models.Model):
    """各ステップ共通：完了日時と完了者"""
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name='完了日時')
    completed_by = models.ForeignKey('accounts.StaffAccount', on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='+', verbose_name='完了者')

    class Meta:
        abstract = True

    @property
    def is_completed(self):
        return self.completed_at is not None

    def requirements(self):
        return []


class Assessment(StepBase):
    """ステップ1：アセスメント（情報収集・面談）"""
    plan = models.OneToOneField(SupportPlan, on_delete=models.CASCADE, related_name='assessment')
    interview_date   = models.DateField(null=True, blank=True, verbose_name='面談日')
    interviewed_with = models.CharField(max_length=100, blank=True, verbose_name='面談相手',
                                        help_text='例：本人・母')
    interviewer      = models.ForeignKey('accounts.StaffAccount', on_delete=models.SET_NULL, null=True, blank=True,
                                         related_name='+', verbose_name='面談担当')
    condition   = models.TextField(blank=True, verbose_name='心身の状況',
                                   help_text='発達・健康面・得意なこと／苦手なこと・コミュニケーションの特徴など')
    environment = models.TextField(blank=True, verbose_name='置かれている環境',
                                   help_text='家庭・学校・関係機関・生活リズムなど')
    wishes      = models.TextField(blank=True, verbose_name='希望する生活',
                                   help_text='本人の希望、家族の希望')
    notes       = models.TextField(blank=True, verbose_name='その他の情報')

    class Meta:
        verbose_name = 'アセスメント'
        verbose_name_plural = 'アセスメント'

    def requirements(self):
        return [
            {'label': '面談日を記録する',           'done': bool(self.interview_date)},
            {'label': '面談相手（本人・家族）を記録する', 'done': bool(self.interviewed_with.strip())},
            {'label': '心身の状況を記入する',        'done': bool(self.condition.strip())},
            {'label': '置かれている環境を記入する',   'done': bool(self.environment.strip())},
            {'label': '希望する生活を記入する',       'done': bool(self.wishes.strip())},
        ]


class PlanDraft(StepBase):
    """ステップ2：個別支援計画（原案）"""
    plan = models.OneToOneField(SupportPlan, on_delete=models.CASCADE, related_name='draft')
    period_start = models.DateField(null=True, blank=True, verbose_name='計画期間（開始）')
    period_end   = models.DateField(null=True, blank=True, verbose_name='計画期間（終了）')
    policy       = models.TextField(blank=True, verbose_name='総合的な支援の方針')
    family_wishes = models.TextField(blank=True, verbose_name='本人・家族の意向（計画書に記載）')
    notes        = models.TextField(blank=True, verbose_name='留意事項')

    class Meta:
        verbose_name = '計画（原案）'
        verbose_name_plural = '計画（原案）'

    def requirements(self):
        goals = list(self.plan.goals.all())
        longs  = [g for g in goals if g.goal_type == PlanGoal.TYPE_LONG]
        shorts = [g for g in goals if g.goal_type == PlanGoal.TYPE_SHORT]
        return [
            {'label': '計画期間を設定する',
             'done': bool(self.period_start and self.period_end and self.period_start <= self.period_end)},
            {'label': '総合的な支援の方針を記入する', 'done': bool(self.policy.strip())},
            {'label': '長期目標を1つ以上登録する（達成時期つき）',
             'done': any(g.target_date for g in longs)},
            {'label': '短期目標を1つ以上登録する（達成時期・具体的な支援内容つき）',
             'done': any(g.target_date and g.support_content.strip() for g in shorts)},
        ]


class PlanGoal(models.Model):
    """計画の目標（長期・短期）。短期目標には具体的な支援内容を持たせる"""
    TYPE_LONG  = 'long'
    TYPE_SHORT = 'short'
    TYPE_CHOICES = [(TYPE_LONG, '長期目標'), (TYPE_SHORT, '短期目標')]

    plan        = models.ForeignKey(SupportPlan, on_delete=models.CASCADE, related_name='goals')
    goal_type   = models.CharField(max_length=10, choices=TYPE_CHOICES, default=TYPE_SHORT, verbose_name='区分')
    content     = models.CharField(max_length=200, verbose_name='目標')
    target_date = models.DateField(null=True, blank=True, verbose_name='達成時期')
    support_content = models.TextField(blank=True, verbose_name='具体的な支援内容',
                                       help_text='短期目標では必須。誰が・いつ・どのように支援するか')
    frequency   = models.CharField(max_length=100, blank=True, verbose_name='頻度・時間',
                                   help_text='例：週3回・活動の前半30分')
    order       = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['goal_type', 'order', 'pk']
        verbose_name = '目標'
        verbose_name_plural = '目標'

    def __str__(self):
        return f'{self.get_goal_type_display()}：{self.content}'


class StaffMeeting(StepBase):
    """ステップ3：担当者会議（個別支援会議）"""
    plan = models.OneToOneField(SupportPlan, on_delete=models.CASCADE, related_name='meeting')
    meeting_date = models.DateField(null=True, blank=True, verbose_name='開催日')
    attendees    = models.TextField(blank=True, verbose_name='出席者', help_text='職員・関係機関など')
    beneficiary_attended = models.BooleanField(default=False, verbose_name='本人が出席した')
    guardian_attended    = models.BooleanField(default=False, verbose_name='保護者が出席した')
    absence_reason = models.CharField(max_length=200, blank=True, verbose_name='本人が出席しなかった理由',
                                      help_text='原則として本人の出席が必要。欠席の場合は理由を記録する')
    opinions       = models.TextField(blank=True, verbose_name='原案への意見・協議内容')
    revised        = models.BooleanField(default=False, verbose_name='原案を修正した')
    revision_summary = models.TextField(blank=True, verbose_name='修正内容')

    class Meta:
        verbose_name = '担当者会議'
        verbose_name_plural = '担当者会議'

    def requirements(self):
        return [
            {'label': '開催日を記録する',                'done': bool(self.meeting_date)},
            {'label': '出席者を記録する',                'done': bool(self.attendees.strip())},
            {'label': '本人の出席（欠席の場合は理由）を記録する',
             'done': self.beneficiary_attended or bool(self.absence_reason.strip())},
            {'label': '原案への意見・協議内容を記録する',  'done': bool(self.opinions.strip())},
            {'label': '修正の有無を記録する（修正した場合は内容も）',
             'done': (not self.revised) or bool(self.revision_summary.strip())},
        ]


class ConsentDelivery(StepBase):
    """ステップ4：説明・同意・交付"""
    METHOD_ESIGN = 'esign'
    METHOD_PAPER = 'paper'
    METHOD_CHOICES = [(METHOD_ESIGN, '電子サイン'), (METHOD_PAPER, '書面（署名・押印）')]

    plan = models.OneToOneField(SupportPlan, on_delete=models.CASCADE, related_name='consent')
    explained_date = models.DateField(null=True, blank=True, verbose_name='説明日')
    explained_to   = models.CharField(max_length=100, blank=True, verbose_name='説明した相手', help_text='例：本人・母')
    explained_by   = models.ForeignKey('accounts.StaffAccount', on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='+', verbose_name='説明した職員')
    consent_method = models.CharField(max_length=10, choices=METHOD_CHOICES, default=METHOD_ESIGN, verbose_name='同意の方法')
    consent_date   = models.DateField(null=True, blank=True, verbose_name='同意日（書面の場合）')
    consent_signer = models.CharField(max_length=100, blank=True, verbose_name='署名者（書面の場合）')
    delivered_to_user_date   = models.DateField(null=True, blank=True, verbose_name='本人・家族への交付日')
    delivered_to_office_date = models.DateField(null=True, blank=True, verbose_name='相談支援事業所への交付日')
    consultation_office = models.CharField(max_length=100, blank=True, verbose_name='相談支援事業所名')
    service_start_date  = models.DateField(null=True, blank=True, verbose_name='サービス開始日')

    class Meta:
        verbose_name = '説明・同意・交付'
        verbose_name_plural = '説明・同意・交付'

    def has_esignature(self):
        from esignatures.models import EsignatureRecord
        return EsignatureRecord.objects.filter(target_type='support_plan', target_id=self.plan_id).exists()

    def consent_done(self):
        if self.consent_method == self.METHOD_ESIGN:
            return self.has_esignature()
        return bool(self.consent_date and self.consent_signer.strip())

    def requirements(self):
        return [
            {'label': '説明日・説明した相手を記録する',
             'done': bool(self.explained_date and self.explained_to.strip())},
            {'label': '文書で同意をもらう（電子サイン、または書面の署名者と同意日）',
             'done': self.consent_done()},
            {'label': '本人・家族へ計画書を交付する',        'done': bool(self.delivered_to_user_date)},
            {'label': '相談支援事業所へ計画書を交付する',    'done': bool(self.delivered_to_office_date)},
            {'label': 'サービス開始日を設定する',            'done': bool(self.service_start_date)},
        ]


class Monitoring(StepBase):
    """ステップ5：モニタリングの設定（記録は MonitoringRecord）"""
    plan = models.OneToOneField(SupportPlan, on_delete=models.CASCADE, related_name='monitoring')
    interval_months = models.PositiveSmallIntegerField(default=3, verbose_name='モニタリング間隔（か月）',
                                                       help_text='サービスの種類に応じて3〜6か月に1回以上')

    class Meta:
        verbose_name = 'モニタリング設定'
        verbose_name_plural = 'モニタリング設定'

    def requirements(self):
        plan = self.plan
        records = plan.monitoring_records.all()
        due = plan.next_monitoring_due
        return [
            {'label': '初回のモニタリング（面談）を記録する', 'done': records.exists()},
            {'label': '次回期限までにモニタリングを行う',
             'done': bool(records.exists() and due and due >= date.today())},
        ]


class MonitoringRecord(models.Model):
    ACHIEVEMENT_CHOICES = [
        ('achieved',    '達成'),
        ('progressing', '順調'),
        ('partial',     '一部達成'),
        ('not_yet',     '未達成'),
    ]
    plan = models.ForeignKey(SupportPlan, on_delete=models.CASCADE, related_name='monitoring_records')
    date = models.DateField(verbose_name='実施日')
    interviewed_with = models.CharField(max_length=100, blank=True, verbose_name='面談相手', help_text='例：本人・母')
    conducted_by = models.ForeignKey('accounts.StaffAccount', on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='+', verbose_name='担当職員')
    implementation = models.TextField(verbose_name='計画通りに支援できているか',
                                      help_text='支援内容の実施状況')
    achievement    = models.CharField(max_length=20, choices=ACHIEVEMENT_CHOICES, default='progressing',
                                      verbose_name='目標の達成状況')
    achievement_detail = models.TextField(blank=True, verbose_name='達成状況の詳細・本人の変化')
    review_needed = models.BooleanField(default=False, verbose_name='計画の見直しが必要')
    review_reason = models.TextField(blank=True, verbose_name='見直しが必要な理由・次の計画への課題')
    next_due = models.DateField(null=True, blank=True, verbose_name='次回モニタリング期限')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-pk']
        verbose_name = 'モニタリング記録'
        verbose_name_plural = 'モニタリング記録'

    def __str__(self):
        return f'{self.plan} モニタリング {self.date}'

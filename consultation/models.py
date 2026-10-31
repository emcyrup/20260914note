"""
相談支援（計画相談支援・障害児相談支援）の書類：申請者の現状（基本情報）・サービス等利用計画／障害児支援利用計画（様式2-1）・週間計画表（様式2-2）。
1 件の ConsultationPlan に 3 枚ぶんを持つ。施設設定で「相談支援の計画を使う」にした事業所だけ。
児童発達支援の個別支援計画（support_plans）は、この計画の方針・目標に沿わせる（support_plans/material.py が参照）。
"""
from django.db import models

# 本児の様子の見出し（申請者の現状の「本児の様子」）
CHILD_STATE_KEYS = [
    ('meal', '食事'), ('toilet', '排泄'), ('dress', '着脱・整容'), ('language', '言語'), ('study', '学習'),
    ('motor', '運動'), ('social', '社会性'), ('health', '健康面'),
]
WEEKDAYS = [('mon', '月'), ('tue', '火'), ('wed', '水'), ('thu', '木'), ('fri', '金'), ('sat', '土'), ('sun', '日・祝')]
# 計画の表の列（様式2-1）
ROW_KEYS = [
    ('priority', '優先順位'), ('issue', '解決すべき課題（本人のニーズ）'), ('goal', '支援目標'), ('timing', '達成時期'),
    ('service', '福祉サービス等（種類・内容・量（頻度・時間））'), ('provider', '提供事業者名（担当者名・電話）'),
    ('role', '課題解決のための本人の役割'), ('eval_timing', '評価時期'), ('notes', 'その他留意事項'),
]
FAMILY_KEYS = [('relation', '続柄'), ('name', '氏名'), ('age', '年齢'), ('together', '同居・別居'), ('job', '職業・学校'), ('notes', '特記事項')]
SERVICE_KEYS = [('name', '名称'), ('provider', '提供機関・提供者'), ('content', '支援内容'), ('frequency', '頻度'), ('notes', '備考')]


class ConsultationPlan(models.Model):
    STATUS_DRAFT = 'draft'
    STATUS_ACTIVE = 'active'
    STATUS_CLOSED = 'closed'
    STATUS_CHOICES = [(STATUS_DRAFT, '作成中'), (STATUS_ACTIVE, '利用者同意ずみ'), (STATUS_CLOSED, '終了')]

    facility = models.ForeignKey('facilities.Facility', on_delete=models.CASCADE, related_name='consultation_plans', verbose_name='施設')
    beneficiary = models.ForeignKey('beneficiaries.Beneficiary', on_delete=models.CASCADE, related_name='consultation_plans', verbose_name='利用者')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_DRAFT, verbose_name='状態')
    # 頭の欄
    office_name = models.CharField(max_length=100, blank=True, verbose_name='相談支援事業者名')
    planner = models.CharField(max_length=50, blank=True, verbose_name='計画作成担当者')
    plan_date = models.DateField(null=True, blank=True, verbose_name='計画作成日')
    monitoring_period = models.CharField(max_length=100, blank=True, verbose_name='モニタリング期間（開始年月）')
    support_class = models.CharField(max_length=20, blank=True, verbose_name='障害支援区分')
    copay_cap = models.CharField(max_length=20, blank=True, verbose_name='利用者負担上限額')
    cert_number = models.CharField(max_length=20, blank=True, verbose_name='障害福祉サービス受給者証番号')
    child_cert_number = models.CharField(max_length=20, blank=True, verbose_name='通所受給者証番号')
    consent_date = models.DateField(null=True, blank=True, verbose_name='利用者同意日')
    # 申請者の現状（基本情報）
    overview = models.TextField(blank=True, verbose_name='概要（支援経過・現状と課題など）')
    child_state = models.JSONField(default=dict, blank=True, verbose_name='本児の様子')          # {key: 文}
    family = models.JSONField(default=list, blank=True, verbose_name='家族構成')                 # [{relation, name, age, together, job, notes}]
    history = models.TextField(blank=True, verbose_name='生活歴')
    medical = models.TextField(blank=True, verbose_name='医療の状況')
    complaint_child = models.TextField(blank=True, verbose_name='本人の主訴')
    complaint_family = models.TextField(blank=True, verbose_name='家族の主訴')
    services = models.JSONField(default=list, blank=True, verbose_name='支援の状況')              # [{name, provider, content, frequency, notes}]
    # サービス等利用計画（様式2-1）
    wishes = models.TextField(blank=True, verbose_name='利用者及びその家族の生活に対する意向（希望する生活）')
    policy = models.TextField(blank=True, verbose_name='総合的な支援の方針')
    long_goal = models.TextField(blank=True, verbose_name='長期目標')
    short_goal = models.TextField(blank=True, verbose_name='短期目標')
    rows = models.JSONField(default=list, blank=True, verbose_name='解決すべき課題と支援目標')      # [{priority, issue, goal, ...}]
    # 週間計画表（様式2-2）
    weekly = models.JSONField(default=dict, blank=True, verbose_name='週間計画')                  # {mon: [{time, text}], ...}
    weekly_other = models.TextField(blank=True, verbose_name='週単位以外のサービス')
    life_summary = models.TextField(blank=True, verbose_name='サービス提供によって実現する生活の全体像')
    # 取り込み元（書類の読み取り）
    source_knowledge = models.ForeignKey('beneficiaries.BeneficiaryKnowledge', on_delete=models.SET_NULL, null=True, blank=True,
                                         related_name='+', verbose_name='取り込んだ書類')
    created_by = models.ForeignKey('accounts.StaffAccount', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '相談支援の計画'
        verbose_name_plural = '相談支援の計画'
        ordering = ['-plan_date', '-pk']

    def __str__(self):
        return f'{self.beneficiary} サービス等利用計画 {self.plan_date or ""}'

    @property
    def title(self):
        return f'サービス等利用計画（{self.plan_date:%Y/%-m/%-d}）' if self.plan_date else 'サービス等利用計画（作成中）'

    def child_state_rows(self):
        return [(label, (self.child_state or {}).get(key, '')) for key, label in CHILD_STATE_KEYS]

    def row_list(self, min_rows=0):
        rows = [r for r in (self.rows or []) if isinstance(r, dict) and any(r.get(k) for k, _ in ROW_KEYS if k != 'priority')]
        rows.sort(key=lambda r: (str(r.get('priority') or '9').zfill(3)))
        while len(rows) < min_rows:
            rows.append({k: '' for k, _ in ROW_KEYS})
        return rows

    def weekly_rows(self, day):
        return [x for x in (self.weekly or {}).get(day) or [] if isinstance(x, dict) and (x.get('text') or x.get('time'))]

    def goals_text(self):
        """児童発達支援の個別支援計画の材料に渡す目標の一覧"""
        out = []
        for r in self.row_list():
            if r.get('goal'):
                out.append((f'優先{r["priority"]}：' if r.get('priority') else '') + r['goal'])
        return out

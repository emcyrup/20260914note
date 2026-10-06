"""
アンケート・自己評価（施設設定で「アンケート・自己評価を使う」にした事業所）。

ガイドラインが求める、年 1 回以上の
  (1) 保護者による評価（保護者向け評価表）  … Survey(kind=guardian) と回答 SurveyResponse
  (2) 従業者による評価（従業者向け評価表）  … Survey(kind=staff) と回答
  (3) 事業所の自己評価（総括）と改善内容の公表 … SelfEvaluation（(1)(2) の集計を見ながら書き、公表用の PDF を出す）
回答ページはログインなし（アドレスそのものが合い言葉。reservations/tokens.py と同じ署名つき）。
"""
import datetime

from django.db import models
from django.urls import reverse

from facilities.models import Facility
from reservations import tokens

TOKEN_KIND = 'surveys.answer'


def new_survey_token():
    return tokens.make_token(TOKEN_KIND)


def fiscal_year_of(day=None):
    """年度（4 月はじまり）"""
    day = day or datetime.date.today()
    return day.year if day.month >= 4 else day.year - 1


def fiscal_label(year):
    """2026 → 令和8年度"""
    n = year - 2018
    return f'令和{"元" if n == 1 else n}年度' if n >= 1 else f'{year}年度'


class Survey(models.Model):
    KIND_GUARDIAN = 'guardian'
    KIND_STAFF = 'staff'
    KIND_CHOICES = [(KIND_GUARDIAN, '保護者評価'), (KIND_STAFF, '従業者評価（職員）')]
    SERVICE_JIHATSU = 'jihatsu'
    SERVICE_HOUDAY = 'houday'
    SERVICE_CHOICES = [(SERVICE_JIHATSU, '児童発達支援'), (SERVICE_HOUDAY, '放課後等デイサービス')]
    # 答え（保護者）
    GUARDIAN_CHOICES = [('yes', 'はい'), ('neutral', 'どちらともいえない'), ('no', 'いいえ'), ('unknown', 'わからない')]
    STAFF_CHOICES = [('yes', 'はい'), ('no', 'いいえ')]

    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name='surveys')
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default=KIND_GUARDIAN, verbose_name='種類')
    service = models.CharField(max_length=10, choices=SERVICE_CHOICES, default=SERVICE_JIHATSU, verbose_name='事業の種類')
    fiscal_year = models.PositiveSmallIntegerField(default=fiscal_year_of, verbose_name='年度')
    title = models.CharField(max_length=100, verbose_name='件名')
    intro = models.TextField(blank=True, verbose_name='回答ページの案内文')
    # [{'no': 1, 'section': '環境・体制整備', 'text': '…'}, …]
    questions = models.JSONField(default=list, blank=True, verbose_name='設問')
    token = models.CharField(max_length=tokens.MAX_LENGTH, default=new_survey_token, unique=True, verbose_name='回答ページのアドレス')
    is_open = models.BooleanField(default=True, verbose_name='回答を受け付けている')
    closes_on = models.DateField(null=True, blank=True, verbose_name='締め切り')
    line_sent_at = models.DateTimeField(null=True, blank=True, verbose_name='LINE で送った日時')
    line_sent_count = models.PositiveSmallIntegerField(default=0, verbose_name='LINE で送った人数')
    created_by = models.ForeignKey('accounts.StaffAccount', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'アンケート'
        verbose_name_plural = 'アンケート'
        ordering = ['-fiscal_year', '-pk']

    def __str__(self):
        return f'{fiscal_label(self.fiscal_year)} {self.title}'

    @property
    def is_guardian(self):
        return self.kind == self.KIND_GUARDIAN

    @property
    def choices(self):
        return self.GUARDIAN_CHOICES if self.is_guardian else self.STAFF_CHOICES

    @property
    def fiscal_label(self):
        return fiscal_label(self.fiscal_year)

    @property
    def accepting(self):
        """いま答えられるか（受付中で、締め切りを過ぎていない）"""
        return self.is_open and (self.closes_on is None or self.closes_on >= datetime.date.today())

    def public_path(self):
        return reverse('surveys_public:answer', args=[self.token])

    def summary(self):
        """
        集計：設問ごとに {'no','section','text','counts': {選択肢: 数}, 'total', 'pct': {選択肢: %}, 'comments': [文]}。
        最後に 'free' として自由記述の一覧も返す
        """
        keys = [k for k, _ in self.choices]
        rows = [{'no': q['no'], 'section': q['section'], 'text': q['text'], 'counts': {k: 0 for k in keys}, 'total': 0,
                 'pct': {k: 0 for k in keys}, 'comments': []} for q in self.questions]
        by_no = {r['no']: r for r in rows}
        free = []
        responses = list(self.responses.order_by('submitted_at', 'pk'))
        for res in responses:
            for no, v in (res.answers or {}).items():
                row = by_no.get(int(no))
                if row and v in row['counts']:
                    row['counts'][v] += 1
                    row['total'] += 1
            for no, c in (res.comments or {}).items():
                row = by_no.get(int(no))
                if row and (c or '').strip():
                    row['comments'].append(c.strip())
            if (res.free_text or '').strip():
                free.append(res.free_text.strip())
        for row in rows:
            if row['total']:
                row['pct'] = {k: round(100 * row['counts'][k] / row['total']) for k in keys}
        return {'rows': rows, 'free': free, 'n': len(responses), 'choices': self.choices}


class SurveyResponse(models.Model):
    survey = models.ForeignKey(Survey, on_delete=models.CASCADE, related_name='responses')
    answers = models.JSONField(default=dict, blank=True, verbose_name='答え')        # {'1': 'yes', …}
    comments = models.JSONField(default=dict, blank=True, verbose_name='設問ごとの意見')  # {'1': '…'}
    free_text = models.TextField(blank=True, verbose_name='ご意見・ご要望')
    respondent = models.CharField(max_length=50, blank=True, verbose_name='お名前（任意）')
    client_key = models.CharField(max_length=64, blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'アンケートの回答'
        verbose_name_plural = 'アンケートの回答'
        ordering = ['submitted_at', 'pk']

    def __str__(self):
        return f'{self.survey} の回答 {self.pk}'


class SelfEvaluation(models.Model):
    """年度ごとの事業所の自己評価（総括）と改善内容。公表用の PDF を出す"""
    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name='self_evaluations')
    fiscal_year = models.PositiveSmallIntegerField(default=fiscal_year_of, verbose_name='年度')
    service = models.CharField(max_length=10, choices=Survey.SERVICE_CHOICES, default=Survey.SERVICE_JIHATSU, verbose_name='事業の種類')
    guardian_survey = models.ForeignKey(Survey, on_delete=models.SET_NULL, null=True, blank=True, related_name='+', verbose_name='保護者評価')
    staff_survey = models.ForeignKey(Survey, on_delete=models.SET_NULL, null=True, blank=True, related_name='+', verbose_name='従業者評価')
    # [{'no','section','text','result': 'yes'|'no'|'', 'strength': '', 'improvement': ''}, …]
    items = models.JSONField(default=list, blank=True, verbose_name='自己評価の項目')
    # [{'no','section','text','improvement': ''}, …] 保護者評価の設問ごとの「改善目標等」
    guardian_items = models.JSONField(default=list, blank=True, verbose_name='保護者評価への改善目標')
    summary = models.TextField(blank=True, verbose_name='総括（事業所全体としての評価と改善の方向）')
    published_on = models.DateField(null=True, blank=True, verbose_name='公表日')
    publish_note = models.CharField(max_length=200, blank=True, verbose_name='公表の方法', help_text='ホームページの URL・会報の号など')
    created_by = models.ForeignKey('accounts.StaffAccount', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '自己評価'
        verbose_name_plural = '自己評価'
        ordering = ['-fiscal_year', '-pk']
        constraints = [models.UniqueConstraint(fields=['facility', 'fiscal_year', 'service'], name='uniq_self_evaluation')]

    def __str__(self):
        return f'{fiscal_label(self.fiscal_year)} 自己評価（{self.get_service_display()}）'

    @property
    def fiscal_label(self):
        return fiscal_label(self.fiscal_year)

    def seed_items(self):
        """項目を従業者評価の設問（無ければ標準の設問）から作る。すでにある項目の記入は残す"""
        from .questions import default_questions
        qs = self.staff_survey.questions if self.staff_survey_id and self.staff_survey.questions else default_questions('staff', self.service)
        old = {i['text']: i for i in self.items}
        self.items = [{'no': q['no'], 'section': q['section'], 'text': q['text'],
                       'result': old.get(q['text'], {}).get('result', ''), 'strength': old.get(q['text'], {}).get('strength', ''),
                       'improvement': old.get(q['text'], {}).get('improvement', '')} for q in qs]
        gq = self.guardian_survey.questions if self.guardian_survey_id and self.guardian_survey.questions else default_questions('guardian', self.service)
        gold = {i['text']: i for i in self.guardian_items}
        self.guardian_items = [{'no': q['no'], 'section': q['section'], 'text': q['text'],
                                'improvement': gold.get(q['text'], {}).get('improvement', '')} for q in gq]

    def fill_from_staff(self):
        """従業者評価の集計から、結果（多数決）と工夫・課題の文を入れる（空の欄だけ）"""
        if not self.staff_survey_id:
            return 0
        s = self.staff_survey.summary()
        by_no = {r['no']: r for r in s['rows']}
        n = 0
        for item in self.items:
            r = by_no.get(item['no'])
            if not r or not r['total']:
                continue
            if not item.get('result'):
                item['result'] = 'yes' if r['counts'].get('yes', 0) >= r['counts'].get('no', 0) else 'no'
                n += 1
            if not item.get('strength') and r['comments']:
                item['strength'] = '\n'.join(c for c in r['comments'] if c.startswith('＋'))[:2000].replace('＋', '', 1) if any(c.startswith('＋') for c in r['comments']) else ''
            if not item.get('improvement') and r['comments']:
                rest = [c.lstrip('＋').strip() for c in r['comments'] if not c.startswith('＋')]
                item['improvement'] = '\n'.join(rest)[:2000]
        return n

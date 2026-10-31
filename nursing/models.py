"""
介護施設（やすらぎの森。通所・入所の介護事業所）の記録。2026-10-20 の打ち合わせの要望から：
- 検温板（VitalsLog）：体温・血圧・脈拍・SpO2・食事量・水分・排泄・入浴を、紙の検温板と同じ「1 人 1 週間 1 枚」で見る・印刷する
- 介護記録（CareRecord）：はい／いいえ・キーワード・音声メモ・母国語の入力から AI が 200〜300 字の下書きを作り、
  リーダー・管理者が確認して承認する（下書き → 確認待ち → 承認）。家族向けの連絡帳の文（表向き）と内部の記録（実態）を分ける
- ヒヤリハット・事故報告書（IncidentReport）：状況・原因・対応・再発防止。外国人スタッフの文を正式な日本語に整える
- 薬の管理（Medication）：薬の一覧・用法・処方医・更新（次の受診）の期限のお知らせ
- 通所介護計画書（CarePlan）：意向・方針・目標・サービス内容と見直し時期
- 人員配置（StaffingDay）：その日の利用者数と職員数から配置基準に合うかを見る（判定は nursing/staffing.py）
- 事業所の設定（NursingSetting）：AI の文字数・文体の希望・発熱とみなす体温
利用者の名簿・介護保険証・家族（緊急連絡先）は beneficiaries／kaigo のものをそのまま使う。
"""
import datetime
from decimal import Decimal

from django.db import models

from accounts.models import StaffAccount
from beneficiaries.models import Beneficiary
from facilities.models import Facility

WEEK_JP = '月火水木金土日'


def week_start(d):
    """その日を含む週の月曜日"""
    return d - datetime.timedelta(days=d.weekday())


# ---------------------------------------------------------------- 検温板
class VitalsLog(models.Model):
    SLOT_AM, SLOT_PM = 'am', 'pm'
    SLOTS = [(SLOT_AM, '朝'), (SLOT_PM, '夕')]
    MEAL_CHOICES = [(None, '—')] + [(n, f'{n}割') for n in range(10, -1, -1)]

    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name='vitals_logs')
    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.CASCADE, related_name='vitals_logs', verbose_name='利用者')
    date = models.DateField(verbose_name='日付')
    slot = models.CharField(max_length=2, choices=SLOTS, default=SLOT_AM, verbose_name='朝／夕')
    temp = models.DecimalField(max_digits=3, decimal_places=1, null=True, blank=True, verbose_name='体温')
    bp_high = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='血圧（上）')
    bp_low = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='血圧（下）')
    pulse = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='脈拍')
    spo2 = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='SpO2')
    meal_main = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='主食（割）')
    meal_side = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='副食（割）')
    water_ml = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='水分（ml）')
    urine = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='排尿（回）')
    stool = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='排便（回）')
    bath = models.BooleanField(default=False, verbose_name='入浴')
    note = models.CharField(max_length=200, blank=True, verbose_name='特記')
    recorded_by = models.ForeignKey(StaffAccount, on_delete=models.SET_NULL, null=True, blank=True, related_name='+', verbose_name='記録した人')
    updated_at = models.DateTimeField(auto_now=True)

    FEVER = Decimal('37.5')
    FIELDS = ('temp', 'bp_high', 'bp_low', 'pulse', 'spo2', 'meal_main', 'meal_side', 'water_ml', 'urine', 'stool')

    class Meta:
        verbose_name = '検温板の記録'
        verbose_name_plural = '検温板の記録'
        ordering = ['-date', 'slot', 'beneficiary__last_name_kana']
        constraints = [models.UniqueConstraint(fields=['beneficiary', 'date', 'slot'], name='uniq_vitals_day_slot')]

    def __str__(self):
        return f'{self.date} {self.get_slot_display()} {self.beneficiary} 検温板'

    @property
    def is_empty(self):
        return not (any(getattr(self, f) is not None for f in self.FIELDS) or self.bath or self.note.strip())

    def fever_limit(self):
        setting = getattr(self.facility, '_nursing_setting', None)
        return setting.fever_temp if setting is not None else self.FEVER

    @property
    def fever(self):
        return self.temp is not None and self.temp >= self.fever_limit()

    @property
    def bp_alert(self):
        """血圧の注意（上 160 以上・下 100 以上・上 90 未満）。医師の指示がある人はその値で見るので、画面では「注意」とだけ出す"""
        return (self.bp_high is not None and (self.bp_high >= 160 or self.bp_high < 90)) or (self.bp_low is not None and self.bp_low >= 100)

    @property
    def pulse_alert(self):
        return self.pulse is not None and (self.pulse < 50 or self.pulse > 100)

    @property
    def spo2_alert(self):
        return self.spo2 is not None and self.spo2 < 95

    @property
    def alerts(self):
        out = []
        if self.fever:
            out.append(f'発熱 {self.temp}℃')
        if self.bp_alert:
            out.append(f'血圧 {self.bp_high or "—"}/{self.bp_low or "—"}')
        if self.pulse_alert:
            out.append(f'脈拍 {self.pulse}')
        if self.spo2_alert:
            out.append(f'SpO2 {self.spo2}%')
        return out

    @property
    def meal_text(self):
        if self.meal_main is None and self.meal_side is None:
            return ''
        return f'{self.meal_main if self.meal_main is not None else "—"}/{self.meal_side if self.meal_side is not None else "—"}'

    @property
    def bp_text(self):
        if self.bp_high is None and self.bp_low is None:
            return ''
        return f'{self.bp_high if self.bp_high is not None else "—"}/{self.bp_low if self.bp_low is not None else "—"}'


# ---------------------------------------------------------------- 介護記録
# はい／いいえで答える項目（AI の下書きの材料。事業所ごとの言いかえは後で設定に足す）
CHECK_ITEMS = [
    ('meal', '食事をしっかりとれた'),
    ('water', '水分をとれた'),
    ('toilet', '排泄に問題はなかった'),
    ('bath', '入浴した'),
    ('mood', '表情・機嫌がよかった'),
    ('activity', '活動・レクリエーションに参加した'),
    ('pain', '痛み・体調不良の訴えがあった'),
    ('fall', '転倒・ヒヤリとしたことがあった'),
    ('family', '家族への連絡事項がある'),
]
CHECK_LABELS = dict(CHECK_ITEMS)
ANSWERS = [('yes', 'はい'), ('no', 'いいえ')]

# 入力の言語（外国人スタッフが母国語で入れたものを日本語の記録にする）
LANGS = [('ja', '日本語'), ('vi', 'ベトナム語'), ('en', '英語'), ('id', 'インドネシア語'), ('tl', 'フィリピン語（タガログ語）'),
         ('my', 'ミャンマー語'), ('ne', 'ネパール語'), ('zh', '中国語'), ('ko', '韓国語'), ('km', 'クメール語'), ('mn', 'モンゴル語')]
LANG_LABELS = dict(LANGS)

STATUS_DRAFT, STATUS_REVIEW, STATUS_APPROVED = 'draft', 'review', 'approved'
STATUS_CHOICES = [(STATUS_DRAFT, '下書き'), (STATUS_REVIEW, '確認待ち'), (STATUS_APPROVED, '承認ずみ')]


class ApprovalMixin(models.Model):
    """下書き → 確認待ち → 承認（リーダー・管理者がワンクリック）"""
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_DRAFT, verbose_name='状態')
    approved_by = models.ForeignKey(StaffAccount, on_delete=models.SET_NULL, null=True, blank=True, related_name='+', verbose_name='承認した人')
    approved_at = models.DateTimeField(null=True, blank=True, verbose_name='承認した日時')
    review_note = models.CharField(max_length=300, blank=True, verbose_name='確認した人からのひとこと')

    class Meta:
        abstract = True

    @property
    def is_approved(self):
        return self.status == STATUS_APPROVED

    @property
    def is_review(self):
        return self.status == STATUS_REVIEW

    def approve(self, user):
        from django.utils import timezone
        self.status = STATUS_APPROVED
        self.approved_by = user
        self.approved_at = timezone.now()

    def send_back(self, note=''):
        self.status = STATUS_DRAFT
        self.approved_by = None
        self.approved_at = None
        self.review_note = (note or '')[:300]


class CareRecord(ApprovalMixin):
    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name='care_records')
    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.CASCADE, related_name='care_records', verbose_name='利用者')
    date = models.DateField(verbose_name='日付')
    author = models.ForeignKey(StaffAccount, on_delete=models.SET_NULL, null=True, blank=True, related_name='care_records', verbose_name='記録した人')
    checks = models.JSONField(default=dict, blank=True, verbose_name='はい／いいえ')
    keywords = models.CharField(max_length=500, blank=True, verbose_name='キーワード')
    memo = models.TextField(blank=True, verbose_name='メモ・音声・母国語の入力')
    lang = models.CharField(max_length=5, choices=LANGS, default='ja', verbose_name='入力の言語')
    body = models.TextField(blank=True, verbose_name='介護記録')
    family_message = models.TextField(blank=True, verbose_name='家族向けのメッセージ（連絡帳）')
    internal_note = models.TextField(blank=True, verbose_name='内部の記録（家族には出さない）')
    ai_drafted = models.BooleanField(default=False, verbose_name='AI の下書きを使った')
    line_sent_at = models.DateTimeField(null=True, blank=True, verbose_name='LINE で送った日時')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '介護記録'
        verbose_name_plural = '介護記録'
        ordering = ['-date', 'beneficiary__last_name_kana']
        constraints = [models.UniqueConstraint(fields=['beneficiary', 'date'], name='uniq_care_record_day')]

    def __str__(self):
        return f'{self.date} {self.beneficiary} 介護記録'

    def check_rows(self):
        """[(key, label, answer)]"""
        c = self.checks if isinstance(self.checks, dict) else {}
        return [(k, label, c.get(k, '')) for k, label in CHECK_ITEMS]

    def checks_text(self):
        parts = []
        for k, label, a in self.check_rows():
            if a in ('yes', 'no'):
                parts.append(f'{label}：{"はい" if a == "yes" else "いいえ"}')
        return '／'.join(parts)

    @property
    def is_empty(self):
        return not (self.body.strip() or self.memo.strip() or self.keywords.strip() or self.family_message.strip()
                    or any(a for _, _, a in self.check_rows()))

    @property
    def flags(self):
        c = self.checks if isinstance(self.checks, dict) else {}
        out = []
        if c.get('pain') == 'yes':
            out.append('体調不良の訴え')
        if c.get('fall') == 'yes':
            out.append('転倒・ヒヤリ')
        if c.get('family') == 'yes':
            out.append('家族へ連絡')
        return out


# ---------------------------------------------------------------- ヒヤリハット・事故報告書
class IncidentReport(ApprovalMixin):
    KIND_NEAR, KIND_ACCIDENT = 'near', 'accident'
    KINDS = [(KIND_NEAR, 'ヒヤリハット'), (KIND_ACCIDENT, '事故')]
    INJURIES = [('none', 'けがなし'), ('minor', '軽いけが（施設で手当て）'), ('hospital', '受診した'), ('serious', '入院・重い状態')]
    REPORT_TO = [('family', '家族'), ('doctor', '医師・看護師'), ('manager', '管理者・法人'), ('caremanager', 'ケアマネジャー'), ('gov', '市町村（行政）'), ('insurer', '保険会社')]
    CATEGORIES = [('fall', '転倒・転落'), ('swallow', '誤嚥・窒息'), ('medication', '誤薬・飲み忘れ'), ('skin', '皮膚のトラブル・打撲'),
                  ('wander', '離設・所在不明'), ('transport', '送迎'), ('bath', '入浴'), ('meal', '食事'), ('other', 'その他')]

    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name='incident_reports')
    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.SET_NULL, null=True, blank=True, related_name='incident_reports', verbose_name='利用者')
    kind = models.CharField(max_length=10, choices=KINDS, default=KIND_NEAR, verbose_name='区分')
    category = models.CharField(max_length=12, choices=CATEGORIES, default='fall', verbose_name='種類')
    occurred_at = models.DateTimeField(verbose_name='発生日時')
    place = models.CharField(max_length=100, blank=True, verbose_name='場所')
    injury = models.CharField(max_length=10, choices=INJURIES, default='none', verbose_name='けがの程度')
    memo = models.TextField(blank=True, verbose_name='メモ・音声・母国語の入力')
    lang = models.CharField(max_length=5, choices=LANGS, default='ja', verbose_name='入力の言語')
    situation = models.TextField(blank=True, verbose_name='発生の状況')
    cause = models.TextField(blank=True, verbose_name='原因・背景')
    response = models.TextField(blank=True, verbose_name='対応（処置・連絡）')
    prevention = models.TextField(blank=True, verbose_name='再発防止策')
    reported_to = models.JSONField(default=list, blank=True, verbose_name='報告先')
    reporter = models.ForeignKey(StaffAccount, on_delete=models.SET_NULL, null=True, blank=True, related_name='+', verbose_name='報告者')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'ヒヤリハット・事故報告'
        verbose_name_plural = 'ヒヤリハット・事故報告'
        ordering = ['-occurred_at']

    def __str__(self):
        return f'{self.occurred_at:%Y-%m-%d} {self.get_kind_display()} {self.beneficiary or ""}'

    def reported_to_labels(self):
        labels = dict(self.REPORT_TO)
        return [labels[k] for k in (self.reported_to or []) if k in labels]


# ---------------------------------------------------------------- 薬の管理
class Medication(models.Model):
    TIMINGS = [('morning', '朝'), ('noon', '昼'), ('evening', '夕'), ('bedtime', '寝る前'), ('prn', '頓服'), ('other', 'その他')]
    DUE_SOON_DAYS = 14

    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name='medications')
    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.CASCADE, related_name='medications', verbose_name='利用者')
    name = models.CharField(max_length=100, verbose_name='薬の名前')
    dose = models.CharField(max_length=100, blank=True, verbose_name='量・用法', help_text='例：1 錠　食後')
    timings = models.JSONField(default=list, blank=True, verbose_name='のむ時間')
    timing_note = models.CharField(max_length=100, blank=True, verbose_name='時間の補足')
    purpose = models.CharField(max_length=100, blank=True, verbose_name='目的・効能')
    start_on = models.DateField(null=True, blank=True, verbose_name='開始日')
    end_on = models.DateField(null=True, blank=True, verbose_name='終了日')
    prescriber = models.CharField(max_length=100, blank=True, verbose_name='処方した医師・医療機関')
    pharmacy = models.CharField(max_length=100, blank=True, verbose_name='薬局')
    review_due = models.DateField(null=True, blank=True, verbose_name='更新の期限（次の受診・処方の切れる日）')
    notes = models.CharField(max_length=300, blank=True, verbose_name='注意（副作用・のみ方など）')
    is_active = models.BooleanField(default=True, verbose_name='服用中')
    updated_by = models.ForeignKey(StaffAccount, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '薬'
        verbose_name_plural = '薬'
        ordering = ['beneficiary__last_name_kana', '-is_active', 'name']

    def __str__(self):
        return f'{self.beneficiary} {self.name}'

    def timing_labels(self):
        labels = dict(self.TIMINGS)
        out = [labels[k] for k in (self.timings or []) if k in labels]
        if self.timing_note:
            out.append(self.timing_note)
        return out

    @property
    def timing_text(self):
        return '・'.join(self.timing_labels())

    def due_state(self, today=None):
        """'' / 'soon'（14 日以内）/ 'over'（過ぎた）"""
        if not self.review_due or not self.is_active:
            return ''
        today = today or datetime.date.today()
        if self.review_due < today:
            return 'over'
        if (self.review_due - today).days <= self.DUE_SOON_DAYS:
            return 'soon'
        return ''


# ---------------------------------------------------------------- 通所介護計画書
PLAN_ROW_KEYS = [('goal', '目標'), ('service', 'サービス内容'), ('detail', '具体的な方法・留意点'), ('frequency', '頻度・時間'), ('staff', '担当')]


class CarePlan(models.Model):
    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name='care_plans')
    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.CASCADE, related_name='care_plans', verbose_name='利用者')
    created_on = models.DateField(verbose_name='作成日')
    period_from = models.DateField(null=True, blank=True, verbose_name='計画の期間（開始）')
    period_to = models.DateField(null=True, blank=True, verbose_name='計画の期間（終了）')
    care_level = models.CharField(max_length=10, blank=True, verbose_name='要介護度')
    planner = models.CharField(max_length=50, blank=True, verbose_name='計画作成者')
    wishes_person = models.TextField(blank=True, verbose_name='本人の希望')
    wishes_family = models.TextField(blank=True, verbose_name='家族の希望')
    policy = models.TextField(blank=True, verbose_name='援助の方針')
    long_goal = models.TextField(blank=True, verbose_name='長期目標')
    short_goal = models.TextField(blank=True, verbose_name='短期目標')
    rows = models.JSONField(default=list, blank=True, verbose_name='サービス内容')
    health_note = models.TextField(blank=True, verbose_name='健康状態・留意事項')
    review_due = models.DateField(null=True, blank=True, verbose_name='見直しの時期')
    explained_on = models.DateField(null=True, blank=True, verbose_name='説明・同意日')
    agreed_by = models.CharField(max_length=100, blank=True, verbose_name='同意した人')
    created_by = models.ForeignKey(StaffAccount, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '通所介護計画書'
        verbose_name_plural = '通所介護計画書'
        ordering = ['-created_on', '-pk']

    def __str__(self):
        return f'{self.beneficiary} 計画書 {self.created_on}'

    def row_list(self, min_rows=0):
        rows = [r for r in (self.rows or []) if isinstance(r, dict)]
        while len(rows) < min_rows:
            rows.append({k: '' for k, _ in PLAN_ROW_KEYS})
        return rows

    def review_state(self, today=None):
        if not self.review_due:
            return ''
        today = today or datetime.date.today()
        if self.review_due < today:
            return 'over'
        if (self.review_due - today).days <= 30:
            return 'soon'
        return ''


# ---------------------------------------------------------------- 人員配置
class StaffingDay(models.Model):
    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name='staffing_days')
    date = models.DateField(verbose_name='日付')
    users = models.PositiveSmallIntegerField(default=0, verbose_name='利用者数')
    care_staff = models.PositiveSmallIntegerField(default=0, verbose_name='介護職員')
    nurses = models.PositiveSmallIntegerField(default=0, verbose_name='看護職員')
    counselors = models.PositiveSmallIntegerField(default=0, verbose_name='生活相談員')
    trainers = models.PositiveSmallIntegerField(default=0, verbose_name='機能訓練指導員')
    note = models.CharField(max_length=200, blank=True, verbose_name='メモ')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '人員配置'
        verbose_name_plural = '人員配置'
        ordering = ['-date']
        constraints = [models.UniqueConstraint(fields=['facility', 'date'], name='uniq_staffing_day')]

    def __str__(self):
        return f'{self.date} 人員配置'

    def judge(self):
        from .staffing import judge
        return judge(self)


# ---------------------------------------------------------------- 事業所の設定
RECORD_CHARS_CHOICES = [150, 200, 250, 300, 400]


class NursingSetting(models.Model):
    facility = models.OneToOneField(Facility, on_delete=models.CASCADE, related_name='nursing_setting')
    record_chars = models.PositiveSmallIntegerField(default=250, verbose_name='介護記録の文字数のめやす')
    family_chars = models.PositiveSmallIntegerField(default=150, verbose_name='家族向けの文の文字数のめやす')
    style_note = models.TextField(blank=True, verbose_name='文体の希望', help_text='例：敬語は最小限。「〜されました」は使わない')
    fever_temp = models.DecimalField(max_digits=3, decimal_places=1, default=Decimal('37.5'), verbose_name='発熱とみなす体温')
    capacity = models.PositiveSmallIntegerField(default=0, verbose_name='定員（人員配置の判定に使う。0 なら見ない）')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '介護施設の設定'
        verbose_name_plural = '介護施設の設定'

    def __str__(self):
        return f'{self.facility} の介護施設の設定'

    @classmethod
    def of(cls, facility):
        setting, _ = cls.objects.get_or_create(facility=facility)
        return setting

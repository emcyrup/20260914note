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
class VitalsAlertMixin:
    """体温・血圧・脈拍・SpO2 の注意（検温板と追加の検温で同じ判定）"""
    FEVER = Decimal('37.5')

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
    def bp_text(self):
        if self.bp_high is None and self.bp_low is None:
            return ''
        return f'{self.bp_high if self.bp_high is not None else "—"}/{self.bp_low if self.bp_low is not None else "—"}'


class VitalsLog(VitalsAlertMixin, models.Model):
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

    @property
    def meal_text(self):
        if self.meal_main is None and self.meal_side is None:
            return ''
        return f'{self.meal_main if self.meal_main is not None else "—"}/{self.meal_side if self.meal_side is not None else "—"}'



class VitalsExtra(VitalsAlertMixin, models.Model):
    """
    追加の検温（2026-11-10 やすらぎの森）：朝・夕の検温板のほかに測ったとき（熱が上がった・様子が気になる・受診の前など）。
    時刻つきで 1 日に何回でも入れられ、1 週間の検温板の下の一覧と、グラフ（その時刻の位置）に出る
    """
    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name='vitals_extras')
    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.CASCADE, related_name='vitals_extras', verbose_name='利用者')
    date = models.DateField(verbose_name='日付')
    time = models.TimeField(verbose_name='時刻')
    temp = models.DecimalField(max_digits=3, decimal_places=1, null=True, blank=True, verbose_name='体温')
    bp_high = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='血圧（上）')
    bp_low = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='血圧（下）')
    pulse = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='脈拍')
    spo2 = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='SpO2')
    note = models.CharField(max_length=200, blank=True, verbose_name='メモ', help_text='測った理由・様子・対応（例：顔が赤い。冷やした）')
    recorded_by = models.ForeignKey(StaffAccount, on_delete=models.SET_NULL, null=True, blank=True, related_name='+', verbose_name='記録した人')
    created_at = models.DateTimeField(auto_now_add=True)

    FIELDS = ('temp', 'bp_high', 'bp_low', 'pulse', 'spo2')

    class Meta:
        verbose_name = '追加の検温'
        verbose_name_plural = '追加の検温'
        ordering = ['date', 'time', 'pk']

    def __str__(self):
        return f'{self.date} {self.time:%H:%M} {self.beneficiary} 追加の検温'

    @property
    def is_empty(self):
        return all(getattr(self, f) is None for f in self.FIELDS)

    def get_slot_display(self):
        """ホームの「注意」の一覧で、検温板の「朝／夕」の代わりに時刻を出す"""
        return f'追加 {self.time:%H:%M}'


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
    time = models.TimeField(null=True, blank=True, verbose_name='時刻')
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
    route = models.CharField(max_length=4, choices=[('oral', '内服'), ('ext', '外用'), ('inj', '注射')], default='oral', verbose_name='区分')
    days = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='日数（○日分）')
    check_on = models.DateField(null=True, blank=True, verbose_name='確認予定日', help_text='空なら開始日＋日数（7 日分なら 7 日後）')
    check_doctor = models.CharField(max_length=50, blank=True, verbose_name='確認する医師')
    checked_at = models.DateTimeField(null=True, blank=True, verbose_name='確認した日時')
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

    def rx_line(self):
        """処方箋・処方処置・温度板に書く 1 行（例：ムコソルバン　3 錠　毎食後（朝・昼・夕）　7日分）"""
        parts = [self.name, self.dose]
        if self.timing_text:
            parts.append(f'（{self.timing_text}）')
        if self.days:
            parts.append(f'{self.days}日分')
        return '　'.join(x for x in parts if x)

    def check_state(self, today=None):
        """確認予定日：'' / 'today'（きょうまで・まだ確認していない）/ 'soon'（3 日以内）"""
        if not self.check_on or not self.is_active or self.checked_at:
            return ''
        today = today or datetime.date.today()
        if self.check_on <= today:
            return 'today'
        return 'soon' if (self.check_on - today).days <= 3 else ''

    def rx_key(self):
        """処方処置へ写し直すかを決める中身（名前・量・時間・日数・区分・医師）"""
        return (self.name, self.dose, tuple(self.timings or []), self.timing_note, self.days, self.route, self.prescriber)

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
    doctors = models.TextField(blank=True, verbose_name='上申書の医師（1 行に 1 人）', help_text='上申書の列の順。例：山田 Dr')
    rx_label = models.CharField(max_length=20, blank=True, default='入所', verbose_name='処方箋の区分（入所・通所など）')
    use_family = models.BooleanField(default=True, verbose_name='家族向けの文（連絡帳）を使う')
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

    def doctor_list(self):
        return [x.strip() for x in (self.doctors or '').splitlines() if x.strip()][:8]


# ---------------------------------------------------------------- 2026-11-11 やすらぎの森の様式（申し送り・所見／処方処置・上申書・温度板・処方箋・提供票）
class CareProfile(models.Model):
    """利用者ごとの介護施設の情報（温度板の「部屋」など。名簿にない項目だけ）"""
    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name='care_profiles')
    beneficiary = models.OneToOneField(Beneficiary, on_delete=models.CASCADE, related_name='care_profile', verbose_name='利用者')
    room = models.CharField(max_length=20, blank=True, verbose_name='部屋')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '利用者の介護施設の情報'
        verbose_name_plural = '利用者の介護施設の情報'

    def __str__(self):
        return f'{self.beneficiary} {self.room}'

    @classmethod
    def of(cls, b):
        obj, _ = cls.objects.get_or_create(beneficiary=b, defaults={'facility_id': b.facility_id})
        return obj


class HandoverDay(models.Model):
    """申し送りの用紙（⑥）の上の欄：リーダー・明日の予定・便対応者・2階での決定・周知事項・レクの人数"""
    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name='handover_days')
    date = models.DateField(verbose_name='日付')
    leader = models.CharField(max_length=50, blank=True, verbose_name='リーダー')
    tomorrow = models.TextField(blank=True, verbose_name='明日の予定')
    telemin = models.CharField(max_length=200, blank=True, verbose_name='便対応者：テレミン')
    laxative = models.CharField(max_length=200, blank=True, verbose_name='便対応者：下剤対応')
    decisions = models.TextField(blank=True, verbose_name='2階での決定・周知事項')
    rec_am = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='本日のレク人数（AM）')
    rec_pm = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='本日のレク人数（PM）')
    updated_by = models.ForeignKey(StaffAccount, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '申し送り（日ごとの欄）'
        verbose_name_plural = '申し送り（日ごとの欄）'
        constraints = [models.UniqueConstraint(fields=['facility', 'date'], name='uniq_handover_day')]

    def __str__(self):
        return f'{self.facility} {self.date}'


class HandoverEntry(models.Model):
    """日勤帯の申し送り 1 件。印刷は申し送り者ごとにひとかたまり・時間順"""
    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name='handover_entries')
    date = models.DateField(verbose_name='日付')
    time = models.TimeField(verbose_name='時刻')
    SHIFTS = [('day', '日勤'), ('night', '夜勤')]
    giver = models.CharField(max_length=50, verbose_name='申し送り者')
    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.SET_NULL, null=True, blank=True, related_name='handovers', verbose_name='利用者')
    content = models.TextField(verbose_name='内容')
    shift = models.CharField(max_length=5, choices=SHIFTS, default='day', verbose_name='勤務帯')
    important = models.BooleanField(default=False, verbose_name='重要（体調の変化など。詳しく確かめる）')
    created_by = models.ForeignKey(StaffAccount, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = '申し送り'
        verbose_name_plural = '申し送り'
        ordering = ['date', 'giver', 'time', 'pk']

    def __str__(self):
        return f'{self.date} {self.time:%H:%M} {self.giver}'


class DoctorNote(models.Model):
    """所見・処方処置（①の用紙）。Dr（黒字）・リハ（青字）・SW（赤字）。薬を足すと処方処置に自動で写す"""
    WRITERS = [('dr', 'Dr'), ('reha', 'リハ'), ('sw', 'SW'), ('ns', '看護')]
    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name='doctor_notes')
    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.CASCADE, related_name='doctor_notes', verbose_name='利用者')
    date = models.DateField(verbose_name='月日')
    writer = models.CharField(max_length=4, choices=WRITERS, default='dr', verbose_name='書いた人の区分')
    doctor = models.CharField(max_length=50, blank=True, verbose_name='医師・担当者')
    finding = models.TextField(blank=True, verbose_name='所見')
    treatment = models.TextField(blank=True, verbose_name='処方処置')
    medication = models.ForeignKey('Medication', on_delete=models.SET_NULL, null=True, blank=True, related_name='doctor_notes', verbose_name='写した薬')
    created_by = models.ForeignKey(StaffAccount, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = '所見・処方処置'
        verbose_name_plural = '所見・処方処置'
        ordering = ['date', 'pk']

    def __str__(self):
        return f'{self.beneficiary} {self.date}'


class JoushinItem(models.Model):
    """上申書（みどり会②）の 1 行。医師への上申（医師ごとの列）と、検査・採血・他科受診 等"""
    KINDS = [('dr', '医師への上申'), ('exam', '検査・採血・他科受診 等'), ('other', 'その他')]
    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name='joushin_items')
    date = models.DateField(verbose_name='日付')
    kind = models.CharField(max_length=6, choices=KINDS, default='dr', verbose_name='区分')
    doctor = models.CharField(max_length=50, blank=True, verbose_name='医師')
    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.SET_NULL, null=True, blank=True, related_name='joushin_items', verbose_name='利用者')
    content = models.TextField(verbose_name='内容')
    referral = models.BooleanField(default=False, verbose_name='紹介状')
    meal_slip = models.BooleanField(default=False, verbose_name='食事箋')
    oral = models.BooleanField(default=False, verbose_name='内服')
    created_by = models.ForeignKey(StaffAccount, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = '上申書の行'
        verbose_name_plural = '上申書の行'
        ordering = ['date', 'kind', 'pk']


class JoushinDay(models.Model):
    """上申書の【その他】の欄（日ごと）"""
    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name='joushin_days')
    date = models.DateField(verbose_name='日付')
    other = models.TextField(blank=True, verbose_name='その他')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '上申書（その他）'
        verbose_name_plural = '上申書（その他）'
        constraints = [models.UniqueConstraint(fields=['facility', 'date'], name='uniq_joushin_day')]


class DayUse(models.Model):
    """
    利用日（通所の予定と実績）。サービス提供票の読み取り・利用の曜日・手で入れた予定から作る。
    デイケアサービス利用予定・実績表（A）・送迎表（B）・週間通所予定表（C）のもと
    """
    SOURCES = [('teikyo', '提供票'), ('weekday', '利用の曜日'), ('manual', '手で入力')]
    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name='day_uses')
    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.CASCADE, related_name='day_uses', verbose_name='利用者')
    date = models.DateField(verbose_name='日付')
    planned = models.BooleanField(default=True, verbose_name='予定')
    attended = models.BooleanField(null=True, blank=True, verbose_name='実績（来た／休み）')
    half = models.BooleanField(default=False, verbose_name='半日')
    short = models.BooleanField(default=False, verbose_name='ショートの送迎')
    source = models.CharField(max_length=8, choices=SOURCES, default='manual', verbose_name='もと')
    note = models.CharField(max_length=100, blank=True, verbose_name='メモ')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '利用日'
        verbose_name_plural = '利用日'
        ordering = ['date', 'beneficiary__last_name_kana']
        constraints = [models.UniqueConstraint(fields=['beneficiary', 'date'], name='uniq_day_use')]

    def __str__(self):
        return f'{self.beneficiary} {self.date}'


class TransportRun(models.Model):
    """送迎表（B）の 1 便：日付・便（迎え／昼の送り／夕の送り）・車両・運転手：添乗・出発の時刻"""
    RUNS = [('am', '迎え'), ('noon', '昼の送り'), ('pm', '夕の送り')]
    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name='transport_runs')
    date = models.DateField(verbose_name='日付')
    run = models.CharField(max_length=4, choices=RUNS, verbose_name='便')
    vehicle = models.ForeignKey('transport.Vehicle', on_delete=models.CASCADE, related_name='nursing_runs', verbose_name='車両')
    driver = models.CharField(max_length=50, blank=True, verbose_name='運転手')
    escort = models.CharField(max_length=50, blank=True, verbose_name='添乗')
    depart = models.TimeField(null=True, blank=True, verbose_name='出発')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '送迎の便'
        verbose_name_plural = '送迎の便'
        constraints = [models.UniqueConstraint(fields=['facility', 'date', 'run', 'vehicle'], name='uniq_transport_run')]


class TransportStop(models.Model):
    """送迎表（B）で便に入れた利用者と順番・時刻"""
    run = models.ForeignKey(TransportRun, on_delete=models.CASCADE, related_name='stops')
    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.CASCADE, related_name='transport_stops', verbose_name='利用者')
    order = models.PositiveSmallIntegerField(default=0, verbose_name='順番')
    time = models.TimeField(null=True, blank=True, verbose_name='時刻')

    class Meta:
        verbose_name = '送迎の順番'
        verbose_name_plural = '送迎の順番'
        ordering = ['order', 'pk']


class ProgressNote(models.Model):
    """
    経過記録（2026-11-11 の打ち合わせ）：介護・看護・医師・リハ・SW が、利用者ごとに出来事の時刻で書く。
    夕方にまとめて入れても、出来事の時刻で並ぶ。担当者・職種を残し、職種で色を分ける。「申し送りに出す」「重要」の印
    """
    ROLES = [('care', '介護'), ('nurse', '看護'), ('doctor', '医師'), ('reha', 'リハ'), ('sw', '相談員'), ('other', 'その他')]
    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name='progress_notes')
    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.CASCADE, related_name='progress_notes', verbose_name='利用者')
    date = models.DateField(verbose_name='日付')
    time = models.TimeField(verbose_name='出来事の時刻')
    role = models.CharField(max_length=6, choices=ROLES, default='care', verbose_name='職種')
    author_name = models.CharField(max_length=50, verbose_name='担当者')
    body = models.TextField(verbose_name='記録')
    handover = models.BooleanField(default=False, verbose_name='申し送りに出す')
    important = models.BooleanField(default=False, verbose_name='重要')
    created_by = models.ForeignKey(StaffAccount, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '経過記録'
        verbose_name_plural = '経過記録'
        ordering = ['date', 'time', 'pk']

    def __str__(self):
        return f'{self.beneficiary} {self.date} {self.time:%H:%M} {self.get_role_display()}'

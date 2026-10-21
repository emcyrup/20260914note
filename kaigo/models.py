"""
居宅介護支援（ケアマネジャーの仕事）。画面の型「居宅介護」（Facility.LAYOUT_KYOTAKU）の事業所で、利用者ごとのタブとして使う。

- CareInsurance：介護保険の被保険者証（保険者番号・被保険者番号・要介護度・認定の有効期間）と負担割合証
- Provider     ：取引先（サービス事業所。訪問介護・通所介護・福祉用具など）
- SupportLog   ：居宅介護支援経過（第5表）。モニタリング・事業所との連絡・サービス担当者会議などを日付ごとに
- KaigoPlan    ：居宅サービス計画書（第1表・第2表・第3表）
- Meeting      ：サービス担当者会議の要点（第4表）
- ServiceUse   ：予定・実績（サービス利用票。月 × 事業所・サービスごとの予定日と実績日）
- Assessment   ：アセスメント（課題分析標準項目 23 項目）
- ScanDocument ：紙・スキャンの書類（前のシステムや手書きのケアプラン・会議録・アセスメントなど。PDF・写真をそのまま保管）
カイポケ（ケアアプリ）の画面の分け方にそろえている。給付管理・ケアプラン料の請求はここでは扱わない。
"""
import calendar
import datetime

from django.db import models

from beneficiaries.models import Beneficiary
from facilities.models import Facility
from facilities.uploads import kaigo_scan_upload_to

CARE_LEVELS = ['要支援1', '要支援2', '要介護1', '要介護2', '要介護3', '要介護4', '要介護5', '事業対象者', '申請中', '非該当']
CARE_LEVEL_CHOICES = [('', '—')] + [(x, x) for x in CARE_LEVELS]
WEEK_JP = '月火水木金土日'
# 要介護度の重さの順（一覧の並べ替え）。申請中・非該当・未登録は最後
CARE_ORDER = {'事業対象者': 0, '要支援1': 1, '要支援2': 2, '要介護1': 3, '要介護2': 4, '要介護3': 5, '要介護4': 6, '要介護5': 7}


def wareki(d):
    """2026-10-20 → 令和8年10月20日"""
    if not d:
        return ''
    if d >= datetime.date(2019, 5, 1):
        return f'令和{d.year - 2018}年{d.month}月{d.day}日'
    return f'平成{d.year - 1988}年{d.month}月{d.day}日'


class CareInsurance(models.Model):
    """介護保険の被保険者証と負担割合証（認定が変わるたびに1件。いちばん新しいものを使う）"""
    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.CASCADE, related_name='care_insurances', verbose_name='利用者')
    insurer_number = models.CharField(max_length=8, blank=True, verbose_name='保険者番号')
    insured_number = models.CharField(max_length=12, blank=True, verbose_name='被保険者番号')
    care_level = models.CharField(max_length=10, choices=CARE_LEVEL_CHOICES, blank=True, verbose_name='要介護度')
    certified_on = models.DateField(null=True, blank=True, verbose_name='認定日')
    issued_on = models.DateField(null=True, blank=True, verbose_name='被保険者証の交付日')
    applied_from = models.DateField(null=True, blank=True, verbose_name='適用開始日')
    applied_until = models.DateField(null=True, blank=True, verbose_name='適用終了日')
    valid_from = models.DateField(null=True, blank=True, verbose_name='認定の有効期間（開始）')
    valid_until = models.DateField(null=True, blank=True, verbose_name='認定の有効期間（終了）')
    copay_ratio = models.PositiveSmallIntegerField(null=True, blank=True, choices=[(1, '1割'), (2, '2割'), (3, '3割')],
                                                   verbose_name='負担割合')
    copay_issued_on = models.DateField(null=True, blank=True, verbose_name='負担割合証の交付日')
    copay_from = models.DateField(null=True, blank=True, verbose_name='負担割合の適用開始日')
    notes = models.CharField(max_length=200, blank=True, verbose_name='メモ')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '介護保険証'
        verbose_name_plural = '介護保険証'
        ordering = ['-valid_from', '-pk']

    def __str__(self):
        return f'{self.beneficiary.full_name} {self.care_level}'

    @property
    def is_support(self):
        return self.care_level.startswith('要支援') or self.care_level == '事業対象者'

    def days_left(self, today=None):
        if not self.valid_until:
            return None
        return (self.valid_until - (today or datetime.date.today())).days


def latest_insurance(b):
    return b.care_insurances.order_by('-valid_from', '-pk').first()


SERVICE_KINDS = ['訪問介護', '訪問入浴介護', '訪問看護', '訪問リハビリテーション', '居宅療養管理指導', '通所介護', '地域密着型通所介護',
                 '通所リハビリテーション', '短期入所生活介護', '短期入所療養介護', '福祉用具貸与', '特定福祉用具販売', '住宅改修',
                 '小規模多機能型居宅介護', '定期巡回・随時対応型訪問介護看護', '認知症対応型通所介護', '介護予防・日常生活支援総合事業',
                 '地域包括支援センター', '医療機関', '居宅介護支援', 'その他']


class Provider(models.Model):
    """取引先（サービス事業所・医療機関・地域包括支援センターなど）"""
    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name='kaigo_providers')
    name = models.CharField(max_length=100, verbose_name='事業所名')
    service_kind = models.CharField(max_length=40, blank=True, verbose_name='サービスの種類')
    office_number = models.CharField(max_length=10, blank=True, verbose_name='事業所番号')
    phone = models.CharField(max_length=20, blank=True, verbose_name='電話')
    fax = models.CharField(max_length=20, blank=True, verbose_name='FAX')
    email = models.EmailField(blank=True, verbose_name='メール')
    address = models.CharField(max_length=200, blank=True, verbose_name='住所')
    contact = models.CharField(max_length=50, blank=True, verbose_name='担当者')
    notes = models.TextField(blank=True, verbose_name='メモ')
    is_active = models.BooleanField(default=True, verbose_name='取引中')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '取引先'
        verbose_name_plural = '取引先'
        ordering = ['-is_active', 'service_kind', 'name', 'pk']

    def __str__(self):
        return self.name


class SupportLog(models.Model):
    """居宅介護支援経過（第5表）の1件"""
    # 記録の種別（2026-10-20 の打ち合わせで決めた 10 種。名前は事業所ごとに KaigoSetting.category_labels で変えられる）
    CATEGORIES = [
        ('monitoring', 'モニタリング'), ('meeting', 'サービス担当者会議'), ('family', '家族との連絡'),
        ('user', '利用者との連絡'), ('hospital', '入院・退院'), ('assessment', 'アセスメント'),
        ('certification', '要介護認定'), ('provider', '事業所との連絡'), ('medical', '医療連携'), ('other', 'その他'),
    ]
    METHODS = [('', '—'), ('visit', '訪問'), ('phone', '電話'), ('fax', 'FAX'), ('mail', 'メール'), ('office', '来所'),
               ('online', 'オンライン'), ('other', 'その他')]
    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name='kaigo_logs')
    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.CASCADE, related_name='support_logs', verbose_name='利用者')
    date = models.DateField(verbose_name='日付')
    time = models.TimeField(null=True, blank=True, verbose_name='時刻')
    category = models.CharField(max_length=20, choices=CATEGORIES, default='monitoring', verbose_name='項目')
    method = models.CharField(max_length=10, choices=METHODS, blank=True, verbose_name='方法')
    provider = models.ForeignKey(Provider, on_delete=models.SET_NULL, null=True, blank=True, related_name='+', verbose_name='相手の事業所')
    content = models.TextField(verbose_name='内容')
    created_by = models.ForeignKey('accounts.StaffAccount', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+', verbose_name='記録した人')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '支援経過'
        verbose_name_plural = '支援経過'
        ordering = ['-date', '-time', '-pk']
        indexes = [models.Index(fields=['beneficiary', 'date'])]

    def __str__(self):
        return f'{self.date} {self.get_category_display()}'

    @property
    def wareki(self):
        return wareki(self.date)

    @property
    def cat_label(self):
        """種別の名前（事業所で名前を変えていればその名前。views で labels を付ける）"""
        return getattr(self, '_labels', {}).get(self.category) or self.get_category_display()

    @property
    def heading(self):
        """第5表の「項目」欄"""
        parts = [self.cat_label]
        if self.method:
            parts.append(self.get_method_display())
        if self.provider_id:
            parts.append(self.provider.name)
        return '・'.join(parts)


class KaigoPlan(models.Model):
    """居宅サービス計画書（第1表・第2表・第3表）"""
    PLAN_TYPES = [('first', '初回'), ('referral', '紹介'), ('continue', '継続')]
    CERT_STATES = [('certified', '認定済'), ('applying', '申請中')]
    LIVING_REASONS = [('', '—'), ('1', '1. 一人暮らし'), ('2', '2. 家族等が障害、疾病等'), ('3', '3. その他')]
    GOAL_KEYS = ('need', 'long_goal', 'long_period', 'short_goal', 'short_period', 'service', 'insured', 'kind', 'provider',
                 'frequency', 'period')
    GOAL_ROWS = 6
    WEEKLY_ROWS = 10
    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name='kaigo_plans')
    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.CASCADE, related_name='kaigo_plans', verbose_name='利用者')
    created_on = models.DateField(verbose_name='作成（変更）日')
    first_created_on = models.DateField(null=True, blank=True, verbose_name='初回居宅サービス計画作成日')
    plan_type = models.CharField(max_length=10, choices=PLAN_TYPES, default='continue', verbose_name='初回・紹介・継続')
    cert_state = models.CharField(max_length=10, choices=CERT_STATES, default='certified', verbose_name='認定済・申請中')
    care_level = models.CharField(max_length=10, choices=CARE_LEVEL_CHOICES, blank=True, verbose_name='要介護状態区分')
    certified_on = models.DateField(null=True, blank=True, verbose_name='認定日')
    cert_from = models.DateField(null=True, blank=True, verbose_name='認定の有効期間（開始）')
    cert_until = models.DateField(null=True, blank=True, verbose_name='認定の有効期間（終了）')
    planner = models.CharField(max_length=50, blank=True, verbose_name='居宅サービス計画作成者')
    wishes = models.TextField(blank=True, verbose_name='利用者及び家族の生活に対する意向を踏まえた課題分析の結果')
    committee_opinion = models.TextField(blank=True, verbose_name='介護認定審査会の意見及びサービスの種類の指定')
    policy = models.TextField(blank=True, verbose_name='総合的な援助の方針')
    living_reason = models.CharField(max_length=1, choices=LIVING_REASONS, blank=True, verbose_name='生活援助中心型の算定理由')
    living_reason_other = models.CharField(max_length=100, blank=True, verbose_name='算定理由（その他）')
    # 第2表：[{'need', 'long_goal', 'long_period', 'short_goal', 'short_period', 'service', 'insured'('○'), 'kind', 'provider', 'frequency', 'period'}]
    goals = models.JSONField(default=list, blank=True, verbose_name='第2表')
    # 第3表：[{'days': [0..6], 'start': '09:00', 'end': '10:00', 'content': '訪問介護'}]
    weekly = models.JSONField(default=list, blank=True, verbose_name='第3表（週間サービス）')
    daily_life = models.TextField(blank=True, verbose_name='主な日常生活上の活動')
    other_services = models.TextField(blank=True, verbose_name='週単位以外のサービス')
    explained_on = models.DateField(null=True, blank=True, verbose_name='説明・同意日')
    agreed_by = models.CharField(max_length=100, blank=True, verbose_name='同意した人')
    created_by = models.ForeignKey('accounts.StaffAccount', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+', verbose_name='作成者')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '居宅サービス計画書'
        verbose_name_plural = '居宅サービス計画書'
        ordering = ['-created_on', '-pk']

    def __str__(self):
        return f'{self.beneficiary.full_name} {self.created_on} 居宅サービス計画書'

    def goal_rows(self, blank=True):
        rows = [{k: str(r.get(k, '')) for k in self.GOAL_KEYS} for r in (self.goals or []) if isinstance(r, dict)]
        return rows + ([{} for _ in range(max(1, self.GOAL_ROWS - len(rows)))] if blank else [])

    def weekly_rows(self, blank=True):
        rows = []
        for r in self.weekly or []:
            if isinstance(r, dict):
                days = [d for d in r.get('days', []) if isinstance(d, int) and 0 <= d <= 6]
                rows.append({'days': days, 'day_label': '・'.join(WEEK_JP[d] for d in days), 'start': r.get('start', ''),
                             'end': r.get('end', ''), 'content': r.get('content', '')})
        return rows + ([{'days': []} for _ in range(max(2, self.WEEKLY_ROWS - len(rows)))] if blank else [])

    def week_grid(self):
        """第3表の表：2 時間ごとの行 × 月〜日。その時間に始まるサービスを入れる"""
        slots = [(h, f'{h}:00') for h in range(4, 24, 2)] + [(0, '0:00'), (2, '2:00')]
        grid = []
        rows = self.weekly_rows(blank=False)
        for h, label in slots:
            cells = []
            for d in range(7):
                items = []
                for r in rows:
                    try:
                        start_h = int((r['start'] or '').split(':')[0])
                    except ValueError:
                        continue
                    if d in r['days'] and h <= start_h < h + 2:
                        items.append(f"{r['start']}〜{r['end']} {r['content']}".strip())
                cells.append(items)
            grid.append({'label': label, 'cells': cells})
        return grid


class Meeting(models.Model):
    """サービス担当者会議の要点（第4表）"""
    ATTENDEE_ROWS = 8
    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name='kaigo_meetings')
    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.CASCADE, related_name='kaigo_meetings', verbose_name='利用者')
    held_on = models.DateField(verbose_name='開催日')
    start = models.TimeField(null=True, blank=True, verbose_name='開始')
    end = models.TimeField(null=True, blank=True, verbose_name='終了')
    place = models.CharField(max_length=100, blank=True, verbose_name='開催場所')
    count = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='開催回数')
    planner = models.CharField(max_length=50, blank=True, verbose_name='居宅サービス計画作成者（担当者）')
    # [{'org': '〇〇訪問介護（サービス提供責任者）', 'name': '山田'}]
    attendees = models.JSONField(default=list, blank=True, verbose_name='会議出席者')
    topics = models.TextField(blank=True, verbose_name='検討した項目')
    discussion = models.TextField(blank=True, verbose_name='検討内容')
    conclusion = models.TextField(blank=True, verbose_name='結論')
    remaining = models.TextField(blank=True, verbose_name='残された課題（次回の開催時期）')
    created_by = models.ForeignKey('accounts.StaffAccount', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+', verbose_name='記録した人')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'サービス担当者会議の要点'
        verbose_name_plural = 'サービス担当者会議の要点'
        ordering = ['-held_on', '-pk']

    def __str__(self):
        return f'{self.beneficiary.full_name} {self.held_on} 担当者会議'

    def attendee_rows(self, blank=True):
        rows = [{'org': str(a.get('org', '')), 'name': str(a.get('name', ''))} for a in (self.attendees or []) if isinstance(a, dict)]
        return rows + ([{} for _ in range(max(0, self.ATTENDEE_ROWS - len(rows)))] if blank else [])

    @property
    def time_label(self):
        if self.start and self.end:
            return f'{self.start:%H:%M}〜{self.end:%H:%M}'
        return f'{self.start:%H:%M}〜' if self.start else ''


class ServiceUse(models.Model):
    """予定・実績（サービス利用票の1行）：その月に、どの事業所の何のサービスを、何日に予定し、何日に使ったか"""
    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name='kaigo_uses')
    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.CASCADE, related_name='kaigo_uses', verbose_name='利用者')
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()
    order = models.PositiveSmallIntegerField(default=0)
    provider = models.ForeignKey(Provider, on_delete=models.SET_NULL, null=True, blank=True, related_name='+', verbose_name='事業所')
    provider_name = models.CharField(max_length=100, blank=True, verbose_name='事業所名')
    service = models.CharField(max_length=100, blank=True, verbose_name='サービス内容')
    start = models.CharField(max_length=5, blank=True, verbose_name='開始')
    end = models.CharField(max_length=5, blank=True, verbose_name='終了')
    plan_days = models.JSONField(default=list, blank=True, verbose_name='予定の日')
    done_days = models.JSONField(default=list, blank=True, verbose_name='実績の日')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '予定・実績'
        verbose_name_plural = '予定・実績'
        ordering = ['year', 'month', 'order', 'pk']
        indexes = [models.Index(fields=['beneficiary', 'year', 'month'])]

    @property
    def provider_label(self):
        return self.provider.name if self.provider_id else self.provider_name

    @property
    def time_label(self):
        return f'{self.start}〜{self.end}' if self.start or self.end else ''

    def days_in_month(self):
        return calendar.monthrange(self.year, self.month)[1]


ASSESSMENT_ITEMS = [
    ('基本情報に関する項目', [
        ('basic', '基本情報（受付、利用者等基本情報）'), ('history', 'これまでの生活と現在の状況'),
        ('welfare', '利用者の社会保障制度の利用情報'), ('resources', '現在利用している支援や社会資源の状況'),
        ('independence', '日常生活自立度（障害）'), ('dementia', '日常生活自立度（認知症）'), ('wishes', '主訴・意向'),
        ('certification', '認定情報'), ('reason', '今回のアセスメントの理由'),
    ]),
    ('課題分析（アセスメント）に関する項目', [
        ('health', '健康状態'), ('adl', 'ADL'), ('iadl', 'IADL'), ('cognition', '認知機能や判断能力'),
        ('communication', 'コミュニケーションにおける理解と表出の状況'), ('rhythm', '生活リズム'), ('excretion', '排泄の状況'),
        ('hygiene', '清潔の保持に関する状況'), ('oral', '口腔内の状況'), ('meal', '食事摂取の状況'), ('social', '社会との関わり'),
        ('family', '家族等の状況'), ('housing', '居住環境'), ('other', 'その他留意すべき事項・状況'),
    ]),
]
ASSESSMENT_KEYS = [k for _, items in ASSESSMENT_ITEMS for k, _ in items]


class Assessment(models.Model):
    """アセスメント（課題分析標準項目 23 項目）"""
    KINDS = [('first', '初回'), ('update', '更新'), ('change', '区分変更'), ('regular', '定期'), ('other', 'その他')]
    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name='kaigo_assessments')
    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.CASCADE, related_name='kaigo_assessments', verbose_name='利用者')
    assessed_on = models.DateField(verbose_name='実施日')
    kind = models.CharField(max_length=10, choices=KINDS, default='regular', verbose_name='理由')
    place = models.CharField(max_length=100, blank=True, verbose_name='場所')
    assessor = models.CharField(max_length=50, blank=True, verbose_name='実施した人')
    items = models.JSONField(default=dict, blank=True, verbose_name='項目ごとの内容')
    summary = models.TextField(blank=True, verbose_name='課題のまとめ')
    created_by = models.ForeignKey('accounts.StaffAccount', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+', verbose_name='記録した人')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'アセスメント（課題分析）'
        verbose_name_plural = 'アセスメント（課題分析）'
        ordering = ['-assessed_on', '-pk']

    def item_groups(self):
        data = self.items or {}
        return [(group, [(k, label, data.get(k, '')) for k, label in items]) for group, items in ASSESSMENT_ITEMS]

    @property
    def filled(self):
        return sum(1 for k in ASSESSMENT_KEYS if (self.items or {}).get(k))


SCAN_EXTENSIONS = ('jpg', 'jpeg', 'png', 'webp', 'gif', 'heic', 'pdf')
SCAN_MAX_BYTES = 20 * 1024 * 1024
SCAN_MAX_FILES = 20


class ScanDocument(models.Model):
    """紙・スキャンの書類。中身は読み取らず、そのまま保管して開けるようにする"""
    KINDS = [('plan', 'ケアプラン'), ('meeting', '担当者会議の要点'), ('assessment', 'アセスメント'),
             ('logs', '支援経過'), ('uses', '予定・実績（利用票・提供票）'), ('insurance', '介護保険証・負担割合証'),
             ('other', 'そのほか')]
    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.CASCADE, related_name='kaigo_scans', verbose_name='利用者')
    kind = models.CharField(max_length=12, choices=KINDS, default='other', verbose_name='書類の種類')
    doc_date = models.DateField(null=True, blank=True, verbose_name='書類の日付')
    title = models.CharField(max_length=100, blank=True, verbose_name='件名')
    file = models.FileField(upload_to=kaigo_scan_upload_to, verbose_name='ファイル')
    file_name = models.CharField(max_length=200, blank=True, verbose_name='元のファイル名')
    uploaded_by = models.ForeignKey('accounts.StaffAccount', on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='+', verbose_name='取り込んだ人')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = '紙・スキャンの書類'
        verbose_name_plural = '紙・スキャンの書類'
        ordering = [models.F('doc_date').desc(nulls_last=True), '-created_at', '-pk']

    def __str__(self):
        return f'{self.beneficiary.full_name} {self.label}'

    @property
    def label(self):
        return self.title or self.file_name or self.get_kind_display()

    @property
    def ext(self):
        return (self.file_name or self.file.name or '').lower().rsplit('.', 1)[-1]

    @property
    def is_image(self):
        return self.ext in ('jpg', 'jpeg', 'png', 'webp', 'gif')


SUMMARY_CHOICES = [200, 300, 400, 600, 800, 1000]


class KaigoSetting(models.Model):
    """居宅介護支援の事業所ごとの設定：AI でまとめるときの文字数・文体の希望、記録の種別の名前"""
    facility = models.OneToOneField(Facility, on_delete=models.CASCADE, related_name='kaigo_setting')
    summary_chars = models.PositiveSmallIntegerField(default=400, verbose_name='AI でまとめる文字数のめやす')
    style_note = models.TextField(blank=True, verbose_name='文体の希望',
                                  help_text='例：もう少し締めた文に／砕けすぎないように／「〜とのこと」を多用しない')
    category_labels = models.JSONField(default=dict, blank=True, verbose_name='記録の種別の名前（変えたものだけ）')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '居宅介護支援の設定'
        verbose_name_plural = '居宅介護支援の設定'

    @classmethod
    def of(cls, facility):
        obj, _ = cls.objects.get_or_create(facility=facility)
        return obj

    def labels(self):
        names = {k: str(v).strip() for k, v in (self.category_labels or {}).items() if str(v).strip()}
        return {k: names.get(k, label) for k, label in SupportLog.CATEGORIES}

    def categories(self):
        labels = self.labels()
        return [(k, labels[k]) for k, _ in SupportLog.CATEGORIES]

"""
帳票のデータを組み立てる（画面・CSV・PDF で共通）

各関数は Report を返す。Report は「見出し・補足の項目・列名・行」だけを持つので、
CSV は行をそのまま書き、PDF は同じ内容を表として描く。
"""
import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta

from django.db.models import Count, Q

from beneficiaries.models import Beneficiary, RecipientCertificate
from billing.models import BillingMatrixEntry
from facilities.context_processors import get_terms
from records.models import DailyRecord
from schedules.models import ScheduledVisit
from support_plans.models import SupportPlan

WEEKDAYS = '月火水木金土日'
CIRCLE = '○'


@dataclass
class Report:
    key: str
    title: str
    subtitle: str = ''
    meta: list = field(default_factory=list)      # [(項目名, 値)]
    columns: list = field(default_factory=list)   # 列名
    rows: list = field(default_factory=list)      # 行（列名と同じ長さのリスト）
    landscape: bool = False
    filename: str = 'report'
    footnote: str = ''
    widths: list | None = None                    # 列幅（%）。省略時は自動


def fmt_date(d, sep='/'):
    return d.strftime(f'%Y{sep}%m{sep}%d') if d else ''


def fmt_time(t):
    return t.strftime('%H:%M') if t else ''


def fmt_dt(dt):
    return dt.strftime('%Y/%m/%d %H:%M') if dt else ''


def age_on(dob, today):
    if not dob:
        return ''
    years = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    return str(years)


def month_range(year, month):
    last = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)


def _cert_for(beneficiary, on_date):
    """その日に有効な受給者証。無ければ最新のもの"""
    certs = list(beneficiary.recipient_certificates.order_by('-valid_from'))
    for c in certs:
        if c.valid_from <= on_date <= c.valid_until:
            return c
    return certs[0] if certs else None


def _viewpoints_text(vps):
    label = {'yes': 'はい', 'no': 'いいえ'}
    return '\n'.join(f'{v.get("text", "")}：{label.get(v.get("answer"), "未確認")}' for v in (vps or []) if v.get('text'))


# =============================================
# 1. サービス提供実績記録票（利用者別・月次）
# =============================================
def service_record(facility, beneficiary, year, month):
    terms = _terms(facility)
    start, end = month_range(year, month)
    visits = {v.date: v for v in ScheduledVisit.objects.filter(beneficiary=beneficiary, date__range=(start, end))}
    entries = {e.date: e for e in BillingMatrixEntry.objects.filter(beneficiary=beneficiary, date__range=(start, end))
               .prefetch_related('addons__addon')}
    records = {}
    for r in DailyRecord.objects.filter(beneficiary=beneficiary, date__range=(start, end)).order_by('date', 'pk'):
        records.setdefault(r.date, r)

    rows, counts = [], {'利用': 0, '欠席': 0, '振替': 0, '予定': 0}
    pickups = dropoffs = 0
    d = start
    while d <= end:
        v, e, r = visits.get(d), entries.get(d), records.get(d)
        if v or e or r:
            status = e.get_status_display() if e else (v.get_status_display() if v else '')
            if status == '来所':
                status = '利用'
            counts[status] = counts.get(status, 0) + 1
            addons = '、'.join(a.addon.name for a in e.addons.all() if a.is_applied) if e else ''
            pick = CIRCLE if v and v.has_pickup else ''
            drop = CIRCLE if v and v.has_dropoff else ''
            pickups += bool(pick)
            dropoffs += bool(drop)
            rows.append([str(d.day), WEEKDAYS[d.weekday()], status,
                         fmt_time(r.entry_time) if r else '', fmt_time(r.exit_time) if r else '',
                         pick, drop, addons, (v.notes if v else '') or '', ''])
        d += timedelta(days=1)

    cert = _cert_for(beneficiary, start)
    meta = [
        ('事業所名', facility.name), ('事業所番号', facility.office_number or ''),
        (f'{terms["beneficiary"]}氏名', f'{beneficiary.full_name}（{beneficiary.full_name_kana}）'),
        ('受給者証番号', cert.certificate_number if cert else ''),
        ('支給量', f'{cert.granted_days} 日/月' if cert else ''),
        ('対象月', f'{year}年{month}月'),
        ('利用日数', f'{counts.get("利用", 0)} 日'), ('欠席', f'{counts.get("欠席", 0)} 回'),
        ('振替', f'{counts.get("振替", 0)} 回'), ('送迎', f'迎え {pickups} 回 ／ 送り {dropoffs} 回'),
    ]
    return Report(
        key='service_record', title='サービス提供実績記録票',
        subtitle=f'{year}年{month}月　{beneficiary.full_name}', meta=meta,
        columns=['日', '曜日', '提供状況', '開始時間', '終了時間', '送迎（迎え）', '送迎（送り）', '加算', '備考', '保護者確認'],
        rows=rows, landscape=True, filename=f'実績記録票_{beneficiary.full_name}_{year}{month:02d}',
        widths=[5, 5, 8, 8, 8, 8, 8, 22, 18, 10],
        footnote='提供状況は請求マトリックス（未入力の日は予定表）から、開始・終了時間は日誌の入退室時間から転記しています。',
    )


# =============================================
# 2. 出欠・利用実績一覧（施設・月次）
# =============================================
def attendance_summary(facility, year, month):
    terms = _terms(facility)
    start, end = month_range(year, month)
    beneficiaries = list(Beneficiary.objects.filter(facility=facility, status=Beneficiary.STATUS_ACTIVE)
                         .prefetch_related('recipient_certificates'))
    visit_stats = {}
    for row in (ScheduledVisit.objects.filter(facility=facility, date__range=(start, end))
                .values('beneficiary_id').annotate(
                    scheduled=Count('pk', filter=Q(status=ScheduledVisit.STATUS_SCHEDULED)),
                    attended=Count('pk', filter=Q(status=ScheduledVisit.STATUS_ATTENDED)),
                    absent=Count('pk', filter=Q(status=ScheduledVisit.STATUS_ABSENT)),
                    transferred=Count('pk', filter=Q(status=ScheduledVisit.STATUS_TRANSFERRED)),
                    pickups=Count('pk', filter=Q(has_pickup=True)),
                    dropoffs=Count('pk', filter=Q(has_dropoff=True)))):
        visit_stats[row['beneficiary_id']] = row
    entry_stats = {}
    for row in (BillingMatrixEntry.objects.filter(facility=facility, date__range=(start, end))
                .values('beneficiary_id').annotate(
                    attended=Count('pk', filter=Q(status=BillingMatrixEntry.STATUS_ATTENDED)),
                    absent=Count('pk', filter=Q(status=BillingMatrixEntry.STATUS_ABSENT)),
                    transferred=Count('pk', filter=Q(status=BillingMatrixEntry.STATUS_TRANSFERRED)))):
        entry_stats[row['beneficiary_id']] = row
    record_counts = dict(DailyRecord.objects.filter(facility=facility, date__range=(start, end))
                         .values_list('beneficiary_id').annotate(n=Count('pk')).values_list('beneficiary_id', 'n'))

    rows = []
    total_attended = 0
    for b in beneficiaries:
        vs, es = visit_stats.get(b.pk, {}), entry_stats.get(b.pk, {})
        cert = _cert_for(b, start)
        total_attended += vs.get('attended', 0)
        row = [b.full_name, b.full_name_kana, str(cert.granted_days) if cert else '',
               str(vs.get('scheduled', 0)), str(vs.get('attended', 0)), str(vs.get('absent', 0)), str(vs.get('transferred', 0)),
               str(vs.get('pickups', 0)), str(vs.get('dropoffs', 0)), str(record_counts.get(b.pk, 0))]
        if facility.use_billing:
            row += [str(es.get('attended', 0)), str(es.get('absent', 0)), str(es.get('transferred', 0))]
        rows.append(row)
    columns = ['氏名', 'ふりがな', '支給量', '予定', '来所', '欠席', '振替', '迎え', '送り', '日誌作成']
    footnote = '予定・来所・欠席・振替・送迎は予定表（出欠入力）から集計しています。'
    if facility.use_billing:
        columns += ['請求：利用', '請求：欠席', '請求：振替']
        footnote = '予定・来所・欠席・振替・送迎は予定表（出欠入力）から、請求の各列は請求マトリックスから集計しています。'
    return Report(
        key='attendance_summary', title='出欠・利用実績一覧', subtitle=f'{year}年{month}月',
        meta=[('事業所名', facility.name), ('対象月', f'{year}年{month}月'),
              (f'在籍{terms["beneficiary"]}数', f'{len(beneficiaries)} 名'), ('来所延べ人数', f'{total_attended} 人日')],
        columns=columns, rows=rows, landscape=True, filename=f'出欠利用実績_{year}{month:02d}', footnote=footnote,
    )


# =============================================
# 3. 業務日誌（施設・日別）
# =============================================
def daily_journal(facility, day):
    terms = _terms(facility)
    records = list(DailyRecord.objects.filter(facility=facility, date=day)
                   .select_related('beneficiary', 'author').prefetch_related('activity_tags')
                   .order_by('beneficiary__last_name_kana', 'beneficiary__first_name_kana', 'pk'))
    attended = ScheduledVisit.objects.filter(facility=facility, date=day, status=ScheduledVisit.STATUS_ATTENDED).count()
    rows = []
    for r in records:
        health = r.get_health_condition_display() + (f'（{r.health_note}）' if r.health_note else '')
        activity = r.activity_name or '・'.join(t.name for t in r.activity_tags.all())
        rows.append([r.beneficiary.full_name, fmt_time(r.entry_time), fmt_time(r.exit_time), health, activity,
                     r.activity_aim, r.observation_text, r.support_text, r.reaction_text,
                     str(r.author) if r.author else '', r.get_status_display()])
    return Report(
        key='daily_journal', title='業務日誌', subtitle=f'{day.year}年{day.month}月{day.day}日（{WEEKDAYS[day.weekday()]}）',
        meta=[('事業所名', facility.name), ('日付', f'{fmt_date(day)}（{WEEKDAYS[day.weekday()]}）'),
              ('来所人数', f'{attended} 名'), ('記録件数', f'{len(records)} 件')],
        columns=[terms['beneficiary'], '入室', '退室', '体調', '活動', 'めあて', '観察・活動内容', '支援内容', '本人の反応', '記録者', '状態'],
        rows=rows, landscape=True, filename=f'業務日誌_{day.strftime("%Y%m%d")}',
        widths=[9, 5, 5, 7, 9, 12, 16, 16, 12, 5, 4],
    )


# =============================================
# 4. 日誌一覧（利用者別・期間）
# =============================================
def beneficiary_records(facility, beneficiary, start, end):
    terms = _terms(facility)
    records = list(DailyRecord.objects.filter(beneficiary=beneficiary, date__range=(start, end))
                   .select_related('author').prefetch_related('activity_tags').order_by('date', 'pk'))
    rows = []
    for r in records:
        activity = r.activity_name or '・'.join(t.name for t in r.activity_tags.all())
        rows.append([fmt_date(r.date), WEEKDAYS[r.date.weekday()], activity, _viewpoints_text(r.activity_viewpoints),
                     r.observation_text, r.support_text, r.reaction_text, r.activity_reflection,
                     str(r.author) if r.author else ''])
    return Report(
        key='beneficiary_records', title='支援記録（日誌）一覧',
        subtitle=f'{beneficiary.full_name}　{fmt_date(start)} 〜 {fmt_date(end)}',
        meta=[('事業所名', facility.name), (f'{terms["beneficiary"]}氏名', f'{beneficiary.full_name}（{beneficiary.full_name_kana}）'),
              ('期間', f'{fmt_date(start)} 〜 {fmt_date(end)}'), ('件数', f'{len(records)} 件')],
        columns=['日付', '曜日', '活動', '観点（はい／いいえ）', '観察・活動内容', '支援内容', '本人の反応', '考察', '記録者'],
        rows=rows, landscape=True,
        filename=f'支援記録_{beneficiary.full_name}_{start.strftime("%Y%m%d")}-{end.strftime("%Y%m%d")}',
        widths=[8, 4, 9, 14, 16, 16, 12, 16, 5],
    )


# =============================================
# 5. 利用者名簿
# =============================================
def beneficiary_roster(facility, include_inactive=False):
    terms = _terms(facility)
    today = date.today()
    qs = Beneficiary.objects.filter(facility=facility).prefetch_related('guardians', 'recipient_certificates')
    if not include_inactive:
        qs = qs.filter(status=Beneficiary.STATUS_ACTIVE)
    rows = []
    for b in qs:
        guardian = next((g for g in b.guardians.all() if g.is_primary), None) or next(iter(b.guardians.all()), None)
        cert = _cert_for(b, today)
        rows.append([b.full_name, b.full_name_kana, fmt_date(b.date_of_birth), age_on(b.date_of_birth, today),
                     b.get_gender_display() if b.gender else '', b.get_disability_class_display() if b.disability_class else '',
                     b.disability_type, b.scheduled_weekdays_display,
                     f'{guardian.last_name} {guardian.first_name}' if guardian else '',
                     guardian.get_relation_display() if guardian and guardian.relation else '', guardian.phone if guardian else '',
                     cert.certificate_number if cert else '',
                     f'{fmt_date(cert.valid_from)} 〜 {fmt_date(cert.valid_until)}' if cert else '',
                     str(cert.granted_days) if cert else '', f'{cert.monthly_cap:,}' if cert else '',
                     b.get_status_display()])
    return Report(
        key='beneficiary_roster', title=f'{terms["beneficiary"]}名簿', subtitle=f'{fmt_date(today)} 現在',
        meta=[('事業所名', facility.name), ('基準日', fmt_date(today)), ('人数', f'{len(rows)} 名'),
              ('対象', 'すべて' if include_inactive else '在籍中のみ')],
        columns=['氏名', 'ふりがな', '生年月日', '年齢', '性別', '障害区分', '障害種別', '利用曜日', '保護者', '続柄', '電話',
                 '受給者証番号', '有効期間', '支給量', '上限月額', '在籍'],
        rows=rows, landscape=True, filename=f'{terms["beneficiary"]}名簿_{today.strftime("%Y%m%d")}',
    )


# =============================================
# 6. 個別支援計画一覧
# =============================================
def plan_list(facility, status='all'):
    terms = _terms(facility)
    qs = SupportPlan.objects.filter(facility=facility).select_related('beneficiary', 'manager').order_by('beneficiary__last_name_kana', '-created_at')
    if status == 'open':
        qs = qs.exclude(status=SupportPlan.STATUS_CLOSED)
    elif status in (SupportPlan.STATUS_IN_PROGRESS, SupportPlan.STATUS_ACTIVE, SupportPlan.STATUS_CLOSED):
        qs = qs.filter(status=status)
    rows = []
    for p in qs:
        d = p.get_step(SupportPlan.STEP_DRAFT)
        period = f'{fmt_date(d.period_start)} 〜 {fmt_date(d.period_end)}' if d and (d.period_start or d.period_end) else ''
        due = p.next_monitoring_due if p.status == SupportPlan.STATUS_ACTIVE else None
        rows.append([p.beneficiary.full_name, p.title, p.get_status_display(),
                     '終了' if p.status == SupportPlan.STATUS_CLOSED else f'{p.current_step}. {p.get_current_step_display()}',
                     period, fmt_date(due) if due else '', str(p.manager) if p.manager else '', fmt_date(p.created_at.date())])
    label = {'all': 'すべて', 'open': '作成中・実施中', 'in_progress': '作成中', 'active': '実施中', 'closed': '終了'}.get(status, 'すべて')
    return Report(
        key='plan_list', title='個別支援計画一覧', subtitle=label,
        meta=[('事業所名', facility.name), ('基準日', fmt_date(date.today())), ('対象', label), ('件数', f'{len(rows)} 件')],
        columns=[terms['beneficiary'], '計画名', '状態', '現在のステップ', '計画期間', '次回モニタリング', '児童発達支援管理責任者', '作成日'],
        rows=rows, landscape=False, filename=f'支援計画一覧_{date.today().strftime("%Y%m%d")}',
    )


def _terms(facility):
    class _U:
        is_authenticated = True

        def __init__(self, f):
            self.facility = f
    return get_terms(_U(facility))


REPORT_KINDS = {
    'service_record':      {'title': 'サービス提供実績記録票', 'desc': '{beneficiary}ごとに、その月の提供状況・入退室時間・送迎・加算を日付順に並べます。保護者確認欄つき。', 'icon': 'bi-calendar-check', 'filters': ['beneficiary', 'month']},
    'attendance_summary':  {'title': '出欠・利用実績一覧', 'desc': '在籍中の{beneficiary}全員の、その月の予定・来所・欠席・振替・送迎回数・日誌作成数・請求状況を一覧にします。', 'icon': 'bi-table', 'filters': ['month']},
    'daily_journal':       {'title': '業務日誌（日別）', 'desc': 'その日に来所した{beneficiary}の入退室・体調・活動・観察・支援・反応を1枚にまとめます。', 'icon': 'bi-journal-text', 'filters': ['date']},
    'beneficiary_records': {'title': '支援記録（日誌）一覧', 'desc': '{beneficiary}ごとに、期間内の日誌（観点のはい／いいえ、観察・支援・反応・考察）を時系列で並べます。モニタリングや相談支援事業所への提出に。', 'icon': 'bi-journal-richtext', 'filters': ['beneficiary', 'range']},
    'beneficiary_roster':  {'title': '{beneficiary}名簿', 'desc': '基本情報・利用曜日・保護者連絡先・受給者証（番号・有効期間・支給量・上限月額）の一覧です。', 'icon': 'bi-people', 'filters': ['include_inactive']},
    'plan_list':           {'title': '個別支援計画一覧', 'desc': '計画の状態・現在のステップ・計画期間・次回モニタリング期限の一覧です。', 'icon': 'bi-diagram-3', 'filters': ['plan_status']},
}

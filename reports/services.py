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


def uses_reservations(facility):
    """来所の実績を予約管理（Reservation の実績）から取る事業所（療育：ゆあーず・オウル）"""
    return bool(facility.use_reservation and not facility.use_schedule)


def uses_therapy(facility):
    """記録が日誌（DailyRecord）ではなく療育記録（TherapyRecord）の事業所"""
    return bool(facility.use_therapy_record)


RESERVATION_STATUS = {'attended': '利用', 'absent': '欠席', 'cancelled': '取消', '': '予定'}


def _reservations(facility, start, end, beneficiary=None):
    from reservations.models import Reservation
    qs = Reservation.objects.filter(facility=facility, date__range=(start, end), status=Reservation.STATUS_CONFIRMED, beneficiary__isnull=False)
    if beneficiary is not None:
        qs = qs.filter(beneficiary=beneficiary)
    return qs


def _slot_end(facility, start_time):
    """時間枠の予約なら 開始＋1 枠の長さ を終了時刻にする"""
    if start_time is None:
        return None
    from reservations.services import get_setting
    setting = get_setting(facility)
    if not setting.slot_mode:
        return None
    from datetime import datetime
    return (datetime.combine(date.today(), start_time) + timedelta(minutes=setting.slot_minutes)).time()


def _activities_text(activities):
    return '・'.join(str(a) for a in (activities or []) if a)


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


def _questions_text(rows):
    """共通の質問・個別の質問の答え（records/questions.py）"""
    from records.questions import ANSWER_LABELS, clean
    return '\n'.join(f'{r["text"]}：{ANSWER_LABELS.get(r["answer"], "未回答")}' for r in clean(rows))


# =============================================
# 1. サービス提供実績記録票（利用者別・月次）
# =============================================
def service_record(facility, beneficiary, year, month):
    if uses_reservations(facility) or uses_therapy(facility):
        return _service_record_ryoiku(facility, beneficiary, year, month)
    terms = _terms(facility)
    start, end = month_range(year, month)
    visits = {v.date: v for v in ScheduledVisit.objects.filter(facility=facility, beneficiary=beneficiary, date__range=(start, end))}
    entries = {e.date: e for e in BillingMatrixEntry.objects.filter(facility=facility, beneficiary=beneficiary, date__range=(start, end))
               .prefetch_related('addons__addon')}
    records = {}
    for r in DailyRecord.objects.filter(facility=facility, beneficiary=beneficiary, date__range=(start, end)).order_by('date', 'pk'):
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


def _service_record_ryoiku(facility, beneficiary, year, month):
    """療育の事業所：提供状況は予約の実績、時間は予約の開始時刻（＋1 枠の長さ）、やったこと・担当は療育記録から"""
    from therapy.models import TherapyRecord
    terms = _terms(facility)
    start, end = month_range(year, month)
    res = {}
    if uses_reservations(facility):
        for r in _reservations(facility, start, end, beneficiary).order_by('date', 'start_time', 'pk'):
            res.setdefault(r.date, r)
    recs = {}
    if uses_therapy(facility):
        for t in TherapyRecord.objects.filter(facility=facility, beneficiary=beneficiary, date__range=(start, end)).select_related('staff').order_by('date', 'time', 'pk'):
            recs.setdefault(t.date, t)
    else:
        for r in DailyRecord.objects.filter(facility=facility, beneficiary=beneficiary, date__range=(start, end)).order_by('date', 'pk'):
            recs.setdefault(r.date, r)
    rows, counts = [], {}
    d = start
    while d <= end:
        r, t = res.get(d), recs.get(d)
        if r or t:
            status = RESERVATION_STATUS.get(r.attendance, '予定') if r else '利用'
            counts[status] = counts.get(status, 0) + 1
            if uses_therapy(facility):
                start_t = r.start_time if r else (t.time if t else None)
                end_t = _slot_end(facility, r.start_time) if r else None
                did = _activities_text(t.activities) if t else ''
                staff = t.staff_label if t else ''
                note = (r.note if r else '') or ''
            else:
                start_t = t.entry_time if t else (r.start_time if r else None)
                end_t = t.exit_time if t else None
                did = (t.activity_name if t else '') or ''
                staff = str(t.author) if t and t.author else ''
                note = (r.note if r else '') or ''
            rows.append([str(d.day), WEEKDAYS[d.weekday()], status, fmt_time(start_t), fmt_time(end_t), staff, did, note, ''])
        d += timedelta(days=1)
    cert = _cert_for(beneficiary, start)
    meta = [
        ('事業所名', facility.name), ('事業所番号', facility.office_number or ''),
        (f'{terms["beneficiary"]}氏名', f'{beneficiary.full_name}（{beneficiary.full_name_kana}）'),
        ('受給者証番号', cert.certificate_number if cert else ''),
        ('支給量', f'{cert.granted_days} 日/月' if cert else ''),
        ('対象月', f'{year}年{month}月'),
        ('利用日数', f'{counts.get("利用", 0)} 日'), ('欠席', f'{counts.get("欠席", 0)} 回'),
        ('取消', f'{counts.get("取消", 0)} 回'), ('予定（実績未入力）', f'{counts.get("予定", 0)} 日'),
    ]
    source = '提供状況は予約の実績（来た・欠席・キャンセル）から、開始時刻は予約の時間枠から' if uses_reservations(facility) else '提供状況は記録のある日'
    source += '、担当・やったことは療育記録から転記しています。' if uses_therapy(facility) else '、時間・活動は日誌から転記しています。'
    return Report(
        key='service_record', title='サービス提供実績記録票',
        subtitle=f'{year}年{month}月　{beneficiary.full_name}', meta=meta,
        columns=['日', '曜日', '提供状況', '開始時間', '終了時間', '担当', 'やったこと・活動', '備考', '保護者確認'],
        rows=rows, landscape=True, filename=f'実績記録票_{beneficiary.full_name}_{year}{month:02d}',
        widths=[5, 5, 8, 8, 8, 10, 30, 16, 10], footnote=source,
    )


# =============================================
# 2. 出欠・利用実績一覧（施設・月次）
# =============================================
def attendance_summary(facility, year, month):
    if uses_reservations(facility):
        return _attendance_summary_ryoiku(facility, year, month)
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


def _attendance_summary_ryoiku(facility, year, month):
    from therapy.models import TherapyRecord
    terms = _terms(facility)
    start, end = month_range(year, month)
    beneficiaries = list(Beneficiary.objects.filter(facility=facility, status=Beneficiary.STATUS_ACTIVE).prefetch_related('recipient_certificates'))
    stats = {}
    for row in (_reservations(facility, start, end).values('beneficiary_id').annotate(
            total=Count('pk'), attended=Count('pk', filter=Q(attendance='attended')), absent=Count('pk', filter=Q(attendance='absent')),
            cancelled=Count('pk', filter=Q(attendance='cancelled')), pending=Count('pk', filter=Q(attendance='')))):
        stats[row['beneficiary_id']] = row
    if uses_therapy(facility):
        record_counts = dict(TherapyRecord.objects.filter(facility=facility, date__range=(start, end))
                             .values_list('beneficiary_id').annotate(n=Count('pk')).values_list('beneficiary_id', 'n'))
        rec_label = '療育記録'
    else:
        record_counts = dict(DailyRecord.objects.filter(facility=facility, date__range=(start, end))
                             .values_list('beneficiary_id').annotate(n=Count('pk')).values_list('beneficiary_id', 'n'))
        rec_label = '日誌作成'
    rows, total_attended = [], 0
    for b in beneficiaries:
        st = stats.get(b.pk, {})
        cert = _cert_for(b, start)
        total_attended += st.get('attended', 0)
        rows.append([b.full_name, b.full_name_kana, str(cert.granted_days) if cert else '', str(st.get('total', 0)), str(st.get('attended', 0)),
                     str(st.get('absent', 0)), str(st.get('cancelled', 0)), str(st.get('pending', 0)), str(record_counts.get(b.pk, 0))])
    return Report(
        key='attendance_summary', title='出欠・利用実績一覧', subtitle=f'{year}年{month}月',
        meta=[('事業所名', facility.name), ('対象月', f'{year}年{month}月'),
              (f'在籍{terms["beneficiary"]}数', f'{len(beneficiaries)} 名'), ('来所延べ人数', f'{total_attended} 人日')],
        columns=['氏名', 'ふりがな', '支給量', '予約', '来た', '欠席', '取消', '実績未入力', rec_label],
        rows=rows, landscape=True, filename=f'出欠利用実績_{year}{month:02d}',
        footnote='予約・来た・欠席・取消は予約管理の実績から集計しています。実績未入力は、予約はあるが実績を付けていない日の数です。',
    )


# =============================================
# 3. 業務日誌（施設・日別）
# =============================================
def daily_journal(facility, day):
    if uses_therapy(facility):
        return _daily_journal_ryoiku(facility, day)
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


def _daily_journal_ryoiku(facility, day):
    from therapy.models import TherapyRecord
    terms = _terms(facility)
    records = list(TherapyRecord.objects.filter(facility=facility, date=day).select_related('beneficiary', 'staff')
                   .order_by('time', 'beneficiary__last_name_kana', 'beneficiary__first_name_kana', 'pk'))
    attended = _reservations(facility, day, day).filter(attendance='attended').count() if uses_reservations(facility) else len(records)
    rows = [[r.beneficiary.full_name, fmt_time(r.time), r.staff_label, _activities_text(r.activities), r.body, r.situation] for r in records]
    return Report(
        key='daily_journal', title='業務日誌（療育記録）', subtitle=f'{day.year}年{day.month}月{day.day}日（{WEEKDAYS[day.weekday()]}）',
        meta=[('事業所名', facility.name), ('日付', f'{fmt_date(day)}（{WEEKDAYS[day.weekday()]}）'),
              ('来所人数', f'{attended} 名'), ('記録件数', f'{len(records)} 件')],
        columns=[terms['beneficiary'], '時刻', '担当', 'やったこと', '記録（したこと）', '様子'],
        rows=rows, landscape=True, filename=f'業務日誌_{day.strftime("%Y%m%d")}', widths=[11, 6, 9, 20, 30, 24],
    )


# =============================================
# 4. 日誌一覧（利用者別・期間）
# =============================================
def beneficiary_records(facility, beneficiary, start, end):
    if uses_therapy(facility):
        return _beneficiary_records_ryoiku(facility, beneficiary, start, end)
    terms = _terms(facility)
    records = list(DailyRecord.objects.filter(facility=facility, beneficiary=beneficiary, date__range=(start, end))
                   .select_related('author').prefetch_related('activity_tags').order_by('date', 'pk'))
    rows = []
    for r in records:
        activity = r.activity_name or '・'.join(t.name for t in r.activity_tags.all())
        rows.append([fmt_date(r.date), WEEKDAYS[r.date.weekday()], activity, '\n'.join(t for t in (_questions_text(r.question_answers), _viewpoints_text(r.activity_viewpoints)) if t),
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


def _beneficiary_records_ryoiku(facility, beneficiary, start, end):
    from therapy.models import TherapyRecord
    terms = _terms(facility)
    records = list(TherapyRecord.objects.filter(facility=facility, beneficiary=beneficiary, date__range=(start, end))
                   .select_related('staff').order_by('date', 'time', 'pk'))
    rows = [[fmt_date(r.date), WEEKDAYS[r.date.weekday()], fmt_time(r.time), r.staff_label, _activities_text(r.activities), r.body, r.situation]
            for r in records]
    return Report(
        key='beneficiary_records', title='療育記録一覧',
        subtitle=f'{beneficiary.full_name}　{fmt_date(start)} 〜 {fmt_date(end)}',
        meta=[('事業所名', facility.name), (f'{terms["beneficiary"]}氏名', f'{beneficiary.full_name}（{beneficiary.full_name_kana}）'),
              ('期間', f'{fmt_date(start)} 〜 {fmt_date(end)}'), ('件数', f'{len(records)} 件')],
        columns=['日付', '曜日', '時刻', '担当', 'やったこと', '記録（したこと）', '様子'],
        rows=rows, landscape=True,
        filename=f'療育記録_{beneficiary.full_name}_{start.strftime("%Y%m%d")}-{end.strftime("%Y%m%d")}',
        widths=[9, 4, 6, 9, 20, 28, 24],
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


def report_kinds(facility):
    """帳票の一覧（療育の事業所は、日誌 → 療育記録、予定表 → 予約 の言い方に）"""
    kinds = {k: dict(v) for k, v in REPORT_KINDS.items()}
    if uses_therapy(facility):
        kinds['beneficiary_records'].update(title='療育記録一覧', desc='{beneficiary}ごとに、期間内の療育記録（時刻・担当・やったこと・記録・様子）を時系列で並べます。モニタリングや相談支援事業所への提出に。')
        kinds['daily_journal'].update(title='業務日誌（日別）', desc='その日の療育記録（時刻・担当・やったこと・記録・様子）を{beneficiary}ごとに 1 枚にまとめます。')
        kinds['service_record'].update(desc='{beneficiary}ごとに、その月の提供状況（予約の実績）・時間・担当・やったことを日付順に並べます。保護者確認欄つき。')
    if uses_reservations(facility):
        kinds['attendance_summary'].update(desc='在籍中の{beneficiary}全員の、その月の予約・来た・欠席・取消と記録の数を一覧にします。')
    return kinds


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

"""
書いていない日誌（来所したのに、その日の日誌が無い・下書きのまま）

- 来所のもとは、予定（schedules.ScheduledVisit：欠席・振替以外）と、予約を使う事業所では予約（reservations.Reservation：
  予約で実績が「来た」か未入力、キャンセル待ちで「来た」）。きょうの分は入れない（まだ書く時間があるので、日誌の週間カレンダーで見る）
- 退所・卒業した利用者は入れない
- 療育記録を使う事業所（日誌はお試し）では、ホームのカードとメールには出さない（enabled）。日誌の画面の一覧は見られる
- 使うところ：ホームのカード（facilities/views.py）、日誌の「書いていない日誌」の画面（records/views.py の UnwrittenListView）、
  期限のお知らせメール（facilities/reminders.py の KIND_RECORD）
"""
import datetime

DEFAULT_DAYS = 14          # 何日前までさかのぼるか（ホーム・メール）
DAY_CHOICES = (7, 14, 30, 60)
STATE_NONE = 'none'        # 日誌が無い
STATE_DRAFT = 'draft'      # 下書きのまま
STATE_LABELS = {STATE_NONE: '未作成', STATE_DRAFT: '下書きのまま'}
WEEKDAYS = '月火水木金土日'


def enabled(facility):
    """ホーム・メールで知らせる事業所か（日誌を本番で使っている＝療育記録を使っていない）"""
    return facility is not None and not getattr(facility, 'use_therapy_record', False)


class Row:
    __slots__ = ('date', 'beneficiary', 'state', 'record', 'days_ago')

    def __init__(self, date, beneficiary, state, record, today):
        self.date, self.beneficiary, self.state, self.record = date, beneficiary, state, record
        self.days_ago = (today - date).days

    @property
    def label(self):
        return STATE_LABELS[self.state]

    @property
    def weekday(self):
        return WEEKDAYS[self.date.weekday()]

    @property
    def when(self):
        return 'きのう' if self.days_ago == 1 else f'{self.days_ago} 日前'

    def __repr__(self):
        return f'<Row {self.date} {self.beneficiary} {self.state}>'


def visit_days(facility, start, end):
    """来所した (利用者ID, 日付) の集合（予定と、予約を使う事業所では予約から）"""
    from schedules.models import ScheduledVisit
    days = set(ScheduledVisit.objects.filter(facility=facility, date__range=(start, end))
               .exclude(status__in=(ScheduledVisit.STATUS_ABSENT, ScheduledVisit.STATUS_TRANSFERRED))
               .values_list('beneficiary_id', 'date'))
    if getattr(facility, 'use_reservation', False):
        from django.db.models import Q
        from reservations.models import Reservation
        came = Q(status=Reservation.STATUS_CONFIRMED, attendance__in=('', Reservation.ATT_ATTENDED)) | Q(attendance=Reservation.ATT_ATTENDED)
        days |= set(Reservation.objects.filter(facility=facility, date__range=(start, end), beneficiary__isnull=False)
                    .filter(came).values_list('beneficiary_id', 'date'))
    return days


def collect(facility, today=None, days=DEFAULT_DAYS, include_draft=True):
    """書いていない日誌の行を、新しい日から順に（同じ日はかなの順で）返す"""
    from beneficiaries.models import Beneficiary
    from .models import DailyRecord

    today = today or datetime.date.today()
    end = today - datetime.timedelta(days=1)
    start = today - datetime.timedelta(days=days)
    came = visit_days(facility, start, end)
    if not came:
        return []
    people = {b.pk: b for b in Beneficiary.objects.filter(facility=facility, status=Beneficiary.STATUS_ACTIVE,
                                                          pk__in={bid for bid, _d in came})}
    records = {(r.beneficiary_id, r.date): r for r in DailyRecord.objects.filter(
        facility=facility, date__range=(start, end), beneficiary_id__in=people.keys())}
    rows = []
    for bid, day in came:
        b = people.get(bid)
        if b is None:
            continue
        r = records.get((bid, day))
        if r is None:
            rows.append(Row(day, b, STATE_NONE, None, today))
        elif include_draft and r.status == DailyRecord.STATUS_DRAFT:
            rows.append(Row(day, b, STATE_DRAFT, r, today))
    rows.sort(key=lambda x: (-x.date.toordinal(), x.beneficiary.last_name_kana or 'ん', x.beneficiary.pk))
    return rows


def by_date(rows):
    """[{'date', 'weekday', 'rows'}]（新しい日から）"""
    out = []
    for r in rows:
        if not out or out[-1]['date'] != r.date:
            out.append({'date': r.date, 'weekday': r.weekday, 'rows': []})
        out[-1]['rows'].append(r)
    return out

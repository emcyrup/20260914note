"""
月予約利用希望と月間予定表（時間枠で予約する事業所）。

- 利用希望：利用者ごと・月ごとに「可能な日時の枠に○」と希望利用回数
- 割り当て：希望の枠の中から、希望回数ぶんの予約を作る（1枠の人数を超えない・同じ日に2回は入れない・
  月の中でなるべく間をあける）。すでに確定している予約は残し、足りないぶんだけ足す
- 月間予定表：週（日〜土）× 時間枠 × 1枠の人数のマス目に、確定した予約の名前を並べる
"""
import calendar
import datetime

from django.db import transaction

from beneficiaries.models import Beneficiary
from config.jp_holidays import holiday_name

from . import services
from .models import MonthlyRequest, Reservation, ReservationNotice, ReservationSetting, hour_label

WEEK_JP = services.WEEK_JP


def month_range(year, month):
    first = datetime.date(year, month, 1)
    return first, datetime.date(year, month, calendar.monthrange(year, month)[1])


def month_days(year, month):
    first, last = month_range(year, month)
    return [first + datetime.timedelta(days=i) for i in range((last - first).days + 1)]


def next_month(year, month):
    return (year + 1, 1) if month == 12 else (year, month + 1)


def prev_month(year, month):
    return (year - 1, 12) if month == 1 else (year, month - 1)


# ---------------------------------------------------------------- 利用希望の用紙（画面・PDF・顧客ページ共通）
def request_grid(facility, year, month, setting=None, request=None, closed=None):
    """
    月の各日の行。列は枠のある時刻ぜんぶ（平日・土日祝の和）。
    その日に無い時刻（平日の9時、土日祝の18時、休業日）は active=False で網掛けにする。
    request があれば、○の付いている枠に wished=True。
    """
    setting = setting or services.get_setting(facility)
    first, last = month_range(year, month)
    closed = services.closed_dates(facility, first, last) if closed is None else closed
    hours = setting.all_slot_hours()
    rows = []
    for day in month_days(year, month):
        day_hours = [] if services.is_closed(facility, day, setting, closed) else setting.slot_hours(day)
        wish = request.wish_of(day) if request is not None else None
        cells = []
        for h in hours:
            active = h in day_hours
            wished = active and (wish == 'all' or (isinstance(wish, list) and h in wish))
            cells.append({'hour': h, 'label': hour_label(h), 'active': active, 'wished': wished})
        rows.append({
            'date': day, 'iso': day.isoformat(), 'weekday': WEEK_JP[day.weekday()],
            'closed': not day_hours, 'holiday': holiday_name(day),
            'is_sunday': day.weekday() == 6, 'is_saturday': day.weekday() == 5,
            'all_wished': bool(day_hours) and wish == 'all',
            'cells': cells, 'hours': day_hours,
        })
    return {'hours': hours, 'hour_labels': [hour_label(h) for h in hours], 'rows': rows}


def wishes_from_post(post, facility, year, month, setting=None):
    """
    画面のチェックから wishes を作る。
    名前は all_<iso>（終日）と h_<iso>_<時>（枠）。終日が付いていれば時刻は見ない。
    """
    setting = setting or services.get_setting(facility)
    wishes = {}
    for day in month_days(year, month):
        iso = day.isoformat()
        if post.get(f'all_{iso}'):
            wishes[iso] = 'all'
            continue
        hours = sorted({int(v) for v in post.getlist(f'h_{iso}') if str(v).isdigit()})
        hours = [h for h in hours if h in setting.slot_hours(day)]
        if hours:
            wishes[iso] = hours
    return wishes


def save_request(facility, beneficiary, year, month, desired_count, wishes, note='',
                 source=MonthlyRequest.SOURCE_STAFF, customer=None, user=None):
    """利用希望を1枚保存する（同じ利用者・同じ月のものは書き換える）"""
    req, _ = MonthlyRequest.objects.update_or_create(
        beneficiary=beneficiary, year=year, month=month,
        defaults={'facility': facility, 'desired_count': max(0, min(int(desired_count or 0), 99)),
                  'wishes': wishes, 'note': (note or '')[:200], 'source': source,
                  'customer': customer, 'created_by': user},
    )
    return req


# ---------------------------------------------------------------- 月のまとめ
def month_reservations(facility, year, month, statuses=(Reservation.STATUS_CONFIRMED,)):
    first, last = month_range(year, month)
    return list(Reservation.objects.filter(facility=facility, date__gte=first, date__lte=last, status__in=statuses)
                .select_related('beneficiary', 'customer').order_by('date', 'start_time', 'created_at'))


def request_rows(facility, year, month, setting=None):
    """
    利用希望の一覧（在籍中の利用者ぜんぶ。用紙が来ていない人も出す）。
    希望回数・○の枠数・確定した予約数・残り（希望−確定）。
    """
    setting = setting or services.get_setting(facility)
    requests = {r.beneficiary_id: r for r in MonthlyRequest.objects.filter(facility=facility, year=year, month=month)}
    confirmed = {}
    for res in month_reservations(facility, year, month):
        if res.beneficiary_id:
            confirmed[res.beneficiary_id] = confirmed.get(res.beneficiary_id, 0) + 1
    rows = []
    for b in Beneficiary.objects.filter(facility=facility, status=Beneficiary.STATUS_ACTIVE):
        req = requests.get(b.pk)
        done = confirmed.get(b.pk, 0)
        desired = req.desired_count if req else 0
        cert = b.latest_certificate
        rows.append({
            'beneficiary': b, 'request': req, 'desired': desired,
            'slots': req.slot_count(setting) if req else 0,
            'confirmed': done, 'remaining': max(desired - done, 0),
            'granted': cert.granted_days if cert else None,
            'granted_left': (cert.granted_days - done) if cert and cert.granted_days else None,
        })
    # 用紙の出ている人を先に、残りが多い順
    rows.sort(key=lambda r: (r['request'] is None, -r['remaining'], r['beneficiary'].last_name_kana))
    return rows


# ---------------------------------------------------------------- 割り当て
class AssignResult:
    def __init__(self):
        self.made = []        # 作った予約
        self.short = []       # (利用希望, 足りなかった回数)
        self.notices = []     # 積んだ通知

    @property
    def summary(self):
        text = f'{len(self.made)} 件の予約を作りました。'
        if self.short:
            names = '、'.join(f'{r.beneficiary.full_name}（あと{n}回）' for r, n in self.short)
            text += f' 希望の枠に空きが足りなかった方：{names}。'
        return text


def _spread_score(day, taken_days):
    """すでに入っている日からの近さ（遠いほど良い）。まだ無ければ月の前半を少し優先"""
    if not taken_days:
        return 99
    return min(abs((day - d).days) for d in taken_days)


@transaction.atomic
def assign_month(facility, year, month, setting=None, base='', notify=True, only=None):
    """
    月予約利用希望から予約を作る。

    - すでに確定している予約は残し、希望回数に足りないぶんだけ足す
    - 同じ日に同じ利用者を2回入れない。休業日・枠のない時刻は使わない
    - 回りながら1回ずつ入れる（回数の少ない人から）。枠は「その人のほかの利用日から遠い日」→
      「空きの多い枠」→「早い日」の順で選ぶので、月の中でなるべく間があき、枠も偏りにくい
    - 通知は利用者（の連絡先）ごとに1通にまとめて積む（`notify=False` なら積まない）
    - `only` に利用希望の一覧を渡すと、その人たちだけを割り当てる
    """
    setting = setting or services.get_setting(facility)
    result = AssignResult()
    if not setting.slot_mode:
        return result
    first, last = month_range(year, month)
    closed = services.closed_dates(facility, first, last)
    requests = list(only) if only is not None else list(
        MonthlyRequest.objects.filter(facility=facility, year=year, month=month).select_related('beneficiary'))
    requests = [r for r in requests if r.beneficiary.status == Beneficiary.STATUS_ACTIVE]
    if not requests:
        return result

    # 枠の使用数と、利用者ごとの予約日（確定・キャンセル待ちとも「その日は入れない」）
    list(Reservation.objects.select_for_update().filter(facility=facility, date__gte=first, date__lte=last))
    used = {}
    taken = {}
    confirmed_count = {}
    for res in month_reservations(facility, year, month, statuses=Reservation.ACTIVE_STATUSES):
        if res.status == Reservation.STATUS_CONFIRMED:
            if res.hour is not None:
                used[(res.date, res.hour)] = used.get((res.date, res.hour), 0) + 1
            if res.beneficiary_id:
                confirmed_count[res.beneficiary_id] = confirmed_count.get(res.beneficiary_id, 0) + 1
        if res.beneficiary_id:
            taken.setdefault(res.beneficiary_id, set()).add(res.date)
    need = {}
    candidates = {}
    for req in requests:
        need[req.pk] = max(req.desired_count - confirmed_count.get(req.beneficiary_id, 0), 0)
        cands = []
        for day in req.wished_days():
            if not (first <= day <= last) or services.is_closed(facility, day, setting, closed):
                continue
            for h in req.wish_hours(day, setting):
                cands.append((day, h))
        candidates[req.pk] = cands

    made_for = {}
    progress = True
    while progress:
        progress = False
        # 入っている回数が少ない人から（同じなら希望の枠が少ない人から：選べる余地の少ない人を先に）
        order = sorted((r for r in requests if need[r.pk] > 0),
                       key=lambda r: (len(taken.get(r.beneficiary_id, ())), len(candidates[r.pk]), r.pk))
        for req in order:
            days_taken = taken.setdefault(req.beneficiary_id, set())
            options = [(d, h) for d, h in candidates[req.pk]
                       if d not in days_taken and used.get((d, h), 0) < setting.slot_capacity]
            if not options:
                continue
            options.sort(key=lambda dh: (-_spread_score(dh[0], days_taken),
                                         used.get(dh, 0), dh[0], dh[1]))
            day, hour = options[0]
            try:
                res, _ = services.create_reservation(
                    facility, req.beneficiary, day, source=Reservation.SOURCE_REQUEST,
                    customer=req.customer, start_time=datetime.time(hour, 0), notify=False)
            except services.ReservationError:
                candidates[req.pk] = [c for c in candidates[req.pk] if c != (day, hour)]
                continue
            if res.status != Reservation.STATUS_CONFIRMED:
                res.delete()           # 数え違い（同時操作）。この枠はあきらめる
                used[(day, hour)] = setting.slot_capacity
                continue
            used[(day, hour)] = used.get((day, hour), 0) + 1
            days_taken.add(day)
            need[req.pk] -= 1
            result.made.append(res)
            made_for.setdefault(req.pk, []).append(res)
            progress = True

    for req in requests:
        if need[req.pk] > 0:
            result.short.append((req, need[req.pk]))
        if notify and made_for.get(req.pk):
            result.notices.append(_queue_month_notice(facility, setting, req, made_for[req.pk]))
    return result


def _queue_month_notice(facility, setting, req, reservations):
    """割り当ての結果を1通にまとめる（例：10月のご利用日が決まりました）"""
    lines = [f'{req.month}月の {req.beneficiary.full_name}さんのご利用日が決まりました。']
    for res in sorted(reservations, key=lambda r: (r.date, r.start_time or datetime.time())):
        lines.append(f'{services.jp_date(res.date)} {res.time_label}')
    if req.desired_count > len(reservations):
        lines.append(f'（ご希望 {req.desired_count} 回のうち {len(reservations)} 回です。'
                     'ほかの日はあらためてご相談させてください）')
    lines.append(setting.sign_text)
    customer = req.customer or services.customer_for(facility, req.beneficiary)
    return services.queue_notice(facility, ReservationNotice.KIND_ACCEPTED, setting, customer=customer,
                                 day=datetime.date(req.year, req.month, 1), body='\n'.join(lines))


# ---------------------------------------------------------------- 月間予定表
def month_schedule(facility, year, month, setting=None):
    """
    月間予定表のマス目。週は日曜はじまり（用紙に合わせる）。
    各日に、枠のある時刻ぜんぶの行（その日に無い時刻・休業日は網掛け）と、1枠の人数ぶんの名前の箱。
    """
    setting = setting or services.get_setting(facility)
    first, last = month_range(year, month)
    closed = services.closed_dates(facility, first, last)
    hours = setting.all_slot_hours()
    by_slot = {}
    waiting = {}
    for res in month_reservations(facility, year, month, statuses=Reservation.ACTIVE_STATUSES):
        key = (res.date, res.hour)
        if res.status == Reservation.STATUS_CONFIRMED:
            by_slot.setdefault(key, []).append(res)
        else:
            waiting.setdefault(key, []).append(res)

    cal = calendar.Calendar(firstweekday=6)     # 日曜はじまり
    weeks = []
    for week in cal.monthdatescalendar(year, month):
        days = []
        for day in week:
            in_month = day.month == month
            day_hours = setting.slot_hours(day) if in_month and not services.is_closed(facility, day, setting, closed) else []
            slots = []
            for h in hours:
                rows = by_slot.get((day, h), [])
                boxes = [r for r in rows[:setting.slot_capacity]]
                boxes += [None] * (setting.slot_capacity - len(boxes))
                slots.append({'hour': h, 'label': hour_label(h), 'active': h in day_hours,
                              'reservations': rows, 'boxes': boxes,
                              'waiting': waiting.get((day, h), []),
                              'over': rows[setting.slot_capacity:]})
            days.append({'date': day, 'in_month': in_month, 'closed': in_month and not day_hours,
                         'holiday': holiday_name(day) if in_month else '',
                         'is_sunday': day.weekday() == 6, 'is_saturday': day.weekday() == 5,
                         'slots': slots, 'count': sum(len(s['reservations']) for s in slots)})
        weeks.append({'days': days, 'start': week[0], 'end': week[-1]})
    return {'year': year, 'month': month, 'hours': hours, 'hour_labels': [hour_label(h) for h in hours],
            'weeks': weeks, 'capacity': setting.slot_capacity, 'closed_text': setting.closed_weekdays_text}

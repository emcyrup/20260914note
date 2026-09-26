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
    ng_mode = request is not None and request.is_ng_mode
    ng = set(request.ng_dates or []) if ng_mode else set()
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
            'ng': day.isoformat() in ng,
            'cells': cells, 'hours': day_hours,
        })
    return {'hours': hours, 'hour_labels': [hour_label(h) for h in hours], 'rows': rows, 'ng_mode': ng_mode}


def wish_mode_from_post(post):
    return MonthlyRequest.WISH_NG if post.get('wish_mode') == MonthlyRequest.WISH_NG else MonthlyRequest.WISH_OK


def ng_from_post(post, year, month):
    """「来られない日」のチェック（名前は ng_<iso>）→ ISO 日付のリスト"""
    return [day.isoformat() for day in month_days(year, month) if post.get(f'ng_{day.isoformat()}')]


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
                 source=MonthlyRequest.SOURCE_STAFF, customer=None, user=None,
                 wish_mode=MonthlyRequest.WISH_OK, ng_dates=None):
    """
    利用希望を1枚保存する（同じ利用者・同じ月のものは書き換える）。
    wish_mode='ng' のときは wishes は使わず、ng_dates（来られない日）以外を終日可能として扱う
    """
    ng_mode = wish_mode == MonthlyRequest.WISH_NG
    req, _ = MonthlyRequest.objects.update_or_create(
        beneficiary=beneficiary, year=year, month=month,
        defaults={'facility': facility, 'desired_count': max(0, min(int(desired_count or 0), 99)),
                  'wishes': {} if ng_mode else wishes, 'wish_mode': wish_mode,
                  'ng_dates': sorted(set(ng_dates or [])) if ng_mode else [],
                  'note': (note or '')[:200], 'source': source,
                  'customer': customer, 'created_by': user},
    )
    return req


def wish_summary(req, setting):
    """保存や送信のあとに出す短い説明：「希望 n 回・○ m 枠」または「希望 n 回・来られない日 k 日」"""
    if req.is_ng_mode:
        return f'希望 {req.desired_count} 回・来られない日 {len(req.ng_days())} 日（それ以外は終日可能）'
    return f'希望 {req.desired_count} 回・○ {req.slot_count(setting)} 枠'


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
    - **時刻を指定した希望を先に、「終日」（時刻の指定なし）の希望をあとに**割り当てる。
      時刻の決まっている子の枠を、どの時刻でもよい子が先に埋めてしまわないように
    - 日にちは「その人のほかの利用日から遠い日」→「早い日」の順で選ぶ（月の中でなるべく間があく）
    - 時刻は**その日の空いている一番早い枠**（午前から詰める）。終日の希望はその日の全部の枠から、
      時刻を指定した希望は○の付いた枠の中から選ぶ
    - 回りながら1回ずつ入れる（回数の少ない人から、同じなら選べる枠の少ない人から）
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
    fixed = {}      # 時刻を指定した希望の枠
    flexible = {}   # 終日（時刻の指定なし）の希望の枠
    for req in requests:
        need[req.pk] = max(req.desired_count - confirmed_count.get(req.beneficiary_id, 0), 0)
        fixed[req.pk], flexible[req.pk] = [], []
        for day in req.wished_days():
            if not (first <= day <= last) or services.is_closed(facility, day, setting, closed):
                continue
            target = flexible if req.wish_of(day) == 'all' else fixed
            target[req.pk].extend((day, h) for h in req.wish_hours(day, setting))

    made_for = {}

    def run(candidates):
        progress = True
        while progress:
            progress = False
            order = sorted((r for r in requests if need[r.pk] > 0),
                           key=lambda r: (len(taken.get(r.beneficiary_id, ())), len(candidates[r.pk]), r.pk))
            for req in order:
                days_taken = taken.setdefault(req.beneficiary_id, set())
                options = [(d, h) for d, h in candidates[req.pk]
                           if d not in days_taken and used.get((d, h), 0) < setting.slot_capacity]
                if not options:
                    continue
                # 日にちは間があく順、同じ日の中では早い時刻から（午前から詰める）
                options.sort(key=lambda dh: (-_spread_score(dh[0], days_taken), dh[0], dh[1]))
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

    run(fixed)       # 1. 時刻を指定した希望
    run(flexible)    # 2. 終日の希望（空いている早い枠から）

    for req in requests:
        if need[req.pk] > 0:
            result.short.append((req, need[req.pk]))
        if notify and made_for.get(req.pk):
            result.notices.append(_queue_month_notice(facility, setting, req, made_for[req.pk]))
    return result


MONTH_NOTICE_HEAD = '{month}月の {name}さんのご利用日が決まりました。'


def _month_notice_body(setting, month, beneficiary, reservations, desired_count=0):
    lines = [MONTH_NOTICE_HEAD.format(month=month, name=beneficiary.full_name)]
    for res in sorted(reservations, key=lambda r: (r.date, r.start_time or datetime.time())):
        lines.append(f'{services.jp_date(res.date)} {res.time_label}')
    if desired_count > len(reservations):
        lines.append(f'（ご希望 {desired_count} 回のうち {len(reservations)} 回です。'
                     'ほかの日はあらためてご相談させてください）')
    lines.append(setting.sign_text)
    return '\n'.join(lines)


def _queue_month_notice(facility, setting, req, reservations):
    """割り当ての結果を1通にまとめる（例：10月のご利用日が決まりました）"""
    body = _month_notice_body(setting, req.month, req.beneficiary, reservations, req.desired_count)
    customer = req.customer or services.customer_for(facility, req.beneficiary)
    return services.queue_notice(facility, ReservationNotice.KIND_ACCEPTED, setting, customer=customer,
                                 day=datetime.date(req.year, req.month, 1), body=body)


def refresh_month_notice(facility, beneficiary, year, month, setting=None):
    """
    まだ送っていない「ご利用日が決まりました」を、いまの予約に合わせて書き直す。
    予定表で入れ替え・移動をしたあとに呼ぶ（古い日時のまま保護者に届かないように）。
    予約が無くなっていれば、そのお知らせは消す。送信ずみのものは触らない。
    """
    if beneficiary is None:
        return None
    setting = setting or services.get_setting(facility)
    head = MONTH_NOTICE_HEAD.format(month=month, name=beneficiary.full_name)
    notice = (ReservationNotice.objects
              .filter(facility=facility, kind=ReservationNotice.KIND_ACCEPTED, reservation__isnull=True,
                      date=datetime.date(year, month, 1), body__startswith=head,
                      status__in=(ReservationNotice.STATUS_PENDING, ReservationNotice.STATUS_MANUAL))
              .order_by('-created_at').first())
    if notice is None:
        return None
    mine = [r for r in month_reservations(facility, year, month) if r.beneficiary_id == beneficiary.pk]
    if not mine:
        notice.delete()
        return None
    req = MonthlyRequest.objects.filter(beneficiary=beneficiary, year=year, month=month).first()
    notice.body = _month_notice_body(setting, month, beneficiary, mine, req.desired_count if req else 0)
    notice.save(update_fields=['body'])
    return notice


# ---------------------------------------------------------------- 予定表の手直し（入れ替え・移動）
def _other_on_day(res, day):
    """その人の、同じ日のほかの有効な予約があるか"""
    qs = Reservation.objects.filter(facility=res.facility, date=day,
                                    status__in=Reservation.ACTIVE_STATUSES).exclude(pk=res.pk)
    if res.beneficiary_id:
        return qs.filter(beneficiary_id=res.beneficiary_id).exists()
    return qs.filter(beneficiary__isnull=True, guest_name=res.guest_name).exists()


@transaction.atomic
def swap_reservations(res_a, res_b):
    """
    月間予定表で2人の枠を入れ替える（日にちと時刻を交換する）。
    1人ずつ入れ替えるので、どの枠の人数も変わらない。保護者へのお知らせは積まない。
    """
    if res_a.pk == res_b.pk:
        raise services.ReservationError('同じ予約を選んでいます。')
    if res_a.facility_id != res_b.facility_id:
        raise services.ReservationError('別の事業所の予約とは入れ替えられません。')
    if res_a.status != Reservation.STATUS_CONFIRMED or res_b.status != Reservation.STATUS_CONFIRMED:
        raise services.ReservationError('確定している予約どうしだけ入れ替えられます（キャンセル待ちは除く）。')
    if res_a.beneficiary_id and res_a.beneficiary_id == res_b.beneficiary_id:
        raise services.ReservationError('同じ利用者どうしは入れ替えられません。')
    list(Reservation.objects.select_for_update().filter(facility=res_a.facility, date__in={res_a.date, res_b.date}))
    a_slot, b_slot = (res_a.date, res_a.start_time), (res_b.date, res_b.start_time)
    if a_slot == b_slot:
        return res_a, res_b
    if res_a.date != res_b.date:
        if _other_on_day(res_a, res_b.date):
            raise services.ReservationError(
                f'{services.jp_date(res_b.date)} には {res_a.display_name} さんのほかの予約があります。')
        if _other_on_day(res_b, res_a.date):
            raise services.ReservationError(
                f'{services.jp_date(res_a.date)} には {res_b.display_name} さんのほかの予約があります。')
    res_a.date, res_a.start_time = b_slot
    res_b.date, res_b.start_time = a_slot
    res_a.save(update_fields=['date', 'start_time', 'updated_at'])
    res_b.save(update_fields=['date', 'start_time', 'updated_at'])
    return res_a, res_b


def move_on_schedule(res, day, hour):
    """月間予定表の空いている枠へ移す。保護者へのお知らせは積まない（キャンセル待ちの繰り上げはする）"""
    if res.status != Reservation.STATUS_CONFIRMED:
        raise services.ReservationError('確定している予約だけ移せます（キャンセル待ちは除く）。')
    with transaction.atomic():
        list(Reservation.objects.select_for_update().filter(facility=res.facility, date__in={res.date, day}))
        if services.is_closed(res.facility, day):
            raise services.ReservationError(f'{services.jp_date(day)} は休業日のため移せません。')
        st = services.day_state(res.facility, day)
        slot = next((x for x in st.get('slots', ()) if x['hour'] == hour), None)
        if slot is None:
            raise services.ReservationError(f'{services.jp_date(day)} {hour}時 の枠はありません。')
        mine = 1 if (res.date == day and res.hour == hour) else 0
        if slot['confirmed'] - mine >= slot['capacity']:
            raise services.ReservationError(f'{services.jp_date(day)} {hour}時 の枠は満員です。入れ替えを使ってください。')
        return services.move_reservation(res, day, start_time=datetime.time(hour, 0), notify=False)


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


# ---------------------------------------------------------------- 保護者に入力してもらう（URL の配信）
WISH_NOTICE_OPEN = (ReservationNotice.STATUS_PENDING, ReservationNotice.STATUS_MANUAL)


def wish_page_url(customer, year, month, base=''):
    """顧客ページの「利用希望」の欄を、その月を選んだ状態で開く URL"""
    url = services.customer_page_url(customer, base)
    return f'{url}?wish={year}-{month:02d}#wishCard' if url else ''


def wish_ask_body(setting, customer, children, year, month, url, deadline=None):
    names = '・'.join(f'{b.full_name}さん' for b in children)
    lines = [f'{customer.name} 様',
             f'{month}月のご利用希望の入力をお願いします（{names}）。',
             '下のページで、ご希望の回数と、来られる日時に○を付けて送ってください。']
    if deadline:
        lines.append(f'{services.jp_date(deadline)}までにお願いします。')
    lines += [url, setting.sign_text]
    return '\n'.join(line for line in lines if line)


def wish_targets(facility, year, month, base=''):
    """
    入力のお願いを送る相手の一覧（顧客＝連絡先ごと）。
    在籍中の利用者を担当している顧客だけ。お子さまごとに、利用希望が届いているかを付ける。
    顧客（連絡先）が1人もいない利用者は `no_contact` に分けて返す。
    """
    active = {b.pk: b for b in Beneficiary.objects.filter(facility=facility, status=Beneficiary.STATUS_ACTIVE)}
    requests = {r.beneficiary_id: r for r in MonthlyRequest.objects.filter(facility=facility, year=year, month=month)}
    asks = {}
    for n in (ReservationNotice.objects.filter(facility=facility, kind=ReservationNotice.KIND_WISH,
                                               date=datetime.date(year, month, 1))
              .order_by('created_at')):
        asks[n.customer_id] = n                       # いちばん新しいお願い
    from .models import Customer
    rows, covered = [], set()
    for c in Customer.objects.filter(facility=facility).prefetch_related('children').order_by('kana', 'name'):
        kids = [active[b.pk] for b in c.children.all() if b.pk in active]
        if not kids:
            continue
        covered.update(b.pk for b in kids)
        kid_rows = [{'beneficiary': b, 'request': requests.get(b.pk)} for b in kids]
        rows.append({
            'customer': c, 'children': kid_rows, 'kids': kids,
            'missing': [k['beneficiary'] for k in kid_rows if k['request'] is None],
            'url': wish_page_url(c, year, month, base),
            'line': c.can_notify, 'ask': asks.get(c.pk),
        })
    no_contact = [b for pk, b in active.items() if pk not in covered]
    return rows, no_contact


def ask_for_wishes(facility, year, month, setting=None, base='', deadline=None, only_missing=True, customers=None):
    """
    保護者へ「利用希望の入力のお願い」を積む（連絡先ごとに1通、入力ページの URL 付き）。
    LINE でつながっている方は送信待ち、そうでない方は「コピーして送る」になる。
    同じ月のまだ送っていないお願いは、新しい文面に置き換える。
    `only_missing` なら、まだ利用希望が届いていないお子さまがいる方だけ。
    """
    setting = setting or services.get_setting(facility)
    rows, _ = wish_targets(facility, year, month, base)
    first = datetime.date(year, month, 1)
    made = []
    for row in rows:
        c = row['customer']
        if customers is not None and c.pk not in customers:
            continue
        kids = row['missing'] if only_missing else row['kids']
        if not kids or not row['url']:
            continue
        ReservationNotice.objects.filter(facility=facility, kind=ReservationNotice.KIND_WISH, customer=c,
                                         date=first, status__in=WISH_NOTICE_OPEN).delete()
        body = wish_ask_body(setting, c, kids, year, month, row['url'], deadline)
        made.append(services.queue_notice(facility, ReservationNotice.KIND_WISH, setting, customer=c,
                                          day=first, body=body))
    return made


def make_contacts(facility, beneficiaries):
    """
    顧客（連絡先）が無い利用者に、保護者台帳から連絡先を作る。
    主連絡先の保護者（いなければ最初の保護者）の名前・電話・LINE を使う。
    同じ LINE の顧客がすでにいれば、そこにお子さまを足す（きょうだい）。保護者の登録も無ければ「○○さんの保護者」。
    """
    from .models import Customer
    made, joined = [], []
    for b in beneficiaries:
        g = (b.guardians.filter(is_primary=True).first() or b.guardians.order_by('pk').first())
        line_id = (g.line_user_id if g and g.line_linked else '') or ''
        existing = Customer.objects.filter(facility=facility, line_user_id=line_id).first() if line_id else None
        if existing is None and g and g.phone:
            existing = Customer.objects.filter(facility=facility, phone=g.phone,
                                               name=g.full_name).first()
        if existing is not None:
            existing.children.add(b)
            joined.append(existing)
            continue
        c = Customer.objects.create(
            facility=facility, name=(g.full_name if g else f'{b.full_name}さんの保護者')[:100],
            phone=(g.phone if g else '')[:20], line_user_id=line_id,
            note='利用希望のお願いのときに保護者台帳から作成')
        c.children.add(b)
        made.append(c)
    return made, joined


def tell_staff_wish(facility, setting, req, again=False, decided=False):
    """保護者のページから利用希望が届いたことを、スタッフの LINE グループへ（設定しているときだけ）。保護者の名前は書かない"""
    if not setting.notify_group_id:
        return None
    what = '直されました' if again else '届きました'
    line = (f'【利用希望】{req.month}月 {req.beneficiary.full_name}さんの利用希望が{what}'
            f'（希望 {req.desired_count} 回・○ {req.slot_count(setting)} 枠）')
    if decided:
        line += '。この月の予定はすでに組んであります。確かめてください'
    return ReservationNotice.objects.create(
        facility=facility, kind=ReservationNotice.KIND_GROUP, date=datetime.date(req.year, req.month, 1),
        to_line_id=setting.notify_group_id, body=line, status=ReservationNotice.STATUS_PENDING)

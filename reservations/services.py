"""
予約の決まりごと（画面に触れない処理）。

- 1日の枠は事業所の設定（既定10人）。休業曜日・臨時休業日は枠0
- 満枠のときはキャンセル待ち（設定で断ることもできる）
- 取消で空いたら、キャンセル待ちを申し込み順に確定する
- 同じ日・同じ利用者の重複は受け付けない
- 通知は積むだけ。送信は職員が押す
"""
import datetime
import re

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import (BookingRequest, ClosedDate, Customer, LineInbox, MonthlyRequest, Reservation,  # noqa: F401
                     ReservationNotice, ReservationSetting, hour_label)

WEEK_JP = ['月', '火', '水', '木', '金', '土', '日']


def jp_date(d):
    return f'{d.month}月{d.day}日({WEEK_JP[d.weekday()]})' if d else ''


def get_setting(facility):
    setting, _ = ReservationSetting.objects.get_or_create(facility=facility)
    return setting


# ---------------------------------------------------------------- 枠
def closed_dates(facility, start, end):
    return set(ClosedDate.objects.filter(facility=facility, date__gte=start, date__lte=end)
               .values_list('date', flat=True))


def is_closed(facility, day, setting=None, closed=None):
    setting = setting or get_setting(facility)
    if day.weekday() in setting.closed_weekday_numbers():
        return True
    if closed is not None:
        return day in closed
    return ClosedDate.objects.filter(facility=facility, date=day).exists()


def capacity_of(facility, day, setting=None, closed=None):
    setting = setting or get_setting(facility)
    if is_closed(facility, day, setting, closed):
        return 0
    return setting.slot_capacity_of(day) if setting.slot_mode else setting.capacity


def slot_states(setting, day, rows, closed=False):
    """
    時間枠ごとの予約数（時間枠で予約する事業所）。
    rows はその日の有効な予約。開始時刻のない予約は最初の枠に数えない（枠の外として一覧にだけ出る）。
    """
    hours = [] if closed else setting.slot_hours(day)
    slots = []
    for h in hours:
        mine = [r for r in rows if r.hour == h]
        confirmed = [r for r in mine if r.status == Reservation.STATUS_CONFIRMED]
        waiting = [r for r in mine if r.status == Reservation.STATUS_WAITLIST]
        slots.append({
            'hour': h, 'label': hour_label(h), 'capacity': setting.slot_capacity,
            'confirmed': len(confirmed), 'waiting': len(waiting),
            'remaining': max(setting.slot_capacity - len(confirmed), 0),
            'full': len(confirmed) >= setting.slot_capacity,
            'reservations': sorted(mine, key=lambda r: (r.status != Reservation.STATUS_CONFIRMED, r.created_at)),
        })
    return slots


def slot_state(facility, day, hour, setting=None):
    """1つの時間枠の状態（無い枠なら None）"""
    setting = setting or get_setting(facility)
    rows = list(Reservation.objects.filter(facility=facility, date=day, status__in=Reservation.ACTIVE_STATUSES))
    for st in slot_states(setting, day, rows, closed=is_closed(facility, day, setting)):
        if st['hour'] == hour:
            return st
    return None


def day_state(facility, day, setting=None, closed=None):
    """その日の枠・予約数・キャンセル待ち数・残り（時間枠のときは枠ごとの内訳 slots も）"""
    setting = setting or get_setting(facility)
    cap = capacity_of(facility, day, setting, closed)
    rows = list(Reservation.objects.filter(facility=facility, date=day, status__in=Reservation.ACTIVE_STATUSES))
    confirmed = sum(1 for r in rows if r.status == Reservation.STATUS_CONFIRMED)
    waiting = sum(1 for r in rows if r.status == Reservation.STATUS_WAITLIST)
    state = {
        'date': day, 'capacity': cap, 'confirmed': confirmed, 'waiting': waiting,
        'remaining': max(cap - confirmed, 0), 'closed': cap == 0,
        'full': cap > 0 and confirmed >= cap,
    }
    if setting.slot_mode:
        state['slots'] = slot_states(setting, day, rows, closed=cap == 0)
        state['open_slots'] = [st for st in state['slots'] if not st['full']]
    return state


def month_states(facility, first_day, last_day):
    """月の各日の残枠（1回のクエリでまとめる）"""
    setting = get_setting(facility)
    closed = closed_dates(facility, first_day, last_day)
    counts = {}
    for r in Reservation.objects.filter(facility=facility, date__gte=first_day, date__lte=last_day,
                                        status__in=Reservation.ACTIVE_STATUSES).values('date', 'status'):
        c = counts.setdefault(r['date'], {'confirmed': 0, 'waiting': 0})
        c['confirmed' if r['status'] == Reservation.STATUS_CONFIRMED else 'waiting'] += 1
    states = {}
    day = first_day
    while day <= last_day:
        cap = capacity_of(facility, day, setting, closed)
        c = counts.get(day, {'confirmed': 0, 'waiting': 0})
        states[day] = {
            'date': day, 'capacity': cap, 'confirmed': c['confirmed'], 'waiting': c['waiting'],
            'remaining': max(cap - c['confirmed'], 0), 'closed': cap == 0,
            'full': cap > 0 and c['confirmed'] >= cap,
        }
        day += datetime.timedelta(days=1)
    return states


# ---------------------------------------------------------------- 通知
def notice_body(kind, setting, day=None, child='', extra=''):
    d = jp_date(day)
    texts = {
        ReservationNotice.KIND_ACCEPTED: f'{d} {child}さんのご予約を承りました。',
        ReservationNotice.KIND_WAITLISTED: f'{d} {child}さんはキャンセル待ちです。空きが出ましたらお知らせします。',
        ReservationNotice.KIND_PROMOTED: f'{d} {child}さんのご予約が確定しました。',
        ReservationNotice.KIND_CANCELLED: f'{d} {child}さんのご予約を取り消しました。',
        ReservationNotice.KIND_MOVED: f'{child}さんのご予約を {d} に変更しました。',
        ReservationNotice.KIND_DECLINED: f'{d} は満席のため、{child}さんのご予約をお受けできませんでした。',
        ReservationNotice.KIND_REMINDER: f'明日 {d} は {child}さんのご利用日です。',
        ReservationNotice.KIND_VACANCY: f'{d} に空きが出ました。',
    }
    body = texts.get(kind, extra)
    if extra and kind != ReservationNotice.KIND_VACANCY:
        body = f'{body}\n{extra}'
    elif extra:
        body = f'{body}\n{extra}'
    return f'{body}\n{setting.sign_text}'


def queue_notice(facility, kind, setting=None, customer=None, reservation=None, day=None, body=None, to_line_id=''):
    """通知を送信待ちに積む。宛先が LINE 未連携なら「手渡し」として残す（文面をコピーして送る）"""
    setting = setting or get_setting(facility)
    child = reservation.display_name if reservation else ''
    if reservation is not None and reservation.start_time and kind != ReservationNotice.KIND_VACANCY:
        child = f'{reservation.time_label} {child}'
    text = body if body is not None else notice_body(kind, setting, day or (reservation.date if reservation else None), child)
    if kind == ReservationNotice.KIND_GROUP:
        status = ReservationNotice.STATUS_PENDING if to_line_id else ReservationNotice.STATUS_MANUAL
    else:
        to_line_id = to_line_id or (customer.line_user_id if customer and customer.can_notify else '')
        status = ReservationNotice.STATUS_PENDING if to_line_id else ReservationNotice.STATUS_MANUAL
    return ReservationNotice.objects.create(
        facility=facility, customer=customer, reservation=reservation, kind=kind,
        date=day or (reservation.date if reservation else None),
        to_line_id=to_line_id, body=text, status=status,
    )


def queue_group_notice(facility, setting, day, child, what):
    """予約の増減をスタッフのLINEグループへ。保護者の名前・連絡先は書かない"""
    if not setting.notify_group_id:
        return None
    st = day_state(facility, day, setting)
    line = (f'【予約の増減】{jp_date(day)} {child} {what} / '
            f'予約 {st["confirmed"]}/{st["capacity"]}名・残り{st["remaining"]}枠')
    if st['waiting']:
        line += f'・キャンセル待ち{st["waiting"]}件'
    return ReservationNotice.objects.create(
        facility=facility, kind=ReservationNotice.KIND_GROUP, date=day,
        to_line_id=setting.notify_group_id, body=line, status=ReservationNotice.STATUS_PENDING,
    )


def customer_for(facility, beneficiary):
    return Customer.objects.filter(facility=facility, children=beneficiary).first()


# ---------------------------------------------------------------- 予約
class ReservationError(Exception):
    pass


def parse_hour(value):
    """'10' / '10:00' → datetime.time(10, 0)。読めなければ None"""
    if value is None or value == '':
        return None
    if isinstance(value, datetime.time):
        return value.replace(second=0, microsecond=0)
    text = str(value).strip().replace('：', ':')
    try:
        if ':' in text:
            h, m = text.split(':', 1)
            return datetime.time(int(h), int(m or 0))
        return datetime.time(int(text), 0)
    except (TypeError, ValueError):
        return None


@transaction.atomic
def create_reservation(facility, beneficiary, day, source=Reservation.SOURCE_STAFF, customer=None,
                       note='', guest_name='', start_time=None, notify=True):
    """
    予約を1件作る。戻り値は (予約, 通知)。

    - 空きがあれば確定、満席ならキャンセル待ち（設定で断ることもできる）
    - 休業日と重複は断る（例外）
    - `guest_name` を渡すと、まだ台帳にいない方のぶんとして席を押さえる
      （職員があとから利用者に結びつける）
    - 時間枠で予約する事業所では `start_time`（その日の枠の時刻）が要る。空きの判定はその枠で行う
    - `notify=False` なら通知を積まない（月間予定表の割り当てで、まとめて1通にするとき）

    同じ日に同時の申し込みが来ても枠を超えないよう、その日の行を先に押さえてから数える。
    先に押さえた申し込みが勝ち、あとの申し込みはキャンセル待ちかお断りになる。
    """
    setting = get_setting(facility)
    guest_name = (guest_name or '').strip()[:100]
    start_time = parse_hour(start_time) if setting.slot_mode else None
    if beneficiary is None and not guest_name:
        raise ReservationError('だれの予約かが分かりません。')
    if beneficiary is not None and beneficiary.facility_id != facility.pk:
        raise ReservationError('この事業所の利用者ではありません。')
    who = beneficiary.full_name if beneficiary is not None else guest_name

    # 同じ日に同時の申し込みが来ても枠を超えないよう、その日の行を先に押さえる
    list(Reservation.objects.select_for_update().filter(facility=facility, date=day))
    taken = Reservation.objects.filter(facility=facility, date=day, status__in=Reservation.ACTIVE_STATUSES)
    if beneficiary is not None:
        already = taken.filter(beneficiary=beneficiary).exists()
    else:
        already = taken.filter(beneficiary__isnull=True, guest_name=guest_name).exists()
    if already:
        raise ReservationError(f'{jp_date(day)} の {who} さんの予約はすでにあります。')
    if is_closed(facility, day, setting):
        raise ReservationError(f'{jp_date(day)} は休業日のため予約を受け付けられません。')

    customer = customer or (customer_for(facility, beneficiary) if beneficiary is not None else None)
    st = day_state(facility, day, setting)
    if setting.slot_mode:
        if start_time is None:
            raise ReservationError('時間の枠を選んでください。')
        slot = next((x for x in st['slots'] if x['hour'] == start_time.hour), None)
        if slot is None:
            raise ReservationError(f'{jp_date(day)} {hour_label(start_time.hour)} の枠はありません。')
        st = slot
    if not st['full']:
        status, kind, what = Reservation.STATUS_CONFIRMED, ReservationNotice.KIND_ACCEPTED, '予約'
    elif setting.allow_waitlist:
        status, kind, what = Reservation.STATUS_WAITLIST, ReservationNotice.KIND_WAITLISTED, 'キャンセル待ち'
    else:
        status, kind, what = Reservation.STATUS_DECLINED, ReservationNotice.KIND_DECLINED, '満席で受付不可'

    try:
        res = Reservation.objects.create(facility=facility, beneficiary=beneficiary, guest_name=guest_name,
                                         customer=customer, date=day, start_time=start_time, status=status,
                                         source=source, note=note[:200])
    except IntegrityError:
        raise ReservationError(f'{jp_date(day)} の {who} さんの予約はすでにあります。')
    if not notify:
        return res, None
    notice = queue_notice(facility, kind, setting, customer=customer, reservation=res)
    queue_group_notice(facility, setting, day, who, what)
    return res, notice


@transaction.atomic
def cancel_reservation(res, notify=True, base=''):
    """
    予約を取り消す。キャンセル待ちがいれば申し込み順に繰り上げ、
    満枠だった日に空きが残ったら、その日に予約のない顧客へ「空きが出ました」を積む。
    """
    facility = res.facility
    setting = get_setting(facility)
    was_full = day_state(facility, res.date, setting)['full']
    was_confirmed = res.status == Reservation.STATUS_CONFIRMED
    res.status = Reservation.STATUS_CANCELLED
    res.cancelled_at = timezone.now()
    res.save(update_fields=['status', 'cancelled_at', 'updated_at'])
    if notify:
        queue_notice(facility, ReservationNotice.KIND_CANCELLED, setting, customer=res.customer, reservation=res)
        queue_group_notice(facility, setting, res.date, res.display_name, '取消')

    promoted = promote_waitlist(facility, res.date, setting) if was_confirmed else []
    if was_full and was_confirmed and not promoted:
        offer_vacancy(facility, res.date, setting, exclude_customer=res.customer, base=base)
    return promoted


@transaction.atomic
def move_reservation(res, new_day, note=None, base='', start_time=None, notify=True):
    """
    予約の日にち（時間枠のときは時刻も）を変える（職員の操作）。
    もとの日はキャンセル待ちを繰り上げ、新しい日は空きがなければキャンセル待ちにする。
    `start_time` を渡さなければ、時刻はそのまま。
    `notify=False` なら、顧客への変更のお知らせ・グループへの増減・空きのお知らせを積まない
    （月間予定表を組んでいる途中の手直しで、確定前の予定を保護者に流さないため）。
    """
    facility = res.facility
    setting = get_setting(facility)
    if not res.is_active:
        raise ReservationError('取消・お断りの予約は日にちを変えられません。')
    if note is not None:
        res.note = note[:200]
    old_day = res.date
    new_time = res.start_time
    if setting.slot_mode and start_time is not None:
        new_time = parse_hour(start_time)
        if new_time is None:
            raise ReservationError('時間の枠を選んでください。')
    if new_day == old_day and new_time == res.start_time:
        res.save(update_fields=['note', 'updated_at'])
        return res

    list(Reservation.objects.select_for_update().filter(facility=facility, date__in=[old_day, new_day]))
    if is_closed(facility, new_day, setting):
        raise ReservationError(f'{jp_date(new_day)} は休業日のため変更できません。')
    if new_day != old_day and Reservation.objects.filter(
            beneficiary=res.beneficiary, date=new_day,
            status__in=Reservation.ACTIVE_STATUSES).exclude(pk=res.pk).exists():
        raise ReservationError(f'{jp_date(new_day)} の {res.display_name} さんの予約はすでにあります。')

    st = day_state(facility, new_day, setting)
    if setting.slot_mode:
        slot = next((x for x in st['slots'] if new_time is not None and x['hour'] == new_time.hour), None)
        if slot is None:
            raise ReservationError(f'{jp_date(new_day)} {new_time.strftime("%H:%M") if new_time else ""} の枠はありません。')
        # 同じ枠の中で自分を数えない
        mine = 1 if (new_day == old_day and res.hour == new_time.hour
                     and res.status == Reservation.STATUS_CONFIRMED) else 0
        st = dict(slot, full=(slot['confirmed'] - mine) >= slot['capacity'])
    if st['full'] and not setting.allow_waitlist:
        raise ReservationError(f'{jp_date(new_day)} は満席で、キャンセル待ちを受けない設定です。')
    was_confirmed = res.status == Reservation.STATUS_CONFIRMED
    old_state = day_state(facility, old_day, setting)
    res.date = new_day
    res.start_time = new_time
    res.status = Reservation.STATUS_WAITLIST if st['full'] else Reservation.STATUS_CONFIRMED
    res.save(update_fields=['date', 'start_time', 'status', 'note', 'updated_at'])
    if new_day == old_day:
        # 同じ日の別の枠へ。空いた枠のキャンセル待ちを繰り上げる
        if was_confirmed:
            promote_waitlist(facility, old_day, setting)
        if notify:
            queue_notice(facility, ReservationNotice.KIND_MOVED, setting, customer=res.customer, reservation=res)
        return res

    if not notify:
        if was_confirmed:
            promote_waitlist(facility, old_day, setting)
        return res
    queue_notice(facility, ReservationNotice.KIND_MOVED, setting, customer=res.customer, reservation=res)
    queue_group_notice(facility, setting, old_day, res.display_name, f'{jp_date(new_day)} へ変更')
    queue_group_notice(facility, setting, new_day, res.display_name,
                       f'{jp_date(old_day)} から変更（{res.get_status_display()}）')
    if was_confirmed:
        was_full = old_state['full']
        promoted = promote_waitlist(facility, old_day, setting)
        if was_full and not promoted:
            offer_vacancy(facility, old_day, setting, exclude_customer=res.customer, base=base)
    return res


@transaction.atomic
def link_reservation(res, beneficiary):
    """台帳に未登録のまま押さえていた予約を、利用者に結びつける"""
    if not res.is_guest:
        raise ReservationError('この予約はすでに利用者に結びついています。')
    if beneficiary.facility_id != res.facility_id:
        raise ReservationError('この事業所の利用者ではありません。')
    if Reservation.objects.filter(beneficiary=beneficiary, date=res.date,
                                  status__in=Reservation.ACTIVE_STATUSES).exclude(pk=res.pk).exists():
        raise ReservationError(f'{jp_date(res.date)} の {beneficiary.full_name} さんの予約はすでにあります。')
    res.beneficiary = beneficiary
    res.guest_name = ''
    if res.customer_id is None:
        res.customer = customer_for(res.facility, beneficiary)
    res.save(update_fields=['beneficiary', 'guest_name', 'customer', 'updated_at'])
    return res


@transaction.atomic
def delete_reservation(res, base=''):
    """予約を記録ごと消す（間違って入れたときの後始末）。通知は顧客へは送らない"""
    facility = res.facility
    setting = get_setting(facility)
    day, name, customer = res.date, res.display_name, res.customer
    was_confirmed = res.status == Reservation.STATUS_CONFIRMED
    was_full = day_state(facility, day, setting)['full']
    ReservationNotice.objects.filter(reservation=res, status=ReservationNotice.STATUS_PENDING).delete()
    res.delete()
    queue_group_notice(facility, setting, day, name, '削除')
    if not was_confirmed:
        return []
    promoted = promote_waitlist(facility, day, setting)
    if was_full and not promoted:
        offer_vacancy(facility, day, setting, exclude_customer=customer, base=base)
    return promoted


def promote_waitlist(facility, day, setting=None):
    """空いたぶんだけ、キャンセル待ちを申し込み順に確定する"""
    setting = setting or get_setting(facility)
    promoted = []
    while True:
        st = day_state(facility, day, setting)
        if st['remaining'] <= 0:
            break
        waiting = (Reservation.objects.filter(facility=facility, date=day, status=Reservation.STATUS_WAITLIST)
                   .order_by('created_at', 'pk'))
        if setting.slot_mode:
            # 空きのある枠のキャンセル待ちだけを、申し込み順に
            open_hours = {x['hour'] for x in st['open_slots']}
            nxt = next((w for w in waiting if w.hour in open_hours), None)
        else:
            nxt = waiting.first()
        if nxt is None:
            break
        nxt.status = Reservation.STATUS_CONFIRMED
        nxt.save(update_fields=['status', 'updated_at'])
        queue_notice(facility, ReservationNotice.KIND_PROMOTED, setting, customer=nxt.customer, reservation=nxt)
        queue_group_notice(facility, setting, day, nxt.display_name, '繰り上げ確定')
        promoted.append(nxt)
    return promoted


def offer_vacancy(facility, day, setting=None, exclude_customer=None, base='', force=False):
    """
    満席だった日に空きが出たことを、顧客へお知らせする（送信待ちに積む）。

    宛先は LINE 連携ずみで、その日に予約のない顧客。取り消した本人は除く。
    同じ日・同じ顧客に送信待ちが残っていれば重ねない。
    `force=True` は職員が画面から押したとき（設定で自動お知らせを止めていても積む）。
    """
    setting = setting or get_setting(facility)
    if not (force or setting.notify_vacancy):
        return []
    st = day_state(facility, day, setting)
    if st['remaining'] <= 0 or st['closed']:
        return []
    booked = set(Reservation.objects.filter(facility=facility, date=day,
                                            status__in=Reservation.ACTIVE_STATUSES)
                 .values_list('customer_id', flat=True))
    already = set(ReservationNotice.objects.filter(facility=facility, date=day,
                                                  kind=ReservationNotice.KIND_VACANCY,
                                                  status=ReservationNotice.STATUS_PENDING)
                  .values_list('customer_id', flat=True))
    made = []
    extra = f'残り{st["remaining"]}枠です。'
    for c in Customer.objects.filter(facility=facility, notify_enabled=True).exclude(line_user_id=''):
        if c.pk in booked or c.pk in already or (exclude_customer and c.pk == exclude_customer.pk):
            continue
        body = notice_body(ReservationNotice.KIND_VACANCY, setting, day, extra=extra)
        url = customer_page_url(c, base)
        if url:
            body = f'{body}\nご予約はこちらから\n{url}'
        made.append(queue_notice(facility, ReservationNotice.KIND_VACANCY, setting, customer=c, day=day, body=body))
    return made


def announce_after_capacity_change(facility, old_capacity, setting=None, base=''):
    """
    1日の枠を増やしたとき、満席で受けられなかった日にお知らせを出す。
    キャンセル待ちがいれば先に繰り上げ、それでも空きが残る日だけお知らせする。
    """
    setting = setting or get_setting(facility)
    if setting.capacity <= old_capacity:
        return []
    today = datetime.date.today()
    end = today + datetime.timedelta(days=setting.booking_until_days)
    counts = {}
    for row in Reservation.objects.filter(facility=facility, date__gte=today, date__lte=end,
                                          status=Reservation.STATUS_CONFIRMED).values('date'):
        counts[row['date']] = counts.get(row['date'], 0) + 1
    made = []
    for day, taken in sorted(counts.items()):
        if taken < old_capacity:
            continue      # もともと空きがあった日は、お知らせの対象にしない
        promote_waitlist(facility, day, setting)
        made += offer_vacancy(facility, day, setting, base=base)
    return made


def queue_reminders(facility, day):
    """その日の予約をしている顧客へ「前日のお知らせ」を積む"""
    setting = get_setting(facility)
    made = []
    for res in (Reservation.objects.filter(facility=facility, date=day, status=Reservation.STATUS_CONFIRMED)
                .select_related('beneficiary', 'customer')):
        if ReservationNotice.objects.filter(reservation=res, kind=ReservationNotice.KIND_REMINDER).exists():
            continue
        made.append(queue_notice(facility, ReservationNotice.KIND_REMINDER, setting,
                                 customer=res.customer, reservation=res))
    return made


def bookable(facility, day, setting=None, today=None):
    """
    顧客が自分で予約を入れられる日かどうか。(可否, 理由) を返す。
    受付できる日の範囲・休業日・満枠（キャンセル待ちを受けない設定のとき）で判断する。
    """
    setting = setting or get_setting(facility)
    today = today or datetime.date.today()
    if not setting.public_booking:
        return False, 'この事業所では、ページからの予約は受け付けていません。'
    start, end = setting.booking_window(today)
    if day < start:
        return False, (f'{jp_date(day)} は受付の締め切りを過ぎています。'
                       if setting.booking_from_days else 'その日は受け付けられません。')
    if day > end:
        return False, f'{jp_date(day)} はまだ受け付けていません（{setting.booking_until_days}日先までです）。'
    if is_closed(facility, day, setting):
        return False, f'{jp_date(day)} は休業日です。'
    st = day_state(facility, day, setting)
    if st['full'] and not setting.allow_waitlist:
        return False, f'{jp_date(day)} は満席のため、ご予約をお受けできませんでした。'
    return True, ''


def vacancy_text(facility, start, days=7):
    """空き状況の文面（コピーして使う）"""
    setting = get_setting(facility)
    end = start + datetime.timedelta(days=days - 1)
    states = month_states(facility, start, end)
    lines = ['空き状況のお知らせです。']
    for day in sorted(states):
        st = states[day]
        if st['closed']:
            lines.append(f'{jp_date(day)} 休業')
        elif st['remaining'] > 0:
            lines.append(f'{jp_date(day)} 残り{st["remaining"]}枠')
        else:
            lines.append(f'{jp_date(day)} 満席')
    lines.append(setting.sign_text)
    return '\n'.join(lines)


# ---------------------------------------------------------------- LINE の文の読み取り
RESERVE_WORDS = ['予約', '利用したい', 'お願いします', '行きます', '追加']
CANCEL_WORDS = ['キャンセル', '取消', '取り消し', '休みます', '欠席', '休み']
CHECK_WORDS = ['空き', '空いて', '空いてますか', '残り', '状況']
REGISTER_MARK = '【登録】'
_DATE_PATTERNS = [
    re.compile(r'(?P<y>\d{4})\s*[-/]\s*(?P<m>\d{1,2})\s*[-/]\s*(?P<d>\d{1,2})'),
    re.compile(r'(?P<m>\d{1,2})\s*[/月]\s*(?P<d>\d{1,2})'),
]
_RELATIVE_DAYS = [(['明後日', 'あさって'], 2), (['明日', 'あした'], 1), (['今日', '本日'], 0)]


def _find_dates(raw, today):
    """文中の日付をぜんぶ拾う（「9/20 9/21 予約します」のような書き方に合わせる）"""
    found = []
    for words, delta in _RELATIVE_DAYS:
        if any(w in raw for w in words):
            found.append(today + datetime.timedelta(days=delta))

    rest = raw
    for pattern in _DATE_PATTERNS:
        spans = []
        for m in pattern.finditer(rest):
            groups = m.groupdict()
            year = int(groups['y']) if groups.get('y') else today.year
            month, day_num = int(groups['m']), int(groups['d'])
            try:
                day = datetime.date(year, month, day_num)
            except ValueError:
                continue
            # 年を書かない書き方は、半年以上前になるなら来年のことと読む
            if not groups.get('y') and day < today - datetime.timedelta(days=180):
                try:
                    day = datetime.date(year + 1, month, day_num)
                except ValueError:
                    continue
            found.append(day)
            spans.append(m.span())
        # 年つきで読めたところは、あらためて月日として拾わない
        for lo, hi in reversed(spans):
            rest = rest[:lo] + ' ' * (hi - lo) + rest[hi:]

    out = []
    for day in found:
        if day not in out:
            out.append(day)
    return sorted(out)


def parse_message(text, today=None):
    """
    届いた文から「何をしたいか」と「いつ」を読み取る。
    決められないところは空のままにする（決めつけない）。
    """
    today = today or datetime.date.today()
    raw = (text or '').strip()
    result = {'intent': 'unknown', 'date': None, 'dates': [], 'name': '', 'fields': {}, 'text': raw}
    if not raw:
        return result

    if REGISTER_MARK in raw:
        result['intent'] = 'register'
        for line in raw.splitlines():
            if ':' in line or '：' in line:
                key, _, value = line.replace('：', ':').partition(':')
                result['fields'][key.strip().lstrip(REGISTER_MARK).strip()] = value.strip()
        result['name'] = result['fields'].get('お名前', '') or result['fields'].get('氏名', '')
        return result

    if any(w in raw for w in CANCEL_WORDS):
        result['intent'] = 'cancel'
    elif any(w in raw for w in RESERVE_WORDS):
        result['intent'] = 'reserve'
    elif any(w in raw for w in CHECK_WORDS):
        result['intent'] = 'check'

    result['dates'] = _find_dates(raw, today)
    result['date'] = result['dates'][0] if result['dates'] else None
    return result


def guess_beneficiary(facility, text, customer=None):
    """
    どの利用者かを、文中の名前 → 顧客の担当が1人だけ の順で決める。
    決められなければ None（職員が画面で選ぶ）。
    """
    from beneficiaries.models import Beneficiary
    plain = (text or '').replace(' ', '').replace('　', '')
    for b in Beneficiary.objects.filter(facility=facility, status=Beneficiary.STATUS_ACTIVE):
        names = [f'{b.last_name}{b.first_name}', b.last_name, b.first_name,
                 f'{b.last_name_kana}{b.first_name_kana}'.strip()]
        if any(n and n.replace(' ', '') in plain for n in names):
            return b
    if customer is not None:
        children = list(customer.children.all())
        if len(children) == 1:
            return children[0]
    return None


def inbox_row(entry, facility):
    """受信1件を画面用にほどく"""
    customer = (Customer.objects.filter(facility=facility, line_user_id=entry.line_user_id).first()
                if entry.line_user_id else None)
    parsed = parse_message(entry.text)
    beneficiary = guess_beneficiary(facility, entry.text, customer) if parsed['intent'] in ('reserve', 'cancel') else None
    return {'entry': entry, 'customer': customer, 'parsed': parsed, 'beneficiary': beneficiary}


def receive(facility, message_id, text, source_type=LineInbox.SOURCE_USER,
            line_user_id='', group_id='', display_name=''):
    """受信箱に積む（同じメッセージIDは二重に積まない）"""
    entry, created = LineInbox.objects.get_or_create(
        facility=facility, message_id=message_id,
        defaults={'text': text or '', 'source_type': source_type, 'line_user_id': line_user_id or '',
                  'group_id': group_id or '', 'display_name': display_name or ''},
    )
    return entry, created


# ---------------------------------------------------------------- LINE からその場で反映
def public_base(base=''):
    """
    顧客向けページのアドレスの入口。
    `RESERVATION_SITE_URL` を決めていればそれを使い、無ければ呼び出し元が渡した入口
    （LINE の Webhook を受けたときの自分のホスト）を使う。
    """
    from django.conf import settings as django_settings
    site = (getattr(django_settings, 'RESERVATION_SITE_URL', '') or '').strip()
    return (site or base or '').rstrip('/')


def _page_url(name, arg, base=''):
    from django.urls import NoReverseMatch, reverse
    root = public_base(base)
    if not root or not arg:
        return ''
    try:
        return root + reverse(name, args=[arg])
    except NoReverseMatch:
        return ''


def customer_page_url(customer, base=''):
    """顧客専用ページ（予約の確認・申し込み・取り消し）の URL"""
    return _page_url('reservations_public:customer', customer.token if customer else '', base)


def calendar_page_url(setting, base=''):
    """空き状況のページ（公開しているときだけ）の URL"""
    if setting is None or not setting.public_calendar:
        return ''
    return _page_url('reservations_public:calendar', setting.public_token, base)


def _mark_replied(notice):
    """その場で返事をした通知は「送信ずみ」にする（同じ内容を二重に送らない）"""
    if notice is None or notice.kind == ReservationNotice.KIND_GROUP:
        return
    notice.status = ReservationNotice.STATUS_SENT
    notice.sent_at = timezone.now()
    notice.error_message = 'LINEの返信でお伝えしました'
    notice.save(update_fields=['status', 'sent_at', 'error_message'])


MAX_DATES_PER_MESSAGE = 5


def apply_message(facility, text, customer=None, staff=False, today=None, setting=None, base=''):
    """
    LINE に届いた文を、その場で予約に反映する。

    戻り値は (反映したか, 返事の文)。
    読み取れないところが1つでもあれば (False, '') を返し、受信箱に積んで職員が確かめる。
    - 顧客（公式LINE）: 自分が担当する利用者の、受付できる範囲の日だけ
    - 職員（スタッフのグループ）: 名前を書いてもらう。受付の範囲は見ない
    """
    setting = setting or get_setting(facility)
    today = today or datetime.date.today()
    parsed = parse_message(text, today)
    dates = parsed['dates'][:MAX_DATES_PER_MESSAGE]

    if parsed['intent'] == 'check':
        text = vacancy_text(facility, dates[0] if dates else today)
        url = customer_page_url(customer, base) if customer is not None else ''
        return True, f'{text}\nご予約はこちらから\n{url}' if url else text

    if parsed['intent'] not in ('reserve', 'cancel') or not dates:
        return False, ''

    beneficiary = guess_beneficiary(facility, text, None if staff else customer)
    if beneficiary is None:
        return False, ''
    if not staff:
        if customer is None:
            return False, ''
        children = list(customer.children.all())
        if children and beneficiary.pk not in {b.pk for b in children}:
            return False, ''   # 担当していない利用者の名前は、職員が確かめる
        target_customer = customer
    else:
        target_customer = customer_for(facility, beneficiary)

    source = Reservation.SOURCE_GROUP if staff else Reservation.SOURCE_LINE
    lines = []
    for day in dates:
        if parsed['intent'] == 'cancel':
            res = Reservation.objects.filter(facility=facility, beneficiary=beneficiary, date=day,
                                             status__in=Reservation.ACTIVE_STATUSES).first()
            if res is None:
                lines.append(f'{jp_date(day)} {beneficiary.full_name}さんのご予約は見つかりませんでした。')
                continue
            cancel_reservation(res)
            _mark_replied(ReservationNotice.objects.filter(reservation=res,
                                                           kind=ReservationNotice.KIND_CANCELLED)
                          .order_by('-pk').first())
            lines.append(f'{jp_date(day)} {beneficiary.full_name}さんのご予約を取り消しました。')
            continue

        if not staff:
            ok, reason = bookable(facility, day, setting, today)
            if not ok:
                lines.append(reason)
                continue
        try:
            res, notice = create_reservation(facility, beneficiary, day, source=source,
                                             customer=target_customer)
        except ReservationError as e:
            lines.append(str(e))
            continue
        _mark_replied(notice)
        if res.status == Reservation.STATUS_CONFIRMED:
            lines.append(f'{jp_date(day)} {beneficiary.full_name}さんのご予約を承りました。')
        elif res.status == Reservation.STATUS_WAITLIST:
            lines.append(f'{jp_date(day)} {beneficiary.full_name}さんはキャンセル待ちです。'
                         '空きが出ましたらお知らせします。')
        else:
            lines.append(f'{jp_date(day)} は満席のため、お受けできませんでした。')

    if not lines:
        return False, ''
    if staff:
        return True, '\n'.join(lines)
    url = customer_page_url(target_customer, base)
    if url:
        lines.append(f'ご予約の確認・取り消しはこちら\n{url}')
    lines.append(setting.sign_text)
    return True, '\n'.join(lines)


# ---------------------------------------------------------------- 空き状況ページからの申し込み
def request_matches(facility, req):
    """
    申し込みに書かれたお子さまの名前から、在籍している利用者を探す。
    1人に決まらなければ None（職員が画面で選ぶ）。
    """
    from beneficiaries.models import Beneficiary
    plain = f'{req.child_name}'.replace(' ', '').replace('　', '')
    if not plain:
        return None
    found = []
    for b in Beneficiary.objects.filter(facility=facility, status=Beneficiary.STATUS_ACTIVE):
        names = [f'{b.last_name}{b.first_name}', f'{b.last_name_kana}{b.first_name_kana}'.strip()]
        if any(n and n.replace(' ', '') == plain for n in names):
            found.append(b)
    return found[0] if len(found) == 1 else None


@transaction.atomic
def apply_request(req, beneficiary, customer=None, staff=None, note=''):
    """申し込みを予約にする（職員の確認ずみ）。作った予約を返す"""
    if not req.is_pending:
        raise ReservationError('この申し込みはすでに処理ずみです。')
    facility = req.facility
    label = note or f'申し込み：{req.name} 様'
    res, _ = create_reservation(facility, beneficiary, req.date, source=Reservation.SOURCE_WEB,
                                customer=customer, note=label)
    req.status = BookingRequest.STATUS_DONE
    req.reservation = res
    req.customer = customer
    req.handled_at, req.handled_by = timezone.now(), staff
    req.result_note = f'{jp_date(req.date)} {beneficiary.full_name} {res.get_status_display()}'[:200]
    req.save(update_fields=['status', 'reservation', 'customer', 'handled_at', 'handled_by', 'result_note'])
    return res


@transaction.atomic
def book_request_now(req, setting=None, base=''):
    """
    自動方式：届いた申し込みを、その場で予約にする（来た順）。

    お子さまの名前が台帳の利用者と決まればその方の予約、決まらなければ
    「台帳に未登録」のまま席を押さえる（職員があとから結びつける）。
    戻り値は予約。満席でキャンセル待ちも受けない設定なら「お断り」で返る。
    """
    facility = req.facility
    setting = setting or get_setting(facility)
    beneficiary = request_matches(facility, req)
    customer = Customer.objects.filter(facility=facility, name=req.name).first()
    res, _ = create_reservation(
        facility, beneficiary, req.date, source=Reservation.SOURCE_WEB, customer=customer,
        note=f'お申し込み：{req.name} 様'[:200],
        guest_name='' if beneficiary is not None else (req.child_name or req.name),
    )
    req.status = BookingRequest.STATUS_DONE
    req.reservation = res
    req.customer = customer
    req.handled_at = timezone.now()
    req.result_note = f'{jp_date(req.date)} {res.display_name} {res.get_status_display()}（自動）'[:200]
    req.save(update_fields=['status', 'reservation', 'customer', 'handled_at', 'result_note'])
    return res


def receive_request(facility, day, name, kana='', phone='', child_name='', note='', setting=None):
    """
    申し込みを受け取る。戻り値は (申し込み, できた予約 or None)。

    自動方式（既定）では、来た順にその場で予約にする（満席ならキャンセル待ちかお断り）。
    承認方式では予約にせず、職員が確かめてから反映する。
    """
    setting = setting or get_setting(facility)
    req = BookingRequest.objects.create(
        facility=facility, date=day, name=name[:100], kana=kana[:100], phone=phone[:20],
        child_name=child_name[:100], note=note[:200],
    )
    if not setting.is_auto:
        return req, None      # 承認方式：職員が確かめてから予約にする
    try:
        return req, book_request_now(req, setting)
    except ReservationError as e:
        req.result_note = str(e)[:200]
        req.save(update_fields=['result_note'])
        raise


# ---------------------------------------------------------------- 「予約」と送られたときの案内
def page_reply(facility, text, customer=None, setting=None, base='', today=None):
    """
    「予約」「空いてますか」などの問い合わせに、顧客向け予定表のアドレスを返す。

    戻り値は (返事の文, 受信箱に積むか)。
    - 顧客台帳にいる方 …… その方専用のページ（確認・申し込み・取り消しができる）
    - はじめての方 …… 空き状況のページ（公開しているときだけ）
    日にちが書かれている文は、職員が見られるように受信箱にも積む。
    アドレスを出せないときは ('', True) を返し、これまでどおりの扱いにする。
    """
    setting = setting or get_setting(facility)
    parsed = parse_message(text, today)
    if parsed['intent'] not in ('reserve', 'cancel', 'check'):
        return '', True

    if customer is not None:
        url = customer_page_url(customer, base)
        lead = 'ご予約の確認・お申し込み・取り消しは、こちらのページからできます。'
    else:
        url = calendar_page_url(setting, base)
        lead = '空き状況の確認とお申し込みは、こちらのページからできます。'
        if parsed['intent'] == 'cancel':
            return '', True   # 取り消しは、はじめての方のページではできない
    if not url:
        return '', True

    body = f'{lead}\n{url}\n{setting.sign_text}'
    return body, bool(parsed['dates'])


# ---------------------------------------------------------------- 顧客台帳と LINE をつなぐ
def link_customer_line(customer, line_user_id):
    """
    顧客台帳の人に LINE のユーザー ID を付ける。ほかの顧客がすでに使っていれば付けない。
    戻り値は (付けたか, 理由)。
    """
    if not line_user_id:
        return False, 'LINE のユーザー ID がありません。'
    other = Customer.objects.filter(facility=customer.facility, line_user_id=line_user_id).exclude(pk=customer.pk).first()
    if other is not None:
        return False, f'この LINE はすでに「{other.name}」さんにつながっています。'
    customer.line_user_id = line_user_id
    customer.save(update_fields=['line_user_id', 'updated_at'])
    return True, ''


def link_guardian_to_customer(guardian):
    """
    保護者が登録コードで LINE をつないだとき、予約の顧客台帳にも同じ LINE を付ける
    （月予約利用希望のお願い・「ご利用日が決まりました」などが LINE で届くように）。
    - 同じ LINE の顧客がいれば、そのお子さまを担当に足す（きょうだい）
    - お子さまの顧客で LINE の無い人がいれば、その人に付ける（保護者の名前が同じ人を優先）
    - 顧客がいなければ、保護者台帳から作る
    予約管理を使わない事業所では何もしない。戻り値は顧客（または None）。
    """
    from config.utils import reservation_enabled
    beneficiary = guardian.beneficiary
    facility = beneficiary.facility
    line_id = guardian.line_user_id
    if not line_id or not reservation_enabled(facility):
        return None
    same = Customer.objects.filter(facility=facility, line_user_id=line_id).first()
    if same is not None:
        same.children.add(beneficiary)
        return same
    candidates = list(Customer.objects.filter(facility=facility, children=beneficiary, line_user_id=''))
    target = next((c for c in candidates if c.name.replace(' ', '').replace('　', '')
                   == guardian.full_name.replace(' ', '')), None) or (candidates[0] if candidates else None)
    if target is None:
        target = Customer.objects.create(facility=facility, name=guardian.full_name[:100], phone=guardian.phone[:20],
                                         note='LINE の登録コードで作成')
        target.children.add(beneficiary)
    target.line_user_id = line_id
    target.save(update_fields=['line_user_id', 'updated_at'])
    return target

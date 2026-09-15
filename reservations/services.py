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

from .models import ClosedDate, Customer, LineInbox, Reservation, ReservationNotice, ReservationSetting

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
    return 0 if is_closed(facility, day, setting, closed) else setting.capacity


def day_state(facility, day, setting=None, closed=None):
    """その日の枠・予約数・キャンセル待ち数・残り"""
    setting = setting or get_setting(facility)
    cap = capacity_of(facility, day, setting, closed)
    rows = Reservation.objects.filter(facility=facility, date=day, status__in=Reservation.ACTIVE_STATUSES)
    confirmed = sum(1 for r in rows if r.status == Reservation.STATUS_CONFIRMED)
    waiting = sum(1 for r in rows if r.status == Reservation.STATUS_WAITLIST)
    return {
        'date': day, 'capacity': cap, 'confirmed': confirmed, 'waiting': waiting,
        'remaining': max(cap - confirmed, 0), 'closed': cap == 0,
        'full': cap > 0 and confirmed >= cap,
    }


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
        cap = 0 if is_closed(facility, day, setting, closed) else setting.capacity
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
    child = reservation.beneficiary.full_name if reservation else ''
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


@transaction.atomic
def create_reservation(facility, beneficiary, day, source=Reservation.SOURCE_STAFF, customer=None, note=''):
    """
    予約を1件作る。戻り値は (予約, 通知).
    空きがあれば確定、満枠ならキャンセル待ち（設定で断る）、休業日と重複は断る。
    """
    setting = get_setting(facility)
    if beneficiary.facility_id != facility.pk:
        raise ReservationError('この事業所の利用者ではありません。')
    # 同じ日に同時の申し込みが来ても枠を超えないよう、その日の行を先に押さえる
    list(Reservation.objects.select_for_update().filter(facility=facility, date=day))
    if Reservation.objects.filter(beneficiary=beneficiary, date=day,
                                  status__in=Reservation.ACTIVE_STATUSES).exists():
        raise ReservationError(f'{jp_date(day)} の {beneficiary.full_name} さんの予約はすでにあります。')
    if is_closed(facility, day, setting):
        raise ReservationError(f'{jp_date(day)} は休業日のため予約を受け付けられません。')

    customer = customer or customer_for(facility, beneficiary)
    st = day_state(facility, day, setting)
    if not st['full']:
        status, kind, what = Reservation.STATUS_CONFIRMED, ReservationNotice.KIND_ACCEPTED, '予約'
    elif setting.allow_waitlist:
        status, kind, what = Reservation.STATUS_WAITLIST, ReservationNotice.KIND_WAITLISTED, 'キャンセル待ち'
    else:
        status, kind, what = Reservation.STATUS_DECLINED, ReservationNotice.KIND_DECLINED, '満席で受付不可'

    try:
        res = Reservation.objects.create(facility=facility, beneficiary=beneficiary, customer=customer,
                                         date=day, status=status, source=source, note=note[:200])
    except IntegrityError:
        raise ReservationError(f'{jp_date(day)} の {beneficiary.full_name} さんの予約はすでにあります。')
    notice = queue_notice(facility, kind, setting, customer=customer, reservation=res)
    queue_group_notice(facility, setting, day, beneficiary.full_name, what)
    return res, notice


@transaction.atomic
def cancel_reservation(res, notify=True):
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
        queue_group_notice(facility, setting, res.date, res.beneficiary.full_name, '取消')

    promoted = promote_waitlist(facility, res.date, setting) if was_confirmed else []
    if was_full and was_confirmed and not promoted:
        offer_vacancy(facility, res.date, setting, exclude_customer=res.customer)
    return promoted


def promote_waitlist(facility, day, setting=None):
    """空いたぶんだけ、キャンセル待ちを申し込み順に確定する"""
    setting = setting or get_setting(facility)
    promoted = []
    while True:
        st = day_state(facility, day, setting)
        if st['remaining'] <= 0:
            break
        nxt = (Reservation.objects.filter(facility=facility, date=day, status=Reservation.STATUS_WAITLIST)
               .order_by('created_at', 'pk').first())
        if nxt is None:
            break
        nxt.status = Reservation.STATUS_CONFIRMED
        nxt.save(update_fields=['status', 'updated_at'])
        queue_notice(facility, ReservationNotice.KIND_PROMOTED, setting, customer=nxt.customer, reservation=nxt)
        queue_group_notice(facility, setting, day, nxt.beneficiary.full_name, '繰り上げ確定')
        promoted.append(nxt)
    return promoted


def offer_vacancy(facility, day, setting=None, exclude_customer=None):
    """
    満枠だった日に空きが出たとき、LINE連携ずみで その日に予約のない顧客へ「空きが出ました」を積む。
    取り消した本人は除く。同じ日・同じ顧客に送信待ちが残っていれば重ねない。
    """
    setting = setting or get_setting(facility)
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
        made.append(queue_notice(facility, ReservationNotice.KIND_VACANCY, setting, customer=c, day=day, body=body))
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
RESERVE_WORDS = ['予約', '利用したい', 'お願いします', '行きます']
CANCEL_WORDS = ['キャンセル', '取消', '取り消し', '休みます', '欠席']
REGISTER_MARK = '【登録】'
_DATE_PATTERNS = [
    re.compile(r'(?P<m>\d{1,2})\s*[/月]\s*(?P<d>\d{1,2})'),
    re.compile(r'(?P<y>\d{4})\s*[-/]\s*(?P<m>\d{1,2})\s*[-/]\s*(?P<d>\d{1,2})'),
]


def parse_message(text, today=None):
    """
    届いた文から「何をしたいか」と「いつ」を読み取る。
    決められないところは空のままにする（決めつけない）。
    """
    today = today or datetime.date.today()
    raw = (text or '').strip()
    result = {'intent': 'unknown', 'date': None, 'name': '', 'fields': {}, 'text': raw}
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

    if '明日' in raw:
        result['date'] = today + datetime.timedelta(days=1)
    elif '今日' in raw or '本日' in raw:
        result['date'] = today
    else:
        for pattern in _DATE_PATTERNS:
            m = pattern.search(raw)
            if not m:
                continue
            groups = m.groupdict()
            year = int(groups['y']) if groups.get('y') else today.year
            try:
                found = datetime.date(year, int(groups['m']), int(groups['d']))
            except ValueError:
                break
            if not groups.get('y') and found < today - datetime.timedelta(days=180):
                found = datetime.date(year + 1, int(groups['m']), int(groups['d']))
            result['date'] = found
            break
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

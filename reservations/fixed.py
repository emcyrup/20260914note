"""
利用予定曜日から予定を作る（2026-11-09 オウル）。はびりすの「予定」（schedules）と同じ使い方を、予約（Reservation）の上で行う。

- bulk_create(facility, year, month)：在籍中の利用者の利用予定曜日に合わせて、その月の「予約（確定）」をまとめて作る。
  休業日・休業曜日・すでに有効な予約のある日は飛ばす（上書きしない）。枠の上限は見ない（契約の曜日なので）
- month_counts(facility, first, last)：日ごとの 予定・来所・欠席・キャンセル の人数（カレンダーの印）
- day_rows(facility, day)：出欠入力の行（在籍中の利用者全員。予約があればその状態と送迎）
- save_day(facility, day, post)：出欠入力の保存（状態・迎え・送り・備考）
"""
import calendar
import datetime

from django.db import transaction
from django.utils import timezone

from beneficiaries.models import Beneficiary

from . import services
from .models import Reservation

# 出欠入力の状態の値：'' は予定なし（予約を取り消す）
STATE_SCHEDULED = 'scheduled'
STATE_CHOICES = [
    (STATE_SCHEDULED, '予定'),
    (Reservation.ATT_ATTENDED, '来所'),
    (Reservation.ATT_ABSENT, '欠席'),
    (Reservation.ATT_CANCELLED, 'キャンセル'),
]
STATE_VALUES = {v for v, _ in STATE_CHOICES}


def state_of(reservation):
    """予約 → 出欠入力の状態（有効な予約が無ければ ''）"""
    if reservation is None or not reservation.is_active:
        return ''
    return reservation.attendance or STATE_SCHEDULED


def bulk_create(facility, year, month, setting=None):
    """
    その月の予定を利用予定曜日からまとめて作る。戻り値 (作った数, 飛ばした数（休業・既存）, 曜日の無い利用者の数)。
    利用予定曜日が 1 つも無い利用者は作らない（はびりすは全日作るが、オウルは曜日が契約なので作らない）
    """
    setting = setting or services.get_setting(facility)
    last = calendar.monthrange(year, month)[1]
    first_day, last_day = datetime.date(year, month, 1), datetime.date(year, month, last)
    closed = services.closed_dates(facility, first_day, last_day)
    bens = list(Beneficiary.objects.filter(facility=facility, status=Beneficiary.STATUS_ACTIVE).order_by('pk'))
    existing = set(Reservation.objects.filter(facility=facility, date__gte=first_day, date__lte=last_day,
                                              status__in=Reservation.ACTIVE_STATUSES, beneficiary__isnull=False)
                   .values_list('beneficiary_id', 'date'))
    start_time = setting.bulk_start_time
    made = skipped = no_days = 0
    rows = []
    with transaction.atomic():
        for b in bens:
            days = b.scheduled_weekday_numbers
            if not days:
                no_days += 1
                continue
            for n in range(1, last + 1):
                d = datetime.date(year, month, n)
                if d.weekday() not in days:
                    continue
                if (b.pk, d) in existing or services.is_closed(facility, d, setting, closed):
                    skipped += 1
                    continue
                rows.append(Reservation(facility=facility, beneficiary=b, date=d, start_time=start_time,
                                        status=Reservation.STATUS_CONFIRMED, source=Reservation.SOURCE_STAFF))
        Reservation.objects.bulk_create(rows)
        made = len(rows)
    return made, skipped, no_days


def month_counts(facility, first_day, last_day):
    """日ごとの人数：{date: {'scheduled', 'attended', 'absent', 'cancelled', 'waiting'}}（取消・お断りの予約は数えない）"""
    counts = {}
    for date, status, att in Reservation.objects.filter(facility=facility, date__gte=first_day, date__lte=last_day,
                                                        status__in=Reservation.ACTIVE_STATUSES).values_list('date', 'status', 'attendance'):
        c = counts.setdefault(date, {'scheduled': 0, 'attended': 0, 'absent': 0, 'cancelled': 0, 'waiting': 0})
        if status == Reservation.STATUS_WAITLIST:
            c['waiting'] += 1
        else:
            c[att or STATE_SCHEDULED] += 1
    return counts


def month_has_reservations(facility, year, month):
    return Reservation.objects.filter(facility=facility, date__year=year, date__month=month,
                                      status__in=Reservation.ACTIVE_STATUSES).exists()


def _transport_rows(facility, day):
    """送迎を使う事業所：その日の配車表の行を (利用者 pk, 方向) → 行 で返す（迎え・送りの印の元）"""
    if not getattr(facility, 'use_transport', False):
        return None
    from transport.views import build_board
    board = build_board(facility, day)
    out = {}
    for direction in ('pickup', 'dropoff'):
        for x in board[direction]:
            out[(x['beneficiary'].pk, direction)] = x
    return out


def day_rows(facility, day):
    """出欠入力の行：在籍中の利用者全員（予約があれば状態・時刻・備考、送迎を使うなら迎え・送りの印）"""
    bens = list(Beneficiary.objects.filter(facility=facility, status=Beneficiary.STATUS_ACTIVE)
                .order_by('last_name_kana', 'first_name_kana', 'pk'))
    res_map = {}
    for r in Reservation.objects.filter(facility=facility, date=day, beneficiary__isnull=False).order_by('-updated_at'):
        if r.is_active:
            res_map[r.beneficiary_id] = r
    tr = _transport_rows(facility, day)
    rows = []
    for b in bens:
        r = res_map.get(b.pk)
        pickup = dropoff = None
        if tr is not None and r is not None:
            px, dx = tr.get((b.pk, 'pickup')), tr.get((b.pk, 'dropoff'))
            pickup = bool(px and not px['skip'])
            dropoff = bool(dx and not dx['skip'])
        rows.append({'beneficiary': b, 'reservation': r, 'state': state_of(r), 'note': r.note if r else '',
                     'time': r.time_label if r else '', 'has_pickup': pickup, 'has_dropoff': dropoff,
                     'weekday_match': day.weekday() in b.scheduled_weekday_numbers})
    return rows


def _set_transport(reservation, direction, wanted):
    """その日の迎え／送りの有無を配車表に合わせる：設定にあれば skip の切り替え、無ければ「この日だけ」の追加・削除"""
    from transport.models import TransportAssignment, TransportProfile
    try:
        profile = reservation.beneficiary.transport
    except TransportProfile.DoesNotExist:
        profile = None
    in_profile = bool(profile and profile.has(direction))
    a = TransportAssignment.objects.filter(reservation=reservation, direction=direction).first()
    if wanted:
        if in_profile:
            if a and a.skip:
                a.skip = False
                a.save(update_fields=['skip', 'updated_at'])
        else:
            if a is None:
                TransportAssignment.objects.create(reservation=reservation, direction=direction, added=True)
            elif not a.added or a.skip:
                a.added, a.skip = True, False
                a.save(update_fields=['added', 'skip', 'updated_at'])
    else:
        if in_profile:
            if a is None:
                TransportAssignment.objects.create(reservation=reservation, direction=direction, skip=True)
            elif not a.skip:
                a.skip = True
                a.save(update_fields=['skip', 'updated_at'])
        elif a is not None and a.added:
            a.delete()


def save_day(facility, day, post, setting=None):
    """
    出欠入力の保存。利用者ごとに b_<pk>_state（''／scheduled／attended／absent／cancelled）・b_<pk>_pickup・b_<pk>_dropoff・b_<pk>_note。
    '' は予約の取消（通知は積まない）。予約が無いのに状態があれば予約を作る（枠の上限は見ない）。戻り値は変えた人数
    """
    setting = setting or services.get_setting(facility)
    has_transport = getattr(facility, 'use_transport', False)
    bens = Beneficiary.objects.filter(facility=facility, status=Beneficiary.STATUS_ACTIVE)
    active = {}
    for r in Reservation.objects.filter(facility=facility, date=day, beneficiary__in=bens, status__in=Reservation.ACTIVE_STATUSES):
        active[r.beneficiary_id] = r
    changed = 0
    now = timezone.now()
    with transaction.atomic():
        for b in bens:
            key = f'b_{b.pk}_'
            if f'{key}state_present' not in post and f'{key}state' not in post:
                continue                     # 画面に無かった人は触らない
            state = (post.get(f'{key}state') or '').strip()
            r = active.get(b.pk)
            if state == '':
                if r is not None:
                    r.status, r.cancelled_at = Reservation.STATUS_CANCELLED, now
                    r.save(update_fields=['status', 'cancelled_at', 'updated_at'])
                    changed += 1
                continue
            if state not in STATE_VALUES:
                continue
            note = (post.get(f'{key}note') or '').strip()[:200]
            if r is None:
                r = Reservation.objects.create(facility=facility, beneficiary=b, date=day, start_time=setting.bulk_start_time,
                                               status=Reservation.STATUS_CONFIRMED, source=Reservation.SOURCE_STAFF, note=note)
                changed += 1
            fields = []
            att = '' if state == STATE_SCHEDULED else state
            if r.attendance != att:
                r.attendance = att
                fields.append('attendance')
            if r.status == Reservation.STATUS_WAITLIST:       # 出欠を入れた人はキャンセル待ちでなく予約に
                r.status = Reservation.STATUS_CONFIRMED
                fields.append('status')
            if r.note != note:
                r.note = note
                fields.append('note')
            if fields:
                r.save(update_fields=fields + ['updated_at'])
                changed += 1
            if has_transport and r.status == Reservation.STATUS_CONFIRMED:
                _set_transport(r, 'pickup', f'{key}pickup' in post)
                _set_transport(r, 'dropoff', f'{key}dropoff' in post)
    return changed

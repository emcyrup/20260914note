"""送迎・配車の画面：日ごとの配車表（迎え／送り）、車両・運転手の登録、利用者ごとの送迎の設定"""
import datetime

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from accounts.models import StaffAccount
from config.concurrency import check_conflict
from config.pdf import pdf_or_html
from config.utils import to_int

from . import routing
from .models import (DIRECTION_DROPOFF, DIRECTION_LABELS, DIRECTION_PICKUP, DIRECTIONS, Driver, TransportAssignment,
                     TransportProfile, Vehicle)

WEEK_JP = ['月', '火', '水', '木', '金', '土', '日']


class TransportEnabledMixin(LoginRequiredMixin):
    """施設設定で送迎・配車を使わない事業所はホームへ戻す"""

    def dispatch(self, request, *args, **kwargs):
        facility = getattr(request.user, 'facility', None)
        if request.user.is_authenticated and not (facility is not None and facility.use_transport):
            messages.info(request, 'この事業所では送迎・配車を使わない設定になっています（施設設定の「使う機能」で変えられます）。')
            return redirect('facilities:dashboard')
        return super().dispatch(request, *args, **kwargs)


def parse_day(value, default=None):
    try:
        return datetime.date.fromisoformat(value or '')
    except ValueError:
        return default or datetime.date.today()


def parse_time(value):
    """'14:05' → time。空や読めない形は None"""
    value = (value or '').strip()
    if not value:
        return None
    try:
        h, m = value.split(':')[:2]
        return datetime.time(int(h), int(m))
    except (ValueError, TypeError):
        return None


def row_key(reservation_pk, direction):
    return f'r{reservation_pk}_{direction}'


def day_reservations(facility, day):
    """その日の確定した予約（台帳にいる利用者だけ）。送迎の設定と配車を一緒に読む"""
    from reservations.models import Reservation
    return list(Reservation.objects.filter(facility=facility, date=day, status=Reservation.STATUS_CONFIRMED,
                                           beneficiary__isnull=False)
                .select_related('beneficiary', 'beneficiary__school', 'beneficiary__transport', 'beneficiary__transport__default_vehicle',
                                'beneficiary__transport__default_vehicle__default_driver')
                .prefetch_related(Prefetch('transports', queryset=TransportAssignment.objects.select_related(
                    'vehicle', 'vehicle__default_driver', 'driver')))
                .order_by('start_time', 'beneficiary__last_name_kana', 'beneficiary__first_name_kana', 'pk'))


def _profile_of(reservation):
    try:
        return reservation.beneficiary.transport
    except TransportProfile.DoesNotExist:
        return None


def build_board(facility, day):
    """
    配車表の中身。{'pickup': [row...], 'dropoff': [row...], 'others': {dir: [reservation...]}, 'counts': {dir: [{'vehicle','count','over'}]}}
    row：key・reservation・beneficiary・profile・assignment・time・place・vehicle_id・driver_id・note・skip・added
    """
    reservations = day_reservations(facility, day)
    drivers = {d.pk: d for d in Driver.objects.filter(facility=facility).prefetch_related('vehicles')}
    board = {'pickup': [], 'dropoff': [], 'others': {DIRECTION_PICKUP: [], DIRECTION_DROPOFF: []}, 'counts': {}, 'problems': 0}
    for r in reservations:
        profile = _profile_of(r)
        asg = {a.direction: a for a in r.transports.all()}
        for direction, _label in DIRECTIONS:
            a = asg.get(direction)
            if not ((profile and profile.has(direction)) or (a and a.added)):
                board['others'][direction].append(r)
                continue
            vehicle = a.vehicle if a and a.vehicle_id else (profile.default_vehicle if profile and profile.default_vehicle_id else None)
            driver_id = a.driver_id if a and a.driver_id else (vehicle.default_driver_id if vehicle else None)
            driver = drivers.get(driver_id)
            # 迎えの時刻が設定に無ければ、学校のその曜日の下校時刻（学校マスタ）を既定にする（2026-11-09）
            school = r.beneficiary.school if r.beneficiary.school_id else None
            dismissal = school.dismissal_for(day) if (school and direction == DIRECTION_PICKUP) else None
            board[direction].append({
                'key': row_key(r.pk, direction), 'reservation': r, 'beneficiary': r.beneficiary, 'profile': profile, 'assignment': a,
                'time': (a.time if a and a.time else ((profile.time(direction) if profile else None) or dismissal)),
                'dismissal': dismissal,
                'route_pair': route_pair(facility, r.beneficiary, direction),
                'place': (a.place if a and a.place else (profile.place(direction) if profile else '')),
                'default_place': default_place(r.beneficiary, direction),
                'vehicle_id': vehicle.pk if vehicle else None, 'vehicle': vehicle,
                'driver_id': driver_id, 'driver': driver,
                'driver_ng': bool(vehicle and driver and not driver.can_drive(vehicle)),
                'note': a.note if a else '', 'profile_note': profile.note if profile else '',
                'skip': bool(a and a.skip), 'added': bool(a and a.added),
            })
    # 所要時間（控えにあるものだけ。外部への問い合わせは「所要時間を計算」ボタンで）
    board['routes_missing'] = 0
    for direction, _label in DIRECTIONS:
        for x in board[direction]:
            pair = x['route_pair']
            x['route'] = routing.cached(*pair) if pair else None
            if pair and not x['route'] and not x['skip']:
                board['routes_missing'] += 1
    for direction, _label in DIRECTIONS:
        rows = board[direction]
        rows.sort(key=lambda x: (x['skip'], x['time'] is None, x['time'] or datetime.time.min,
                                 x['beneficiary'].last_name_kana, x['beneficiary'].first_name_kana))
        by_vehicle = {}
        for x in rows:
            if not x['skip'] and x['vehicle']:
                c = by_vehicle.setdefault(x['vehicle'].pk, {'vehicle': x['vehicle'], 'count': 0, 'driver_ng': False, 'minutes': 0})
                c['count'] += 1
                c['driver_ng'] = c['driver_ng'] or x['driver_ng']
                c['minutes'] += x['route'].minutes if x['route'] else 0
        counts = sorted(by_vehicle.values(), key=lambda c: (c['vehicle'].order, c['vehicle'].pk))
        for c in counts:
            c['over'] = bool(c['vehicle'].capacity and c['count'] > c['vehicle'].capacity)
            c['ng'] = c['over'] or c['driver_ng']
            board['problems'] += int(c['over']) + int(c['driver_ng'])
        board['counts'][direction] = counts
        board[f'{direction}_active'] = sum(1 for x in rows if not x['skip'])
        board[f'{direction}_unassigned'] = sum(1 for x in rows if not x['skip'] and not x['vehicle'])
    board = mark_ride_ng(facility, board)
    board['problems'] += sum(board['ride_ng'].values())
    return board


def route_pair(facility, beneficiary, direction):
    """
    所要時間を求める (出発, 到着) の住所（2026-11-09）。迎え：学校の住所 → 施設の住所、送り：施設の住所 → 自宅の住所。
    どちらかが無ければ None（学校の住所は学校マスタ、自宅は利用者情報、施設は施設設定の住所）
    """
    base = routing.normalize_address(facility.address)
    if not base:
        return None
    if direction == DIRECTION_PICKUP:
        school = beneficiary.school if beneficiary.school_id else None
        origin = routing.normalize_address(school.address) if school else ''
        return (origin, base) if origin else None
    home = routing.normalize_address(beneficiary.address)
    return (base, home) if home else None


def compute_routes(facility, day, limit=40):
    """その日の配車表の、まだ控えの無い組の所要時間を外部に問い合わせて控える。(求めた数, 求められなかった数, 残り)"""
    board = build_board(facility, day)
    pairs = [x['route_pair'] for d, _l in DIRECTIONS for x in board[d] if x['route_pair'] and not x['route'] and not x['skip']]
    done, failed = routing.estimate_many(pairs, limit=limit)
    left = max(0, len({p for p in pairs}) - done - failed)
    return done, failed, left


def default_place(beneficiary, direction):
    """場所の欄が空のときに出す既定：迎えは学校（名前と住所）、送りは自宅の住所（利用者情報から）"""
    if direction == DIRECTION_PICKUP:
        school = beneficiary.school if beneficiary.school_id else None
        if school:
            return school.name + (f'（{school.address}）' if school.address else '')
        return beneficiary.school_name or ''
    return beneficiary.address or ''


RIDE_WINDOW = datetime.timedelta(minutes=30)    # 同じ車でも、これより時刻が離れていれば別の便とみなす


def _near(a, b):
    """同じ便とみなす時刻か（どちらかに時刻が無ければ同じとみなす）"""
    if a is None or b is None:
        return True
    da = datetime.datetime.combine(datetime.date.min, a)
    db = datetime.datetime.combine(datetime.date.min, b)
    return abs(da - db) <= RIDE_WINDOW


def mark_ride_ng(facility, board):
    """
    同乗NG：同じ方向（迎え／送り）・同じ車両・近い時刻（30 分以内）に「同じ車に乗せない利用者」がいれば、
    その行に ride_ng（相手の名前のリスト）を付け、board['ride_ng'][方向] に組の数を入れる
    """
    from beneficiaries.models import Beneficiary
    pairs = {}
    for a, b in Beneficiary.no_ride_with.through.objects.filter(from_beneficiary__facility=facility).values_list(
            'from_beneficiary_id', 'to_beneficiary_id'):
        pairs.setdefault(a, set()).add(b)
    board['ride_ng'] = {}
    for direction, _label in DIRECTIONS:
        rows = [x for x in board[direction] if not x['skip']]
        n = 0
        for x in board[direction]:
            x['ride_ng'] = []
        for i, x in enumerate(rows):
            for y in rows[i + 1:]:
                if (x['vehicle_id'] and x['vehicle_id'] == y['vehicle_id'] and y['beneficiary'].pk in pairs.get(x['beneficiary'].pk, ())
                        and _near(x['time'], y['time'])):
                    x['ride_ng'].append(y['beneficiary'].full_name)
                    y['ride_ng'].append(x['beneficiary'].full_name)
                    n += 1
        board['ride_ng'][direction] = n
    return board


def auto_assign(facility, day):
    """
    車両が決まっていない行に、自動で車両と運転手を割り当てる（2026-11-09 オウル）。
    順番：いつもの車両 → 空きのある車両（定員・同じ便の同乗NG を避ける。車両の並び順）。運転手は車両のいつもの運転手が
    その車を運転できればその人、できなければ運転できる人から。決められなかった行はそのまま（画面で手で選ぶ）。
    戻り値 (割り当てた数, 決められなかった数)
    """
    from beneficiaries.models import Beneficiary
    board = build_board(facility, day)
    vehicles = list(Vehicle.objects.filter(facility=facility, is_active=True).select_related('default_driver').order_by('order', 'pk'))
    drivers = list(Driver.objects.filter(facility=facility, is_active=True).prefetch_related('vehicles').order_by('order', 'pk'))
    pairs = {}
    for a, b in Beneficiary.no_ride_with.through.objects.filter(from_beneficiary__facility=facility).values_list(
            'from_beneficiary_id', 'to_beneficiary_id'):
        pairs.setdefault(a, set()).add(b)

    def pick_driver(vehicle):
        if vehicle.default_driver and vehicle.default_driver.is_active and vehicle.default_driver.can_drive(vehicle):
            return vehicle.default_driver
        return next((d for d in drivers if d.can_drive(vehicle)), None)

    assigned = left = 0
    with transaction.atomic():
        for direction, _label in DIRECTIONS:
            rows = [x for x in board[direction] if not x['skip']]
            # 車両ごとの乗っている人（時刻つき）。同じ便（30 分以内）の人数と同乗NG を見る
            riding = {}
            for x in rows:
                if x['vehicle']:
                    riding.setdefault(x['vehicle'].pk, []).append(x)
            for x in rows:
                if x['vehicle']:
                    continue
                chosen = None
                candidates = []
                if x['profile'] and x['profile'].default_vehicle_id:
                    candidates.append(next((v for v in vehicles if v.pk == x['profile'].default_vehicle_id), None))
                candidates += [v for v in vehicles if v not in candidates]
                for v in candidates:
                    if v is None:
                        continue
                    same_trip = [y for y in riding.get(v.pk, []) if _near(x['time'], y['time'])]
                    if v.capacity and len(same_trip) >= v.capacity:
                        continue
                    if any(y['beneficiary'].pk in pairs.get(x['beneficiary'].pk, ()) for y in same_trip):
                        continue
                    chosen = v
                    break
                if chosen is None:
                    left += 1
                    continue
                a = x['assignment'] or TransportAssignment(reservation=x['reservation'], direction=direction)
                a.vehicle = chosen
                a.driver = pick_driver(chosen)
                if a.time is None and x['time']:
                    a.time = x['time']
                a.save()
                x['vehicle'] = chosen
                x['vehicle_id'] = chosen.pk
                riding.setdefault(chosen.pk, []).append(x)
                assigned += 1
    return assigned, left


def save_board(facility, day, post):
    """配車表の一括保存。画面に出ていた行（present_<key>）だけを読み、予約 × 迎え／送り ごとに配車を作る・直す。保存した件数"""
    vehicles = {v.pk: v for v in Vehicle.objects.filter(facility=facility)}
    drivers = {d.pk: d for d in Driver.objects.filter(facility=facility)}
    saved = 0
    with transaction.atomic():
        for r in day_reservations(facility, day):
            existing = {a.direction: a for a in r.transports.all()}
            for direction, _label in DIRECTIONS:
                key = row_key(r.pk, direction)
                if f'present_{key}' not in post:
                    continue
                a = existing.get(direction) or TransportAssignment(reservation=r, direction=direction)
                a.vehicle = vehicles.get(to_int(post.get(f'vehicle_{key}'), 0))
                a.driver = drivers.get(to_int(post.get(f'driver_{key}'), 0))
                a.time = parse_time(post.get(f'time_{key}'))
                a.place = (post.get(f'place_{key}') or '').strip()[:200]
                a.note = (post.get(f'note_{key}') or '').strip()[:200]
                a.skip = f'skip_{key}' in post
                if a.added and a.skip:          # この日だけ足した行で「なし」にしたら、行そのものを消す
                    if a.pk:
                        a.delete()
                    continue
                a.save()
                saved += 1
    return saved


def day_context(facility, day):
    return {
        'facility': facility, 'day': day, 'weekday': WEEK_JP[day.weekday()], 'today': datetime.date.today(),
        'prev_day': day - datetime.timedelta(days=1), 'next_day': day + datetime.timedelta(days=1),
        'board': build_board(facility, day), 'directions': DIRECTIONS,
        'vehicles': list(Vehicle.objects.filter(facility=facility, is_active=True)),
        'drivers': list(Driver.objects.filter(facility=facility, is_active=True).prefetch_related('vehicles')),
        'route_source': 'Google マップ' if getattr(settings, 'GOOGLE_MAPS_API_KEY', '') else '国土地理院の住所検索と道路の経路（OSRM）',
        'facility_has_address': bool(routing.normalize_address(facility.address)),
    }


class DayView(TransportEnabledMixin, View):
    """その日の配車表（迎え・送りの一覧）。?d=YYYY-MM-DD。POST action=save（一括保存）／add（この日だけ送迎を足す）／remove"""
    template_name = 'transport/day.html'

    def get(self, request):
        day = parse_day(request.GET.get('d'))
        return render(request, self.template_name, day_context(request.user.facility, day))

    def post(self, request):
        facility = request.user.facility
        day = parse_day(request.POST.get('d') or request.GET.get('d'))
        back = redirect(reverse('transport:day') + f'?d={day.isoformat()}')
        action = request.POST.get('action')
        if action == 'save':
            n = save_board(facility, day, request.POST)
            messages.success(request, f'{day:%-m月%-d日}の配車を保存しました（{n} 件）。')
            ng = build_board(facility, day)['ride_ng']
            if any(ng.values()):
                messages.warning(request, '同じ車に乗せない組み合わせが同じ車・近い時刻に入っています（' +
                                 '・'.join(f'{DIRECTION_LABELS[d]} {c} 組' for d, c in ng.items() if c) + '）。赤い行を確かめてください。')
            return back
        if action == 'routes':
            done, failed, left = compute_routes(facility, day)
            if done:
                messages.success(request, f'所要時間を {done} 件求めました。' + (f'{failed} 件は住所が見つからないか、経路が引けませんでした（住所を番地まで入れてください）。' if failed else '')
                                 + (f'残り {left} 件は、もう一度押すと続きを求めます。' if left else ''))
            elif failed:
                messages.warning(request, f'所要時間を求められませんでした（{failed} 件）。住所が見つからないか、経路のサービスに届いていません。住所を番地まで入れて、しばらくしてからもう一度試してください。')
            else:
                messages.info(request, '所要時間を求める行はありません（住所が入っている行は計算済みです）。')
            return back
        if action == 'auto':
            n, left = auto_assign(facility, day)
            if n:
                messages.success(request, f'{day:%-m月%-d日}の配車：車両が空いていた {n} 件に車両と運転手を割り当てました。' + (f'{left} 件は空き・同乗の条件で決められなかったので手で選んでください。' if left else ''))
            else:
                messages.info(request, '車両が空いている行はありません。' + (f'（{left} 件は空き・同乗の条件で決められませんでした）' if left else ''))
            return back
        if action in ('add', 'remove'):
            from reservations.models import Reservation
            r = get_object_or_404(Reservation, pk=to_int(request.POST.get('reservation'), -1), facility=facility, date=day)
            direction = request.POST.get('direction')
            if direction not in DIRECTION_LABELS:
                return back
            if action == 'add':
                a, _created = TransportAssignment.objects.get_or_create(reservation=r, direction=direction)
                profile = _profile_of(r)
                if not (profile and profile.has(direction)):
                    a.added = True
                a.skip = False
                a.save()
                messages.success(request, f'{r.display_name} さんの{DIRECTION_LABELS[direction]}をこの日の配車表に足しました。')
            else:
                TransportAssignment.objects.filter(reservation=r, direction=direction, added=True).delete()
                messages.success(request, f'{r.display_name} さんの{DIRECTION_LABELS[direction]}を配車表から外しました。')
            return back
        return back


class DayPdfView(TransportEnabledMixin, View):
    """配車表の PDF（A4 横 1 枚。迎え・送りの表。?fmt=html で画面）"""

    def get(self, request):
        day = parse_day(request.GET.get('d'))
        ctx = day_context(request.user.facility, day)
        return pdf_or_html(request, 'transport/pdf/day.html', ctx, f'配車表_{day:%Y%m%d}')


class VehiclesView(TransportEnabledMixin, View):
    """車両・運転手の登録"""
    template_name = 'transport/vehicles.html'

    def get(self, request):
        facility = request.user.facility
        return render(request, self.template_name, {
            'facility': facility,
            'vehicles': list(Vehicle.objects.filter(facility=facility).select_related('default_driver')),
            'drivers': list(Driver.objects.filter(facility=facility).select_related('staff').prefetch_related('vehicles')),
            'staff_accounts': list(StaffAccount.objects.filter(facility=facility, is_active=True).order_by('display_name', 'username')),
        })

    def post(self, request):
        facility = request.user.facility
        p = request.POST
        action = p.get('action', '')
        kind, _, what = action.partition('_')
        if kind == 'vehicle':
            if what == 'delete':
                v = get_object_or_404(Vehicle, pk=to_int(p.get('pk'), -1), facility=facility)
                name = v.name
                v.delete()
                messages.success(request, f'車両「{name}」を削除しました。')
                return redirect('transport:vehicles')
            v = get_object_or_404(Vehicle, pk=to_int(p.get('pk'), -1), facility=facility) if what == 'save' else Vehicle(facility=facility)
            name = (p.get('name') or '').strip()[:50]
            if not name:
                messages.error(request, '車両名を入れてください。')
                return redirect('transport:vehicles')
            v.name = name
            v.capacity = max(0, min(99, to_int(p.get('capacity'), 0)))
            v.plate = (p.get('plate') or '').strip()[:30]
            v.note = (p.get('note') or '').strip()[:200]
            v.order = max(0, min(999, to_int(p.get('order'), 0)))
            v.is_active = 'is_active' in p if what == 'save' else True
            v.default_driver = Driver.objects.filter(facility=facility, pk=to_int(p.get('default_driver'), 0)).first()
            v.save()
            messages.success(request, f'車両「{v.name}」を{"保存" if what == "save" else "登録"}しました。')
        elif kind == 'driver':
            if what == 'delete':
                d = get_object_or_404(Driver, pk=to_int(p.get('pk'), -1), facility=facility)
                name = d.name
                d.delete()
                messages.success(request, f'運転手「{name}」を削除しました。')
                return redirect('transport:vehicles')
            d = get_object_or_404(Driver, pk=to_int(p.get('pk'), -1), facility=facility) if what == 'save' else Driver(facility=facility)
            name = (p.get('name') or '').strip()[:50]
            if not name:
                messages.error(request, '運転手の名前を入れてください。')
                return redirect('transport:vehicles')
            d.name = name
            d.phone = (p.get('phone') or '').strip()[:20]
            d.note = (p.get('note') or '').strip()[:200]
            d.order = max(0, min(999, to_int(p.get('order'), 0)))
            d.is_active = 'is_active' in p if what == 'save' else True
            if 'staff' in p:        # ログインする職員（配車担当）。同じ事業所の人だけ
                d.staff = StaffAccount.objects.filter(facility=facility, pk=to_int(p.get('staff'), 0)).first()
            d.save()
            if 'vehicles_present' in p:     # 運転できる車両（空ならどれでも）
                ids = [to_int(x, 0) for x in p.getlist('vehicles')]
                d.vehicles.set(Vehicle.objects.filter(facility=facility, pk__in=ids))
            messages.success(request, f'運転手「{d.name}」を{"保存" if what == "save" else "登録"}しました。')
        return redirect('transport:vehicles')


def profile_context(beneficiary):
    """利用者情報の「送迎」欄に渡すもの（送迎・配車を使う事業所だけ呼ぶ）"""
    profile = TransportProfile.objects.filter(beneficiary=beneficiary).select_related('default_vehicle').first()
    from beneficiaries.models import Beneficiary
    return {'transport_profile': profile or TransportProfile(beneficiary=beneficiary),
            'transport_vehicles': list(Vehicle.objects.filter(facility=beneficiary.facility, is_active=True)),
            'ride_candidates': list(Beneficiary.objects.filter(facility=beneficiary.facility, status=Beneficiary.STATUS_ACTIVE)
                                    .exclude(pk=beneficiary.pk).order_by('last_name_kana', 'first_name_kana')),
            'ride_ng_ids': set(beneficiary.no_ride_with.values_list('pk', flat=True))}


class ProfileView(TransportEnabledMixin, View):
    """利用者ごとの送迎の設定を保存する（利用者情報の画面から）"""

    def post(self, request, pk):
        from beneficiaries.models import Beneficiary
        b = get_object_or_404(Beneficiary, pk=pk, facility=request.user.facility)
        back = redirect(reverse('beneficiaries:detail', args=[b.pk]) + '#transport')
        profile, _created = TransportProfile.objects.get_or_create(beneficiary=b)
        conflict = check_conflict(request, profile)
        if conflict:
            messages.error(request, conflict)
            return back
        p = request.POST
        profile.pickup = 'pickup' in p
        profile.pickup_place = (p.get('pickup_place') or '').strip()[:200]
        profile.pickup_time = parse_time(p.get('pickup_time'))
        profile.dropoff = 'dropoff' in p
        profile.dropoff_place = (p.get('dropoff_place') or '').strip()[:200]
        profile.dropoff_time = parse_time(p.get('dropoff_time'))
        profile.default_vehicle = Vehicle.objects.filter(facility=b.facility, pk=to_int(p.get('default_vehicle'), 0)).first()
        profile.note = (p.get('note') or '').strip()[:200]
        profile.save()
        if 'ride_ng_present' in p:      # 同じ車に乗せない利用者（同じ事業所の人だけ。相手の側にも付く）
            ids = [to_int(x, 0) for x in p.getlist('no_ride_with')]
            b.no_ride_with.set(Beneficiary.objects.filter(facility=b.facility, pk__in=ids).exclude(pk=b.pk))
        messages.success(request, f'{b.full_name} さんの送迎の設定を保存しました。')
        return back


def driver_today(user, day=None):
    """
    ホームの「きょうの送迎」（2026-11-09 オウル）。ログインした職員が運転手（Driver.staff）に結びついていれば、
    その人が運転する行（迎え・送り）と、車両ごとの人数・注意を返す。運転手でなければ None
    """
    facility = getattr(user, 'facility', None)
    if facility is None or not getattr(facility, 'use_transport', False):
        return None
    my_drivers = list(Driver.objects.filter(facility=facility, staff=user, is_active=True))
    if not my_drivers:
        return None
    day = day or datetime.date.today()
    board = build_board(facility, day)
    my_ids = {d.pk for d in my_drivers}
    out = {'day': day, 'drivers': my_drivers, 'pickup': [], 'dropoff': [], 'problems': 0}
    for direction, _label in DIRECTIONS:
        rows = [x for x in board[direction] if not x['skip'] and x['driver_id'] in my_ids]
        for x in rows:
            x['place_shown'] = x['place'] or x['default_place']
            out['problems'] += int(bool(x['ride_ng'])) + int(x['driver_ng'])
        out[direction] = rows
    out['total'] = len(out['pickup']) + len(out['dropoff'])
    return out

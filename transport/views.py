"""送迎・配車の画面：日ごとの配車表（迎え／送り）、車両・運転手の登録、利用者ごとの送迎の設定"""
import datetime

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from config.concurrency import check_conflict
from config.pdf import pdf_or_html
from config.utils import to_int

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
                .select_related('beneficiary', 'beneficiary__transport', 'beneficiary__transport__default_vehicle',
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
    drivers = {d.pk: d for d in Driver.objects.filter(facility=facility)}
    board = {'pickup': [], 'dropoff': [], 'others': {DIRECTION_PICKUP: [], DIRECTION_DROPOFF: []}, 'counts': {}}
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
            board[direction].append({
                'key': row_key(r.pk, direction), 'reservation': r, 'beneficiary': r.beneficiary, 'profile': profile, 'assignment': a,
                'time': (a.time if a and a.time else (profile.time(direction) if profile else None)),
                'place': (a.place if a and a.place else (profile.place(direction) if profile else '')),
                'vehicle_id': vehicle.pk if vehicle else None, 'vehicle': vehicle,
                'driver_id': driver_id, 'driver': drivers.get(driver_id),
                'note': a.note if a else '', 'profile_note': profile.note if profile else '',
                'skip': bool(a and a.skip), 'added': bool(a and a.added),
            })
    for direction, _label in DIRECTIONS:
        rows = board[direction]
        rows.sort(key=lambda x: (x['skip'], x['time'] is None, x['time'] or datetime.time.min,
                                 x['beneficiary'].last_name_kana, x['beneficiary'].first_name_kana))
        by_vehicle = {}
        for x in rows:
            if not x['skip'] and x['vehicle']:
                by_vehicle.setdefault(x['vehicle'].pk, {'vehicle': x['vehicle'], 'count': 0})['count'] += 1
        counts = sorted(by_vehicle.values(), key=lambda c: (c['vehicle'].order, c['vehicle'].pk))
        for c in counts:
            c['over'] = bool(c['vehicle'].capacity and c['count'] > c['vehicle'].capacity)
        board['counts'][direction] = counts
        board[f'{direction}_active'] = sum(1 for x in rows if not x['skip'])
    return board


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
        'drivers': list(Driver.objects.filter(facility=facility, is_active=True)),
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
            'drivers': list(Driver.objects.filter(facility=facility)),
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
            d.save()
            messages.success(request, f'運転手「{d.name}」を{"保存" if what == "save" else "登録"}しました。')
        return redirect('transport:vehicles')


def profile_context(beneficiary):
    """利用者情報の「送迎」欄に渡すもの（送迎・配車を使う事業所だけ呼ぶ）"""
    profile = TransportProfile.objects.filter(beneficiary=beneficiary).select_related('default_vehicle').first()
    return {'transport_profile': profile or TransportProfile(beneficiary=beneficiary),
            'transport_vehicles': list(Vehicle.objects.filter(facility=beneficiary.facility, is_active=True))}


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
        messages.success(request, f'{b.full_name} さんの送迎の設定を保存しました。')
        return back

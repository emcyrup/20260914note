"""
通所の利用日（2026-11-11 やすらぎの森の相談「やすらぎデイ」）：
1. サービス提供票（1 か月分・全員分。PDF・写真）を AI で読み、通所リハ（通所介護）の「予定」の行に 1 が入っている日を利用日にする
   → デイケアサービス利用予定・実績表（A）。実績（来た・休み）もこの表で付ける
2. 送迎表（B）：日付ごとに利用者の候補が出て、車両の便（迎え・昼の送り・夕の送り）へドラッグで入れる・並べ替える
3. 週間通所予定表（C）：A から曜日ごとに介護・予防・半日・総人数・ショートの送迎・勤務を自動で振り分ける
提供票のファイルはサーバーに残さない（読んだ結果の名前と日付だけを画面に返す）。
"""
import calendar
import datetime
import json
import logging
import re
import unicodedata

from django.contrib import messages
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views import View

from beneficiaries.models import Beneficiary
from config.pdf import pdf_or_html
from config.utils import to_int

from .models import WEEK_JP, DayUse, StaffingDay, TransportRun, TransportStop, week_start
from .views import CareMixin, WEEKDAY_FIELDS, _json_body, _text, jp_date, parse_date, parse_time

logger = logging.getLogger(__name__)


def month_of(request):
    today = datetime.date.today()
    y, m = to_int(request.GET.get('y'), today.year), to_int(request.GET.get('m'), today.month)
    if not (2000 <= y <= 2100 and 1 <= m <= 12):
        y, m = today.year, today.month
    return y, m


def month_days(y, m):
    return [datetime.date(y, m, d) for d in range(1, calendar.monthrange(y, m)[1] + 1)]


def shift_month(y, m, n):
    k = y * 12 + (m - 1) + n
    return k // 12, k % 12 + 1


def norm_name(s):
    s = re.sub(r'[\s・]+', '', unicodedata.normalize('NFKC', s or ''))
    return re.sub(r'(様|殿|さん)$', '', s)


def is_support(b):
    from kaigo.models import latest_insurance
    ins = latest_insurance(b)
    return bool(ins and ins.is_support)


def uses_on(facility, day):
    """その日の利用者（予定あり・休みでない）。予定が 1 件も無い日は、利用の曜日から（DayUse を作らずに仮の行で）"""
    qs = list(DayUse.objects.filter(facility=facility, date=day, planned=True).exclude(attended=False)
              .select_related('beneficiary').order_by('beneficiary__last_name_kana', 'beneficiary__first_name_kana'))
    if qs or DayUse.objects.filter(facility=facility, date__year=day.year, date__month=day.month).exists():
        return qs, False
    field = WEEKDAY_FIELDS[day.weekday()]
    if not field:
        return [], True
    people = Beneficiary.objects.filter(facility=facility, status=Beneficiary.STATUS_ACTIVE, **{field: True}).order_by('last_name_kana', 'first_name_kana')
    return [DayUse(facility=facility, beneficiary=b, date=day, planned=True, source='weekday') for b in people], True


# ---------------------------------------------------------------- サービス提供票の読み取り
TEIKYO_PROMPT = """これは居宅介護支援事業所が作った「サービス提供票（兼居宅サービス計画）」です。1 人 1〜2 枚で、何人分も続くことがあります。
スキャンが横向き・逆さまのこともあります。向きを直して読んでください。
読みたいのは、利用者ごとの「通所の日」です。

返す形（JSON だけ。説明は書かない）：
{"wareki_year": 8, "month": 9, "people": [
  {"name": "利用者の氏名（右上の『○○ 様』、または被保険者氏名）", "kana": "フリガナ（あれば）", "care_level": "要介護2 など（あれば）",
   "rows": [{"service": "サービス内容の欄の文字（例：通所リハⅠ262）", "time": "提供時間帯（例：09:30-16:00。無ければ空）", "basic": true, "plan_days": [1, 2, 4]}]}
]}

決まり
- wareki_year は「令和○年○月分」の令和の年、month はその月
- rows には、サービス内容の行のうち「通所」「デイ」「短期入所」の付くものだけを入れる。加算の行（処遇改善加算・入浴介助加算・マネジメント加算・提供体制強化加算・送迎減算など）は basic を false にする
- basic は、提供時間帯の付いた基本のサービスの行（例：通所リハⅠ262・通所介護Ⅰ23）だけ true
- plan_days は、その行の「予定」の段で 1 が書いてある日付（1〜31 の数）。「実績」の段は見ない。読めない日は入れない（推測しない）
- 氏名は書いてあるとおり。被保険者番号・住所・電話などほかの個人の情報は返さない"""


def read_teikyo(uploaded):
    """(結果, None) か (None, エラーの文)。結果：{'year', 'month', 'people': [{'name', 'kana', 'care_level', 'days', 'half', 'services'}]}"""
    from ai_assist.quick import ask_ai
    from kaigo.ai import image_block
    try:
        block = image_block(uploaded)
    except ValueError as e:
        return None, str(e)
    raw, error = ask_ai(TEIKYO_PROMPT, [block, {'type': 'text', 'text': '通所の日を JSON で返してください。'}], 8000)
    if error is not None:
        try:
            return None, json.loads(error.content.decode('utf-8')).get('error', 'AI で読めませんでした。')
        except (ValueError, AttributeError):
            return None, 'AI で読めませんでした。'
    return parse_teikyo(raw), None


def parse_teikyo(raw):
    body = (raw or '').strip()
    try:
        data = json.loads(body[body.find('{'):body.rfind('}') + 1])
    except (ValueError, TypeError):
        return {'year': None, 'month': None, 'people': [], 'error': '読み取った結果の形が正しくありませんでした。もう一度読むか、写真を撮り直してください。'}
    wy, mo = to_int(data.get('wareki_year'), 0), to_int(data.get('month'), 0)
    year = 2018 + wy if 1 <= wy <= 30 else None
    month = mo if 1 <= mo <= 12 else None
    people = []
    for p in data.get('people') or []:
        if not isinstance(p, dict):
            continue
        rows = [r for r in (p.get('rows') or []) if isinstance(r, dict)]
        basic = [r for r in rows if r.get('basic') and re.search(r'通所|デイ', str(r.get('service') or ''))]
        use = basic or [r for r in rows if re.search(r'通所|デイ', str(r.get('service') or '')) and not re.search(r'加算|減算', str(r.get('service') or ''))]
        days = sorted({d for r in use for d in (to_int(x, 0) for x in (r.get('plan_days') or [])) if 1 <= d <= 31})
        half = any(_short_time(str(r.get('time') or '')) for r in use)
        people.append({'name': str(p.get('name') or '')[:50], 'kana': str(p.get('kana') or '')[:50], 'care_level': str(p.get('care_level') or '')[:10],
                       'days': days, 'half': half, 'services': [str(r.get('service') or '')[:40] for r in use]})
    return {'year': year, 'month': month, 'people': people, 'error': ''}


def _short_time(text):
    """提供時間帯が 4 時間未満なら半日"""
    m = re.findall(r'(\d{1,2})[:：](\d{2})', text)
    if len(m) < 2:
        return False
    a, b = int(m[0][0]) * 60 + int(m[0][1]), int(m[1][0]) * 60 + int(m[1][1])
    return 0 < b - a < 240


def match_person(people, name, kana=''):
    n, k = norm_name(name), norm_name(kana)
    for b in people:
        if n and norm_name(b.last_name + b.first_name) == n:
            return b
    for b in people:
        if k and norm_name(b.last_name_kana + b.first_name_kana) == norm_name(unicodedata.normalize('NFKC', k)):
            return b
    return None


class TeikyoView(CareMixin, View):
    template_name = 'nursing/teikyo.html'

    def get(self, request):
        y, m = month_of(request)
        from django.conf import settings
        return render(request, self.template_name, {'y': y, 'm': m, 'people': self.people(), 'ai_enabled': bool(settings.ANTHROPIC_API_KEY)})


class TeikyoReadView(CareMixin, View):
    """1 ファイルずつ読む（何人分でも。全員分は画面から 1 つずつ順に送る）"""

    def post(self, request):
        f = request.FILES.get('file')
        if not f:
            return JsonResponse({'error': 'ファイルを選んでください。'}, status=400)
        result, error = read_teikyo(f)
        if error:
            return JsonResponse({'error': error}, status=400)
        people = list(self.people())
        for p in result['people']:
            b = match_person(people, p['name'], p['kana'])
            p['b'] = b.pk if b else None
            p['b_name'] = b.full_name if b else ''
        return JsonResponse(result)


class TeikyoApplyView(CareMixin, View):
    """読んだ結果（画面で確かめた分）を利用日へ。その月の提供票からの予定は置きかえる（実績の付いた日は残す）"""

    def post(self, request):
        data = _json_body(request)
        y, m = to_int(data.get('year'), 0), to_int(data.get('month'), 0)
        if not (2000 <= y <= 2100 and 1 <= m <= 12):
            return JsonResponse({'error': '年月が読めませんでした。'}, status=400)
        last = calendar.monthrange(y, m)[1]
        people = {b.pk: b for b in self.people()}
        done, total = 0, 0
        with transaction.atomic():
            for row in data.get('rows') or []:
                b = people.get(to_int(row.get('b'), 0))
                if not b:
                    continue
                days = sorted({d for d in (to_int(x, 0) for x in (row.get('days') or [])) if 1 <= d <= last})
                half = bool(row.get('half'))
                dates = {datetime.date(y, m, d) for d in days}
                old = DayUse.objects.filter(beneficiary=b, date__year=y, date__month=m)
                for u in old:
                    if u.date not in dates and u.source == 'teikyo' and u.attended is None:
                        u.delete()
                for d in dates:
                    u, _ = DayUse.objects.get_or_create(beneficiary=b, date=d, defaults={'facility': self.facility, 'source': 'teikyo', 'half': half})
                    u.planned, u.source = True, 'teikyo'
                    u.half = half or u.half
                    u.save()
                done += 1
                total += len(dates)
        messages.success(request, f'提供票から {done} 人・{total} 日分の予定を {y}年{m}月の利用予定・実績表に入れました。')
        return JsonResponse({'ok': True, 'url': reverse('nursing:day_month') + f'?y={y}&m={m}'})


# ---------------------------------------------------------------- デイケアサービス利用予定・実績表（A）
def month_table(facility, y, m, everyone=False):
    days = month_days(y, m)
    uses = DayUse.objects.filter(facility=facility, date__year=y, date__month=m).select_related('beneficiary')
    by_b = {}
    for u in uses:
        by_b.setdefault(u.beneficiary_id, {})[u.date.day] = u
    people = Beneficiary.objects.filter(facility=facility).order_by('last_name_kana', 'first_name_kana')
    people = [b for b in people if b.pk in by_b or (everyone and b.status == Beneficiary.STATUS_ACTIVE)]
    rows = []
    for b in people:
        cells = []
        for d in days:
            u = by_b.get(b.pk, {}).get(d.day)
            cells.append({'d': d, 'u': u, 'plan': bool(u and u.planned), 'att': u.attended if u else None})
        rows.append({'b': b, 'cells': cells, 'plan_n': sum(c['plan'] for c in cells), 'att_n': sum(1 for c in cells if c['att'])})
    totals = [{'d': d, 'plan': sum(1 for r in rows if r['cells'][i]['plan']), 'att': sum(1 for r in rows if r['cells'][i]['att'])} for i, d in enumerate(days)]
    return days, rows, totals


class DayMonthView(CareMixin, View):
    template_name = 'nursing/day_month.html'

    def get(self, request):
        y, m = month_of(request)
        everyone = request.GET.get('all') == '1'
        days, rows, totals = month_table(self.facility, y, m, everyone)
        py, pm = shift_month(y, m, -1)
        ny, nm = shift_month(y, m, 1)
        return render(request, self.template_name, {'y': y, 'm': m, 'days': days, 'rows': rows, 'totals': totals, 'everyone': everyone,
                                                    'heads': [{'d': d, 'w': WEEK_JP[d.weekday()]} for d in days],
                                                    'prev': (py, pm), 'next': (ny, nm), 'today': datetime.date.today(), 'week_jp': WEEK_JP,
                                                    'people': self.people()})

    def post(self, request):
        y, m = to_int(request.POST.get('y'), 0), to_int(request.POST.get('m'), 0)
        back = reverse('nursing:day_month') + f'?y={y}&m={m}'
        if not (2000 <= y <= 2100 and 1 <= m <= 12):
            return redirect(reverse('nursing:day_month'))
        action = request.POST.get('action')
        if action == 'weekday':
            # 利用の曜日から予定を作る（その月に予定が 1 件も無い人だけ）
            made = 0
            for b in self.people():
                if DayUse.objects.filter(beneficiary=b, date__year=y, date__month=m).exists():
                    continue
                for d in month_days(y, m):
                    field = WEEKDAY_FIELDS[d.weekday()]
                    if field and getattr(b, field):
                        DayUse.objects.create(facility=self.facility, beneficiary=b, date=d, planned=True, source='weekday')
                        made += 1
            messages.success(request, f'利用の曜日から {made} 日分の予定を作りました（予定がすでにある人はそのまま）。')
        elif action == 'add_person':
            b = self.people().filter(pk=to_int(request.POST.get('b'), 0)).first()
            d = parse_date(request.POST.get('d'))
            if b and d and d.year == y and d.month == m:
                DayUse.objects.get_or_create(beneficiary=b, date=d, defaults={'facility': self.facility, 'source': 'manual'})
                messages.success(request, f'{b.full_name}さんの {jp_date(d)} の予定を足しました。')
        return redirect(back)


class DayToggleView(CareMixin, View):
    """表のマスを押したとき（JSON）：part=plan は予定の ○ をつける／外す、part=att は実績を 空 → ○（来た）→ ×（休み）→ 空"""

    def post(self, request):
        data = _json_body(request)
        b = Beneficiary.objects.filter(facility=self.facility, pk=to_int(data.get('b'), 0)).first()
        d = parse_date(data.get('d'))
        if not b or not d:
            return JsonResponse({'error': '利用者か日付が正しくありません。'}, status=400)
        u = DayUse.objects.filter(beneficiary=b, date=d).first()
        part = data.get('part')
        if part == 'plan':
            if u and u.planned:
                if u.attended is None:
                    u.delete()
                    u = None
                else:
                    u.planned = False
                    u.save()
            else:
                u = u or DayUse(facility=self.facility, beneficiary=b, date=d, source='manual')
                u.planned = True
                u.save()
        elif part == 'att':
            u = u or DayUse(facility=self.facility, beneficiary=b, date=d, planned=False, source='manual')
            u.attended = {None: True, True: False, False: None}[u.attended]
            if not u.planned and u.attended is None:
                if u.pk:
                    u.delete()
                u = None
            else:
                u.save()
        elif part in ('half', 'short'):
            if not u:
                return JsonResponse({'error': '予定の無い日です。'}, status=400)
            setattr(u, part, not getattr(u, part))
            u.save()
        else:
            return JsonResponse({'error': '押した場所が分かりません。'}, status=400)
        return JsonResponse({'plan': bool(u and u.planned), 'att': u.attended if u else None, 'half': bool(u and u.half), 'short': bool(u and u.short)})


class DayMonthPdfView(CareMixin, View):
    def get(self, request):
        y, m = month_of(request)
        days, rows, totals = month_table(self.facility, y, m, request.GET.get('all') == '1')
        pad = max(0, 10 - len(rows))
        return pdf_or_html(request, 'nursing/pdf/day_month.html', {
            'facility': self.facility, 'y': y, 'm': m, 'wareki': y - 2018, 'days': days, 'rows': rows, 'totals': totals, 'pad': range(pad),
            'heads': [{'d': d, 'w': WEEK_JP[d.weekday()]} for d in days],
            'blank_days': range(31 - len(days)), 'week_jp': WEEK_JP,
            'back_url': reverse('nursing:day_month') + f'?y={y}&m={m}'}, f'利用予定実績表_{y}{m:02d}')


# ---------------------------------------------------------------- 送迎表（B）：ドラッグで組む
RUN_KEYS = [k for k, _ in TransportRun.RUNS]
RUN_LABELS = dict(TransportRun.RUNS)


def run_candidates(facility, day):
    """便ごとの候補：迎え＝迎えありの人、昼の送り＝半日で送りありの人、夕の送り＝半日でなく送りありの人。送迎の設定が無い人は迎え・夕の送りの候補"""
    from transport.models import TransportProfile
    uses, fallback = uses_on(facility, day)
    profiles = {t.beneficiary_id: t for t in TransportProfile.objects.filter(beneficiary__in=[u.beneficiary for u in uses])}
    cands = {k: [] for k in RUN_KEYS}
    walk = []
    for u in uses:
        t = profiles.get(u.beneficiary_id)
        pickup = t.pickup if t else True
        drop = t.dropoff if t else True
        if t and not (t.pickup or t.dropoff):
            walk.append(u)
        if pickup:
            cands['am'].append(u)
        if drop:
            cands['noon' if u.half else 'pm'].append(u)
    return uses, cands, walk, profiles, fallback


def board_state(facility, day):
    from transport.models import Driver, Vehicle
    vehicles = list(Vehicle.objects.filter(facility=facility, is_active=True).select_related('default_driver'))
    drivers = {d.name: d for d in Driver.objects.filter(facility=facility, is_active=True)}
    uses, cands, walk, profiles, fallback = run_candidates(facility, day)
    saved = {(r.run, r.vehicle_id): r for r in TransportRun.objects.filter(facility=facility, date=day).prefetch_related('stops__beneficiary')}
    from beneficiaries.models import Beneficiary as _B
    pairs = {}
    for a, b in _B.no_ride_with.through.objects.filter(from_beneficiary__facility=facility).values_list('from_beneficiary_id', 'to_beneficiary_id'):
        pairs.setdefault(a, set()).add(b)
    use_by_b = {u.beneficiary_id: u for u in uses}
    runs = []
    for key in RUN_KEYS:
        placed = set()
        lanes = []
        for v in vehicles:
            r = saved.get((key, v.pk))
            stops = list(r.stops.all()) if r else []
            placed |= {s.beneficiary_id for s in stops}
            ids = [s.beneficiary_id for s in stops]
            wheel = sum(1 for i in ids if profiles.get(i) and profiles[i].wheelchair)
            seats = v.seats_used(len(ids), wheel)
            ng_pairs = [(x, y) for i, x in enumerate(ids) for y in ids[i + 1:] if y in pairs.get(x, ())]
            drv = drivers.get(r.driver) if r and r.driver else None
            lanes.append({'v': v, 'run': r, 'stops': [{'b': s.beneficiary, 'time': s.time, 'use': use_by_b.get(s.beneficiary_id),
                                                       'wheel': bool(profiles.get(s.beneficiary_id) and profiles[s.beneficiary_id].wheelchair),
                                                       'ng': any(s.beneficiary_id in p for p in ng_pairs)} for s in stops],
                          'seats': seats, 'over': bool(v.capacity and seats > v.capacity), 'wc_over': wheel > v.wheelchair_slots,
                          'ride_ng': bool(ng_pairs), 'driver_ng': bool(drv and not drv.can_drive(v)),
                          'driver': r.driver if r else (v.default_driver.name if v.default_driver else ''),
                          'escort': r.escort if r else '', 'depart': r.depart if r else None})
        pool = [u for u in cands[key] if u.beneficiary_id not in placed]
        runs.append({'key': key, 'label': RUN_LABELS[key], 'lanes': lanes, 'pool': pool,
                     'problems': sum(int(l['over']) + int(l['wc_over']) + int(l['ride_ng']) + int(l['driver_ng']) for l in lanes)})
    return {'vehicles': vehicles, 'runs': runs, 'uses': uses, 'walk': walk, 'fallback': fallback, 'profiles': profiles, 'drivers': list(drivers)}


def auto_place(facility, day):
    """まだ組んでいない便に、候補をいつもの車両へ（送迎の設定の時刻の順）。組んである便は触らない"""
    from transport.models import Vehicle
    vehicles = {v.pk: v for v in Vehicle.objects.filter(facility=facility, is_active=True).select_related('default_driver')}
    uses, cands, walk, profiles, fallback = run_candidates(facility, day)
    made = 0
    for key in RUN_KEYS:
        if TransportRun.objects.filter(facility=facility, date=day, run=key).exists():
            continue
        direction = 'pickup' if key == 'am' else 'dropoff'
        groups = {}
        for u in cands[key]:
            t = profiles.get(u.beneficiary_id)
            if t and t.default_vehicle_id in vehicles:
                groups.setdefault(t.default_vehicle_id, []).append((t.time(direction), u))
        for vid, items in groups.items():
            v = vehicles[vid]
            r = TransportRun.objects.create(facility=facility, date=day, run=key, vehicle=v, driver=v.default_driver.name if v.default_driver else '')
            items.sort(key=lambda x: (x[0] is None, x[0] or datetime.time.min, x[1].beneficiary.last_name_kana))
            for i, (tm, u) in enumerate(items):
                TransportStop.objects.create(run=r, beneficiary=u.beneficiary, order=i, time=tm)
                made += 1
    return made


class TransportBoardView(CareMixin, View):
    template_name = 'nursing/transport_board.html'

    def get(self, request):
        day = parse_date(request.GET.get('d'), datetime.date.today())
        state = board_state(self.facility, day)
        return render(request, self.template_name, {'day': day, 'day_jp': jp_date(day), 'state': state, 'today': datetime.date.today(),
                                                    'prev': day - datetime.timedelta(days=1), 'next': day + datetime.timedelta(days=1),
                                                    'use_transport': self.facility.use_transport})

    def post(self, request):
        """action=auto：いつもの車両で並べる。JSON（画面の保存）：便ごとの車両・運転手・添乗・出発と、順番・時刻、半日・ショート"""
        if request.content_type == 'application/json':
            return self.save_json(request)
        day = parse_date(request.POST.get('d'), datetime.date.today())
        back = reverse('nursing:transport_board') + f'?d={day.isoformat()}'
        if request.POST.get('action') == 'auto':
            n = auto_place(self.facility, day)
            messages.success(request, f'まだ組んでいない便に、いつもの車両で {n} 人を並べました。ドラッグで直してから保存してください。' if n else
                             'いつもの車両が決まっている人がいないか、便はもう組んであります。')
        elif request.POST.get('action') == 'clear':
            TransportRun.objects.filter(facility=self.facility, date=day).delete()
            messages.success(request, f'{jp_date(day)} の送迎表を空にしました。')
        return redirect(back)

    def save_json(self, request):
        from transport.models import Vehicle
        data = _json_body(request)
        day = parse_date(data.get('d'))
        if not day:
            return JsonResponse({'error': '日付が正しくありません。'}, status=400)
        vehicles = {v.pk: v for v in Vehicle.objects.filter(facility=self.facility)}
        people = {b.pk: b for b in Beneficiary.objects.filter(facility=self.facility)}
        with transaction.atomic():
            TransportRun.objects.filter(facility=self.facility, date=day).delete()
            n = 0
            for lane in data.get('lanes') or []:
                key, v = lane.get('run'), vehicles.get(to_int(lane.get('vehicle'), 0))
                if key not in RUN_KEYS or not v:
                    continue
                stops = [s for s in (lane.get('stops') or []) if to_int(s.get('b'), 0) in people]
                driver, escort = str(lane.get('driver') or '')[:50], str(lane.get('escort') or '')[:50]
                depart = parse_time(lane.get('depart'))
                if not stops and not (driver and (escort or depart)):
                    continue
                r = TransportRun.objects.create(facility=self.facility, date=day, run=key, vehicle=v, driver=driver, escort=escort, depart=depart)
                seen = set()
                for i, s in enumerate(stops):
                    bid = to_int(s.get('b'), 0)
                    if bid in seen:
                        continue
                    seen.add(bid)
                    TransportStop.objects.create(run=r, beneficiary=people[bid], order=i, time=parse_time(s.get('time')))
                    n += 1
            for f in data.get('flags') or []:
                u = DayUse.objects.filter(facility=self.facility, date=day, beneficiary_id=to_int(f.get('b'), 0)).first()
                if u:
                    u.half, u.short = bool(f.get('half')), bool(f.get('short'))
                    u.save(update_fields=['half', 'short', 'updated_at'])
        messages.success(request, f'{jp_date(day)} の送迎表を保存しました（{n} 人）。')
        return JsonResponse({'ok': True})


def week_runs(facility, start, n=6):
    days = [start + datetime.timedelta(days=i) for i in range(n)]
    runs = TransportRun.objects.filter(facility=facility, date__in=days).select_related('vehicle').prefetch_related('stops__beneficiary')
    out = []
    for d in days:
        mine = [r for r in runs if r.date == d]
        mine.sort(key=lambda r: (RUN_KEYS.index(r.run), r.vehicle.order, r.vehicle.pk))
        out.append({'d': d, 'week': WEEK_JP[d.weekday()], 'runs': mine})
    return out


class TransportBoardPdfView(CareMixin, View):
    def get(self, request):
        day = parse_date(request.GET.get('d'), datetime.date.today())
        span = request.GET.get('span', 'week')
        if span == 'day':
            cols = week_runs(self.facility, day, 1)
        else:
            cols = week_runs(self.facility, week_start(day), 6)
        return pdf_or_html(request, 'nursing/pdf/transport_board.html', {
            'facility': self.facility, 'cols': cols, 'day': day, 'span': span, 'wareki': day.year - 2018, 'run_labels': RUN_LABELS, 'end': cols[-1]['d'],
            'back_url': reverse('nursing:transport_board') + f'?d={day.isoformat()}'}, f'送迎表_{day:%Y%m%d}')


# ---------------------------------------------------------------- 週間通所予定表（C）
def week_plan(facility, start, setting):
    days = [start + datetime.timedelta(days=i) for i in range(6)]
    notes = {s.date: s for s in StaffingDay.objects.filter(facility=facility, date__in=days)}
    support_cache = {}
    out = []
    for d in days:
        uses, fallback = uses_on(facility, d)
        groups = {'care': [], 'prev': [], 'care_half': [], 'prev_half': []}
        for u in uses:
            b = u.beneficiary
            if b.pk not in support_cache:
                support_cache[b.pk] = is_support(b)
            key = ('prev' if support_cache[b.pk] else 'care') + ('_half' if u.half else '')
            groups[key].append(u)
        total = len(uses)
        out.append({'d': d, 'week': WEEK_JP[d.weekday()], 'groups': groups, 'total': total, 'short': [u for u in uses if u.short],
                    'note': notes[d].note if d in notes and notes[d].note != '【サンプル】' else '', 'fallback': fallback, 'cap': setting.capacity,
                    'over': bool(setting.capacity and total > setting.capacity)})
    return out


class WeekPlanView(CareMixin, View):
    template_name = 'nursing/week_plan.html'

    def get(self, request):
        start = week_start(parse_date(request.GET.get('d'), datetime.date.today()))
        cols = week_plan(self.facility, start, self.setting)
        return render(request, self.template_name, {'start': start, 'cols': cols, 'prev': start - datetime.timedelta(days=7),
                                                    'next': start + datetime.timedelta(days=7), 'today': datetime.date.today(),
                                                    'max_care': max([len(c['groups']['care']) for c in cols] + [0])})

    def post(self, request):
        start = week_start(parse_date(request.POST.get('d'), datetime.date.today()))
        for i in range(6):
            d = start + datetime.timedelta(days=i)
            note = _text(request.POST, f'note_{d.isoformat()}', 200)
            s = StaffingDay.objects.filter(facility=self.facility, date=d).first()
            if s or note:
                s = s or StaffingDay(facility=self.facility, date=d)
                s.note = note
                s.save()
        messages.success(request, '勤務（半日・休み）を保存しました。')
        return redirect(reverse('nursing:week_plan') + f'?d={start.isoformat()}')


class WeekPlanPdfView(CareMixin, View):
    def get(self, request):
        start = week_start(parse_date(request.GET.get('d'), datetime.date.today()))
        cols = week_plan(self.facility, start, self.setting)
        rows = max([len(c['groups']['care']) for c in cols] + [16])
        prev_rows = max([len(c['groups']['prev']) for c in cols] + [4])
        return pdf_or_html(request, 'nursing/pdf/week_plan.html', {
            'facility': self.facility, 'start': start, 'end': start + datetime.timedelta(days=5), 'cols': cols, 'wareki': start.year - 2018,
            'care_rows': range(rows), 'prev_rows': range(prev_rows),
            'back_url': reverse('nursing:week_plan') + f'?d={start.isoformat()}'}, f'週間通所予定表_{start:%Y%m%d}')

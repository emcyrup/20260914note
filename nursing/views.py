"""
介護施設（やすらぎの森）の画面：ホーム・検温板・介護記録（AI の下書き・承認）・連絡帳（LINE・印刷）・ヒヤリハット／事故報告・
薬の管理・通所介護計画書・人員配置・送迎表・資料の検索。画面の型「介護施設」の事業所だけ。
"""
import datetime
import json
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from beneficiaries.models import Beneficiary, Guardian
from config.concurrency import check_conflict
from config.pdf import pdf_or_html
from config.utils import to_int

from .models import (ANSWERS, CHECK_ITEMS, LANGS, PLAN_ROW_KEYS, RECORD_CHARS_CHOICES, STATUS_APPROVED, STATUS_CHOICES, STATUS_DRAFT,
                     STATUS_REVIEW, WEEK_JP, CarePlan, CareRecord, IncidentReport, Medication, NursingSetting, StaffingDay, VitalsLog,
                     week_start)

logger = logging.getLogger(__name__)
TEXT_MAX = 6000


# ---------------------------------------------------------------- 共通
class CareMixin(LoginRequiredMixin):
    """画面の型「介護施設」の事業所だけ"""

    def dispatch(self, request, *args, **kwargs):
        facility = getattr(request.user, 'facility', None)
        if request.user.is_authenticated and not (facility is not None and facility.is_care):
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    @property
    def facility(self):
        return self.request.user.facility

    @property
    def setting(self):
        if not hasattr(self, '_setting'):
            self._setting = NursingSetting.of(self.facility)
            self.facility._nursing_setting = self._setting
        return self._setting

    def people(self):
        return Beneficiary.objects.filter(facility=self.facility, status=Beneficiary.STATUS_ACTIVE).order_by('last_name_kana', 'first_name_kana')

    def person_or_404(self, pk):
        return get_object_or_404(Beneficiary, pk=to_int(pk, 0), facility=self.facility)

    def can_approve(self):
        u = self.request.user
        return u.is_admin or u.is_superuser or u.is_child_dev_manager


def parse_date(value, default=None):
    try:
        return datetime.date.fromisoformat((value or '').strip())
    except ValueError:
        return default


def parse_time(value):
    try:
        return datetime.time.fromisoformat((value or '').strip()[:5])
    except ValueError:
        return None


def _text(p, key, n=TEXT_MAX):
    return (p.get(key) or '').strip()[:n]


def _int(p, key, lo, hi):
    v = to_int(p.get(key), None)
    if v is None or v < lo or v > hi:
        return None
    return v


def _dec(p, key):
    from decimal import Decimal, InvalidOperation
    s = (p.get(key) or '').strip().replace('℃', '')
    if not s:
        return None
    try:
        v = Decimal(s).quantize(Decimal('0.1'))
    except InvalidOperation:
        return None
    return v if Decimal('30') <= v <= Decimal('45') else None


def _json_body(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except (ValueError, UnicodeDecodeError):
        return {}


def latest_insurance_of(b):
    from kaigo.models import latest_insurance
    return latest_insurance(b)


def jp_date(d):
    return f'{d.month}月{d.day}日（{WEEK_JP[d.weekday()]}）'


# ---------------------------------------------------------------- ホーム
class HomeView(CareMixin, View):
    template_name = 'nursing/home.html'

    def get(self, request):
        f = self.facility
        today = datetime.date.today()
        people = list(self.people())
        n = len(people)
        vitals_done = VitalsLog.objects.filter(facility=f, date=today, slot=VitalsLog.SLOT_AM).exclude(temp__isnull=True).count()
        fevers = [v for v in VitalsLog.objects.filter(facility=f, date=today).select_related('beneficiary') if v.alerts]
        records = {r.beneficiary_id: r for r in CareRecord.objects.filter(facility=f, date=today)}
        unwritten = [b for b in people if b.pk not in records or records[b.pk].is_empty]
        review = CareRecord.objects.filter(facility=f, status=STATUS_REVIEW).select_related('beneficiary', 'author').order_by('date')[:30]
        approved_today = sum(1 for r in records.values() if r.status == STATUS_APPROVED)
        incidents_open = IncidentReport.objects.filter(facility=f).exclude(status=STATUS_APPROVED).select_related('beneficiary')[:10]
        meds = [m for m in Medication.objects.filter(facility=f, is_active=True, review_due__isnull=False).select_related('beneficiary') if m.due_state(today)]
        plans = {}
        for p in CarePlan.objects.filter(facility=f, beneficiary__status=Beneficiary.STATUS_ACTIVE).select_related('beneficiary').order_by('beneficiary_id', '-created_on', '-pk'):
            plans.setdefault(p.beneficiary_id, p)
        plan_alerts = [p for p in plans.values() if p.review_state(today)]
        no_plan = [b for b in people if b.pk not in plans]
        staffing = StaffingDay.objects.filter(facility=f, date=today).first()
        return render(request, self.template_name, {
            'today': today, 'today_jp': jp_date(today), 'n': n, 'vitals_done': vitals_done, 'fevers': fevers,
            'unwritten': unwritten, 'written': n - len(unwritten), 'review': review, 'review_count': CareRecord.objects.filter(facility=f, status=STATUS_REVIEW).count(),
            'approved_today': approved_today, 'incidents_open': incidents_open, 'meds': meds, 'plan_alerts': plan_alerts, 'no_plan': no_plan,
            'staffing': staffing, 'staffing_judge': staffing.judge() if staffing else None, 'can_approve': self.can_approve(),
            'family_flags': [r for r in records.values() if 'family' in (r.checks or {}) and r.checks.get('family') == 'yes'],
        })


# ---------------------------------------------------------------- 検温板
def read_vitals(log, p, prefix):
    log.temp = _dec(p, prefix + 'temp')
    log.bp_high = _int(p, prefix + 'bp_high', 40, 300)
    log.bp_low = _int(p, prefix + 'bp_low', 20, 200)
    log.pulse = _int(p, prefix + 'pulse', 20, 250)
    log.spo2 = _int(p, prefix + 'spo2', 50, 100)
    log.meal_main = _int(p, prefix + 'meal_main', 0, 10)
    log.meal_side = _int(p, prefix + 'meal_side', 0, 10)
    log.water_ml = _int(p, prefix + 'water_ml', 0, 5000)
    log.urine = _int(p, prefix + 'urine', 0, 30)
    log.stool = _int(p, prefix + 'stool', 0, 30)
    log.bath = p.get(prefix + 'bath') == '1'
    log.note = _text(p, prefix + 'note', 200)


class VitalsDayView(CareMixin, View):
    """その日の検温板（全員を一覧で入れて、まとめて保存）"""
    template_name = 'nursing/vitals_day.html'

    def get(self, request):
        f = self.facility
        day = parse_date(request.GET.get('d'), datetime.date.today())
        slot = request.GET.get('slot') if request.GET.get('slot') in dict(VitalsLog.SLOTS) else VitalsLog.SLOT_AM
        people = list(self.people())
        logs = {l.beneficiary_id: l for l in VitalsLog.objects.filter(facility=f, date=day, slot=slot)}
        self.setting
        rows = []
        for b in people:
            log = logs.get(b.pk) or VitalsLog(facility=f, beneficiary=b, date=day, slot=slot)
            log.facility = f
            rows.append({'b': b, 'log': log})
        done = sum(1 for r in rows if r['log'].pk and not r['log'].is_empty)
        return render(request, self.template_name, {
            'day': day, 'day_jp': jp_date(day), 'slot': slot, 'slots': VitalsLog.SLOTS, 'rows': rows, 'done': done, 'today': datetime.date.today(),
            'prev': day - datetime.timedelta(days=1), 'next': day + datetime.timedelta(days=1), 'week': week_start(day),
            'meal_choices': [(n, f'{n}') for n in range(10, -1, -1)], 'fever': self.setting.fever_temp,
        })

    def post(self, request):
        f = self.facility
        p = request.POST
        day = parse_date(p.get('d'), datetime.date.today())
        slot = p.get('slot') if p.get('slot') in dict(VitalsLog.SLOTS) else VitalsLog.SLOT_AM
        ids = {b.pk for b in self.people()}
        n = 0
        with transaction.atomic():
            for bid in ids:
                prefix = f'b{bid}_'
                if f'{prefix}present' not in p:
                    continue
                log = VitalsLog.objects.filter(beneficiary_id=bid, date=day, slot=slot).first() or VitalsLog(facility=f, beneficiary_id=bid, date=day, slot=slot)
                read_vitals(log, p, prefix)
                if log.is_empty and not log.pk:
                    continue
                log.recorded_by = request.user
                log.save()
                n += 1
        messages.success(request, f'{jp_date(day)} {dict(VitalsLog.SLOTS)[slot]}の検温板を保存しました（{n} 人）。')
        return redirect(reverse('nursing:vitals_day') + f'?d={day.isoformat()}&slot={slot}')


def week_days(start):
    return [start + datetime.timedelta(days=i) for i in range(7)]


def week_sheet(b, start, setting=None):
    """1 人 1 週間の検温板：{'days': [...], 'cells': {(date, slot): log}, 'rows': [(label, key, [値...])]}"""
    days = week_days(start)
    logs = VitalsLog.objects.filter(beneficiary=b, date__range=(days[0], days[-1]))
    if setting is not None:
        for l in logs:
            l.facility._nursing_setting = setting
    cells = {(l.date, l.slot): l for l in logs}
    slots = [VitalsLog.SLOT_AM] + ([VitalsLog.SLOT_PM] if any(k[1] == VitalsLog.SLOT_PM for k in cells) else [])
    cols = [(d, s) for d in days for s in slots]
    col_info = [{'d': d, 's': s, 'wd': WEEK_JP[d.weekday()], 'slot': ('朝' if s == VitalsLog.SLOT_AM else '夕') if len(slots) > 1 else ''} for d, s in cols]
    items = [('体温（℃）', 'temp'), ('血圧（上/下）', 'bp_text'), ('脈拍', 'pulse'), ('SpO2（%）', 'spo2'), ('主食/副食（割）', 'meal_text'),
             ('水分（ml）', 'water_ml'), ('排尿（回）', 'urine'), ('排便（回）', 'stool'), ('入浴', 'bath'), ('特記', 'note')]
    rows = []
    for label, key in items:
        vals = []
        for d, s in cols:
            l = cells.get((d, s))
            v = getattr(l, key) if l else None
            if key == 'bath':
                v = '○' if v else ''
            vals.append({'v': '' if v is None else v, 'alert': bool(l and ((key == 'temp' and l.fever) or (key == 'bp_text' and l.bp_alert)
                                                                             or (key == 'pulse' and l.pulse_alert) or (key == 'spo2' and l.spo2_alert)))})
        rows.append({'label': label, 'key': key, 'vals': vals})
    return {'days': days, 'slots': slots, 'cols': cols, 'col_info': col_info, 'cells': cells, 'rows': rows, 'logs': [cells.get(c) for c in cols]}


def vitals_chart(sheet, fever):
    """1 週間の体温・血圧・脈拍の折れ線（SVG の文字列）"""
    cols = sheet['cols']
    W, H, L, T, R, B = 760, 230, 44, 14, 44, 40
    n = max(len(cols), 1)
    xs = [L + (W - L - R) * (i + 0.5) / n for i in range(n)]

    def series(key, lo, hi):
        pts = []
        for i, c in enumerate(cols):
            l = sheet['cells'].get(c)
            v = getattr(l, key, None) if l else None
            if v is None:
                continue
            y = T + (H - T - B) * (1 - (float(v) - lo) / (hi - lo))
            pts.append((xs[i], max(T, min(H - B, y)), v))
        return pts

    def path(pts, color, dash=''):
        if not pts:
            return ''
        d = ' '.join(f'{"M" if i == 0 else "L"}{x:.1f},{y:.1f}' for i, (x, y, _) in enumerate(pts))
        dots = ''.join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{color}"><title>{v}</title></circle>' for x, y, v in pts)
        return f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2"{f" stroke-dasharray={chr(34)}{dash}{chr(34)}" if dash else ""}/>{dots}'

    out = [f'<svg viewBox="0 0 {W} {H}" width="100%" style="max-height:260px;font-family:sans-serif;font-size:10px;" role="img" aria-label="1 週間の体温・血圧・脈拍">']
    # 体温の目盛（左）35〜40、血圧・脈拍の目盛（右）40〜200
    for i in range(6):
        y = T + (H - T - B) * i / 5
        out.append(f'<line x1="{L}" y1="{y:.1f}" x2="{W - R}" y2="{y:.1f}" stroke="#e5e0d6"/>')
        out.append(f'<text x="{L - 6}" y="{y + 3:.1f}" text-anchor="end" fill="#b3261e">{40 - i}</text>')
        out.append(f'<text x="{W - R + 6}" y="{y + 3:.1f}" fill="#2c4a7a">{200 - 32 * i}</text>')
    fy = T + (H - T - B) * (1 - (float(fever) - 35) / 5)
    out.append(f'<line x1="{L}" y1="{fy:.1f}" x2="{W - R}" y2="{fy:.1f}" stroke="#b3261e" stroke-dasharray="3 3" opacity=".6"/>')
    for i, (d, s) in enumerate(cols):
        label = f'{d.month}/{d.day}' + ('' if len(sheet['slots']) == 1 else ('朝' if s == 'am' else '夕'))
        out.append(f'<text x="{xs[i]:.1f}" y="{H - B + 14}" text-anchor="middle" fill="#555">{label}</text>')
        if len(sheet['slots']) == 1:
            out.append(f'<text x="{xs[i]:.1f}" y="{H - B + 26}" text-anchor="middle" fill="#888">{WEEK_JP[d.weekday()]}</text>')
    out.append(path(series('temp', 35, 40), '#b3261e'))
    out.append(path(series('bp_high', 40, 200), '#2c4a7a'))
    out.append(path(series('bp_low', 40, 200), '#2c4a7a', '4 3'))
    out.append(path(series('pulse', 40, 200), '#2f7d4f'))
    out.append(f'<text x="{L}" y="{H - 4}" fill="#b3261e">— 体温</text><text x="{L + 60}" y="{H - 4}" fill="#2c4a7a">— 血圧（上）</text>'
               f'<text x="{L + 140}" y="{H - 4}" fill="#2c4a7a">- - 血圧（下）</text><text x="{L + 230}" y="{H - 4}" fill="#2f7d4f">— 脈拍</text>')
    out.append('</svg>')
    return ''.join(out)


class VitalsWeekView(CareMixin, View):
    """1 人 1 週間の検温板（紙と同じ形）と折れ線グラフ"""
    template_name = 'nursing/vitals_week.html'

    def get(self, request, pk):
        b = self.person_or_404(pk)
        start = week_start(parse_date(request.GET.get('w'), datetime.date.today()))
        sheet = week_sheet(b, start, self.setting)
        return render(request, self.template_name, {
            'b': b, 'tab': 'vitals', 'start': start, 'end': start + datetime.timedelta(days=6), 'sheet': sheet, 'chart': vitals_chart(sheet, self.setting.fever_temp),
            'prev': start - datetime.timedelta(days=7), 'next': start + datetime.timedelta(days=7), 'insurance': latest_insurance_of(b),
            'today': datetime.date.today(), 'week_jp': WEEK_JP, 'fever': self.setting.fever_temp,
        })


class VitalsWeekPdfView(CareMixin, View):
    def get(self, request, pk):
        b = self.person_or_404(pk)
        start = week_start(parse_date(request.GET.get('w'), datetime.date.today()))
        sheet = week_sheet(b, start, self.setting)
        return pdf_or_html(request, 'nursing/pdf/vitals_week.html', {
            'b': b, 'facility': self.facility, 'start': start, 'end': start + datetime.timedelta(days=6), 'sheet': sheet,
            'chart': vitals_chart(sheet, self.setting.fever_temp), 'insurance': latest_insurance_of(b), 'week_jp': WEEK_JP,
            'back_url': reverse('nursing:vitals_week', args=[b.pk]) + f'?w={start.isoformat()}',
        }, f'検温板_{b.full_name}_{start:%Y%m%d}')


class VitalsWeekAllPdfView(CareMixin, View):
    """全員分の 1 週間の検温板をまとめて（1 人 1 枚）"""

    def get(self, request):
        start = week_start(parse_date(request.GET.get('w'), datetime.date.today()))
        sheets = [{'b': b, 'sheet': (sh := week_sheet(b, start, self.setting)), 'chart': vitals_chart(sh, self.setting.fever_temp),
                   'insurance': latest_insurance_of(b)} for b in self.people()]
        return pdf_or_html(request, 'nursing/pdf/vitals_week_all.html', {
            'facility': self.facility, 'start': start, 'end': start + datetime.timedelta(days=6), 'sheets': sheets, 'week_jp': WEEK_JP,
            'back_url': reverse('nursing:vitals_day') + f'?d={start.isoformat()}',
        }, f'検温板_全員_{start:%Y%m%d}')


# ---------------------------------------------------------------- 介護記録
class RecordsView(CareMixin, View):
    """その日の介護記録の一覧（誰が未記入か・確認待ちか）"""
    template_name = 'nursing/records.html'

    def get(self, request):
        f = self.facility
        day = parse_date(request.GET.get('d'), datetime.date.today())
        people = list(self.people())
        records = {r.beneficiary_id: r for r in CareRecord.objects.filter(facility=f, date=day).select_related('author', 'approved_by')}
        rows = [{'b': b, 'r': records.get(b.pk)} for b in people]
        counts = {'none': sum(1 for r in rows if r['r'] is None or r['r'].is_empty),
                  'draft': sum(1 for r in rows if r['r'] and not r['r'].is_empty and r['r'].status == STATUS_DRAFT),
                  'review': sum(1 for r in rows if r['r'] and r['r'].status == STATUS_REVIEW),
                  'approved': sum(1 for r in rows if r['r'] and r['r'].status == STATUS_APPROVED)}
        review_all = CareRecord.objects.filter(facility=f, status=STATUS_REVIEW).count()
        return render(request, self.template_name, {
            'day': day, 'day_jp': jp_date(day), 'rows': rows, 'counts': counts, 'today': datetime.date.today(), 'review_all': review_all,
            'prev': day - datetime.timedelta(days=1), 'next': day + datetime.timedelta(days=1), 'can_approve': self.can_approve(),
        })


class ReviewListView(CareMixin, View):
    """確認待ちの介護記録（リーダー・管理者がまとめて見て承認）"""
    template_name = 'nursing/review.html'

    def get(self, request):
        records = CareRecord.objects.filter(facility=self.facility, status=STATUS_REVIEW).select_related('beneficiary', 'author').order_by('date', 'beneficiary__last_name_kana')
        return render(request, self.template_name, {'records': records, 'can_approve': self.can_approve()})


class RecordEditView(CareMixin, View):
    """利用者 × 日の介護記録を書く（AI の下書き・整える・確認へ出す・承認）"""
    template_name = 'nursing/record_form.html'

    def _record(self, b, day):
        r = CareRecord.objects.filter(beneficiary=b, date=day).select_related('author', 'approved_by').first()
        return r or CareRecord(facility=self.facility, beneficiary=b, date=day)

    def get(self, request, pk):
        b = self.person_or_404(pk)
        day = parse_date(request.GET.get('d'), datetime.date.today())
        r = self._record(b, day)
        vitals = list(VitalsLog.objects.filter(beneficiary=b, date=day))
        for v in vitals:
            v.facility._nursing_setting = self.setting
        recent = CareRecord.objects.filter(beneficiary=b, date__lt=day).exclude(body='').order_by('-date')[:3]
        return render(request, self.template_name, {
            'b': b, 'tab': 'records', 'day': day, 'day_jp': jp_date(day), 'r': r, 'check_items': CHECK_ITEMS, 'answers': ANSWERS, 'langs': LANGS,
            'checks': r.checks if isinstance(r.checks, dict) else {}, 'vitals': vitals, 'recent': recent, 'setting': self.setting,
            'can_approve': self.can_approve(), 'insurance': latest_insurance_of(b), 'today': datetime.date.today(),
            'prev': day - datetime.timedelta(days=1), 'next': day + datetime.timedelta(days=1), 'status_choices': STATUS_CHOICES,
            'ai_enabled': bool(settings.ANTHROPIC_API_KEY),
            'guardian': Guardian.objects.filter(beneficiary=b).order_by('-is_primary', 'pk').first(),
        })

    def post(self, request, pk):
        b = self.person_or_404(pk)
        p = request.POST
        day = parse_date(p.get('d'), datetime.date.today())
        r = self._record(b, day)
        back = reverse('nursing:record_edit', args=[b.pk]) + f'?d={day.isoformat()}'
        action = p.get('action', 'save')
        if r.pk:
            conflict = check_conflict(request, r)
            if conflict:
                messages.error(request, conflict)
                return redirect(back)
        if action in ('approve', 'send_back'):
            if not self.can_approve():
                messages.error(request, '承認はリーダー・管理者が行います。')
                return redirect(back)
            if not r.pk:
                messages.error(request, 'まだ記録がありません。')
                return redirect(back)
            if action == 'approve':
                r.approve(request.user)
                messages.success(request, f'{b.full_name}さんの {jp_date(day)} の介護記録を承認しました。')
            else:
                r.send_back(p.get('review_note', ''))
                messages.success(request, '下書きに戻しました。記録した人にひとことが伝わります。')
            r.save()
            return redirect(back)
        if action == 'delete' and r.pk:
            r.delete()
            messages.success(request, f'{jp_date(day)} の介護記録を消しました。')
            return redirect(reverse('nursing:records') + f'?d={day.isoformat()}')
        # 保存（下書きのまま／確認へ出す）
        r.checks = {k: (p.get(f'chk_{k}') if p.get(f'chk_{k}') in ('yes', 'no') else '') for k, _ in CHECK_ITEMS}
        r.keywords = _text(p, 'keywords', 500)
        r.memo = _text(p, 'memo')
        r.lang = p.get('lang') if p.get('lang') in dict(LANGS) else 'ja'
        r.body = _text(p, 'body')
        r.family_message = _text(p, 'family_message', 2000)
        r.internal_note = _text(p, 'internal_note')
        r.ai_drafted = r.ai_drafted or p.get('ai_drafted') == '1'
        if r.is_empty:
            messages.error(request, '何も入っていないので保存しませんでした。')
            return redirect(back)
        if not r.author_id:
            r.author = request.user
        if action == 'review':
            if not r.body.strip():
                messages.error(request, '介護記録の本文が空です。「AIで下書き」を押すか、本文を書いてから確認へ出してください。')
                r.status = STATUS_DRAFT
            else:
                r.status = STATUS_REVIEW
                r.review_note = ''
        elif r.status == STATUS_APPROVED and not self.can_approve():
            # 承認ずみを一般の職員が直したときは確認待ちに戻す
            r.status = STATUS_REVIEW
        elif r.status == STATUS_APPROVED and self.can_approve():
            pass
        else:
            r.status = STATUS_DRAFT if action != 'review' else r.status
        r.save()
        label = {STATUS_DRAFT: '下書きで保存しました', STATUS_REVIEW: '保存して確認待ちにしました', STATUS_APPROVED: '保存しました（承認ずみ）'}[r.status]
        messages.success(request, f'{b.full_name}さんの {jp_date(day)} の介護記録を{label}。')
        nxt = p.get('next')
        if nxt == 'list':
            return redirect(reverse('nursing:records') + f'?d={day.isoformat()}')
        return redirect(back)


class RecordApproveView(CareMixin, View):
    """一覧からワンクリックで承認（確認待ちの画面）"""

    def post(self, request, record_pk):
        if not self.can_approve():
            messages.error(request, '承認はリーダー・管理者が行います。')
            return redirect('nursing:review')
        r = get_object_or_404(CareRecord, pk=record_pk, facility=self.facility)
        if request.POST.get('action') == 'send_back':
            r.send_back(request.POST.get('review_note', ''))
            messages.success(request, f'{r.beneficiary.full_name}さん（{jp_date(r.date)}）の記録を下書きに戻しました。')
        else:
            r.approve(request.user)
            messages.success(request, f'{r.beneficiary.full_name}さん（{jp_date(r.date)}）の記録を承認しました。')
        r.save()
        return redirect(request.POST.get('back') or reverse('nursing:review'))


class PersonRecordsView(CareMixin, View):
    """利用者ごとの介護記録（時系列）"""
    template_name = 'nursing/person_records.html'

    def get(self, request, pk):
        b = self.person_or_404(pk)
        start, end = parse_date(request.GET.get('from')), parse_date(request.GET.get('to'))
        qs = CareRecord.objects.filter(beneficiary=b).select_related('author', 'approved_by')
        if start:
            qs = qs.filter(date__gte=start)
        if end:
            qs = qs.filter(date__lte=end)
        return render(request, self.template_name, {'b': b, 'tab': 'records', 'records': qs[:200], 'start': start, 'end': end,
                                                    'insurance': latest_insurance_of(b), 'today': datetime.date.today(), 'can_approve': self.can_approve()})


class PersonRecordsPdfView(CareMixin, View):
    def get(self, request, pk):
        b = self.person_or_404(pk)
        start, end = parse_date(request.GET.get('from')), parse_date(request.GET.get('to'))
        qs = CareRecord.objects.filter(beneficiary=b).exclude(body='').select_related('author', 'approved_by').order_by('date')
        if start:
            qs = qs.filter(date__gte=start)
        if end:
            qs = qs.filter(date__lte=end)
        return pdf_or_html(request, 'nursing/pdf/records.html', {'b': b, 'facility': self.facility, 'records': qs[:400], 'start': start, 'end': end,
                                                                 'insurance': latest_insurance_of(b), 'back_url': reverse('nursing:person_records', args=[b.pk])},
                           f'介護記録_{b.full_name}')


# ---------------------------------------------------------------- AI（JSON）
class AiRecordDraftView(CareMixin, View):
    """はい／いいえ・キーワード・メモ → 介護記録と家族向けの文の下書き"""

    def post(self, request, pk):
        from . import ai
        b = self.person_or_404(pk)
        data = _json_body(request)
        day = parse_date(data.get('date'), datetime.date.today())
        r = CareRecord(facility=self.facility, beneficiary=b, date=day)
        r.checks = {k: (data.get('checks') or {}).get(k, '') for k, _ in CHECK_ITEMS} if isinstance(data.get('checks'), dict) else {}
        r.keywords = str(data.get('keywords') or '')[:500]
        r.memo = str(data.get('memo') or '')[:TEXT_MAX]
        r.lang = data.get('lang') if data.get('lang') in dict(LANGS) else 'ja'
        vitals = list(VitalsLog.objects.filter(beneficiary=b, date=day))
        for v in vitals:
            v.facility._nursing_setting = self.setting
        if r.is_empty and not vitals:
            return JsonResponse({'error': 'はい／いいえ・キーワード・メモのどれかを入れてから押してください。'}, status=400)
        try:
            out, error = ai.record_draft(r, self.setting, vitals, latest_insurance_of(b))
        except Exception as e:  # noqa: BLE001
            logger.exception('介護記録の下書きでエラー')
            return JsonResponse({'error': f'AIでの作成中にエラーが発生しました: {type(e).__name__}'}, status=500)
        if error is not None:
            return error
        if out is None:
            return JsonResponse({'error': '材料がありません。'}, status=400)
        return JsonResponse(out)


class AiRewriteView(CareMixin, View):
    """入力の文 → 記録に使える日本語（＋元の言語への訳）"""

    def post(self, request):
        from . import ai
        data = _json_body(request)
        text = str(data.get('text') or '').strip()[:8000]
        if not text:
            return JsonResponse({'error': '整える文がありません。話すか書いてから押してください。'}, status=400)
        lang = data.get('lang') if data.get('lang') in dict(LANGS) else 'ja'
        kind = data.get('kind') if data.get('kind') in ('record', 'family', 'incident') else 'record'
        try:
            out, error = ai.rewrite(text, lang, kind, self.setting)
        except Exception as e:  # noqa: BLE001
            logger.exception('整えるでエラー')
            return JsonResponse({'error': f'AIでの作成中にエラーが発生しました: {type(e).__name__}'}, status=500)
        if error is not None:
            return error
        return JsonResponse(out)


class AiIncidentView(CareMixin, View):
    def post(self, request):
        from . import ai
        data = _json_body(request)
        text = str(data.get('text') or '').strip()[:8000]
        if not text:
            return JsonResponse({'error': 'メモがありません。話すか書いてから押してください。'}, status=400)
        lang = data.get('lang') if data.get('lang') in dict(LANGS) else 'ja'
        try:
            out, error = ai.incident_draft(text, lang, self.setting)
        except Exception as e:  # noqa: BLE001
            logger.exception('事故報告の下書きでエラー')
            return JsonResponse({'error': f'AIでの作成中にエラーが発生しました: {type(e).__name__}'}, status=500)
        if error is not None:
            return error
        return JsonResponse(out)


# ---------------------------------------------------------------- 連絡帳（家族向け）
def _primary_guardian(b):
    return Guardian.objects.filter(beneficiary=b).order_by('-is_primary', 'pk').first()


class RenrakuView(CareMixin, View):
    """その日の家族向けメッセージの一覧：LINE で送る・印刷する"""
    template_name = 'nursing/renraku.html'

    def get(self, request):
        f = self.facility
        day = parse_date(request.GET.get('d'), datetime.date.today())
        records = list(CareRecord.objects.filter(facility=f, date=day).exclude(family_message='').select_related('beneficiary'))
        guardians = {}
        for g in Guardian.objects.filter(beneficiary__in=[r.beneficiary for r in records]).order_by('-is_primary', 'pk'):
            guardians.setdefault(g.beneficiary_id, g)
        rows = [{'r': r, 'g': guardians.get(r.beneficiary_id), 'can_line': bool(f.use_line and (g := guardians.get(r.beneficiary_id)) and g.line_linked and g.line_user_id)} for r in records]
        missing = [b for b in self.people() if b.pk not in {r.beneficiary_id for r in records}]
        return render(request, self.template_name, {'day': day, 'day_jp': jp_date(day), 'rows': rows, 'missing': missing, 'today': datetime.date.today(),
                                                    'prev': day - datetime.timedelta(days=1), 'next': day + datetime.timedelta(days=1),
                                                    'line_ready': bool(f.use_line and f.line_channel_access_token)})

    def post(self, request):
        """LINE で送る（1 件ずつ、または承認ずみを全部）"""
        from line_integration.models import LineDeliveryLog
        from line_integration.sending import push_text
        f = self.facility
        day = parse_date(request.POST.get('d'), datetime.date.today())
        back = reverse('nursing:renraku') + f'?d={day.isoformat()}'
        if not (f.use_line and f.line_channel_access_token):
            messages.error(request, 'LINE 連携が設定されていません（施設設定）。')
            return redirect(back)
        ids = [to_int(x, 0) for x in request.POST.getlist('record')]
        qs = CareRecord.objects.filter(facility=f, date=day, pk__in=ids).exclude(family_message='').select_related('beneficiary')
        sent = skipped = 0
        for r in qs:
            g = _primary_guardian(r.beneficiary)
            if not (g and g.line_linked and g.line_user_id):
                skipped += 1
                continue
            text = f'【{f.name}】{r.date.month}月{r.date.day}日の{r.beneficiary.full_name}さんの様子\n\n{r.family_message}'
            ok, reason = push_text(f, g.line_user_id, text)
            LineDeliveryLog.objects.create(facility=f, guardian=g, content=text, is_success=ok, error_message=reason or '')
            if ok:
                r.line_sent_at = timezone.now()
                r.save(update_fields=['line_sent_at', 'updated_at'])
                sent += 1
            else:
                skipped += 1
        if sent:
            messages.success(request, f'LINE で {sent} 件送りました。' + (f'（送れなかったもの {skipped} 件）' if skipped else ''))
        else:
            messages.error(request, '送れませんでした。家族の LINE 連携（利用者情報の家族の欄）を確かめてください。')
        return redirect(back)


class RenrakuPdfView(CareMixin, View):
    """その日の連絡帳（A4 に 2 人分）"""

    def get(self, request):
        f = self.facility
        day = parse_date(request.GET.get('d'), datetime.date.today())
        records = CareRecord.objects.filter(facility=f, date=day).exclude(family_message='').select_related('beneficiary').order_by('beneficiary__last_name_kana')
        only = to_int(request.GET.get('b'), 0)
        if only:
            records = records.filter(beneficiary_id=only)
        vitals = {v.beneficiary_id: v for v in VitalsLog.objects.filter(facility=f, date=day, slot=VitalsLog.SLOT_AM)}
        rows = [{'r': r, 'v': vitals.get(r.beneficiary_id)} for r in records]
        return pdf_or_html(request, 'nursing/pdf/renraku.html', {'facility': f, 'day': day, 'day_jp': jp_date(day), 'rows': rows,
                                                                 'back_url': reverse('nursing:renraku') + f'?d={day.isoformat()}'},
                           f'連絡帳_{day:%Y%m%d}')


# ---------------------------------------------------------------- ヒヤリハット・事故報告
class IncidentsView(CareMixin, View):
    template_name = 'nursing/incidents.html'

    def get(self, request):
        qs = IncidentReport.objects.filter(facility=self.facility).select_related('beneficiary', 'reporter', 'approved_by')
        kind = request.GET.get('kind') if request.GET.get('kind') in dict(IncidentReport.KINDS) else ''
        if kind:
            qs = qs.filter(kind=kind)
        b = to_int(request.GET.get('b'), 0)
        if b:
            qs = qs.filter(beneficiary_id=b)
        status = request.GET.get('status') if request.GET.get('status') in dict(STATUS_CHOICES) else ''
        if status:
            qs = qs.filter(status=status)
        counts = {k: IncidentReport.objects.filter(facility=self.facility, kind=k).count() for k, _ in IncidentReport.KINDS}
        return render(request, self.template_name, {'reports': qs[:300], 'kind': kind, 'kinds': IncidentReport.KINDS, 'status': status, 'status_choices': STATUS_CHOICES,
                                                    'counts': counts, 'can_approve': self.can_approve(), 'b': b, 'people': self.people()})


class IncidentEditView(CareMixin, View):
    template_name = 'nursing/incident_form.html'

    def _report(self, pk):
        return get_object_or_404(IncidentReport, pk=pk, facility=self.facility) if pk else None

    def get(self, request, pk=None):
        rep = self._report(pk)
        now = timezone.localtime()
        return render(request, self.template_name, {
            'rep': rep, 'people': self.people(), 'kinds': IncidentReport.KINDS, 'categories': IncidentReport.CATEGORIES, 'injuries': IncidentReport.INJURIES,
            'report_to': IncidentReport.REPORT_TO, 'langs': LANGS, 'can_approve': self.can_approve(), 'setting': self.setting,
            'now_date': now.date(), 'now_time': now.strftime('%H:%M'), 'b_default': to_int(request.GET.get('b'), 0),
            'ai_enabled': bool(settings.ANTHROPIC_API_KEY),
        })

    def post(self, request, pk=None):
        rep = self._report(pk)
        p = request.POST
        action = p.get('action', 'save')
        if rep is not None:
            conflict = check_conflict(request, rep)
            if conflict:
                messages.error(request, conflict)
                return redirect('nursing:incident_edit', pk=rep.pk)
        if rep is not None and action == 'delete':
            rep.delete()
            messages.success(request, '報告を 1 件消しました。')
            return redirect('nursing:incidents')
        if rep is not None and action in ('approve', 'send_back'):
            if not self.can_approve():
                messages.error(request, '承認はリーダー・管理者が行います。')
                return redirect('nursing:incident_edit', pk=rep.pk)
            if action == 'approve':
                rep.approve(request.user)
                messages.success(request, '報告を承認しました。')
            else:
                rep.send_back(p.get('review_note', ''))
                messages.success(request, '下書きに戻しました。')
            rep.save()
            return redirect('nursing:incident_edit', pk=rep.pk)
        if rep is None:
            rep = IncidentReport(facility=self.facility, reporter=request.user)
        rep.beneficiary = Beneficiary.objects.filter(pk=to_int(p.get('beneficiary'), 0), facility=self.facility).first()
        rep.kind = p.get('kind') if p.get('kind') in dict(IncidentReport.KINDS) else IncidentReport.KIND_NEAR
        rep.category = p.get('category') if p.get('category') in dict(IncidentReport.CATEGORIES) else 'other'
        rep.injury = p.get('injury') if p.get('injury') in dict(IncidentReport.INJURIES) else 'none'
        d = parse_date(p.get('date'), timezone.localdate())
        t = parse_time(p.get('time')) or datetime.time(0, 0)
        rep.occurred_at = timezone.make_aware(datetime.datetime.combine(d, t))
        rep.place = _text(p, 'place', 100)
        rep.memo = _text(p, 'memo')
        rep.lang = p.get('lang') if p.get('lang') in dict(LANGS) else 'ja'
        for k in ('situation', 'cause', 'response', 'prevention'):
            setattr(rep, k, _text(p, k))
        rep.reported_to = [k for k, _ in IncidentReport.REPORT_TO if k in p.getlist('reported_to')]
        if not (rep.situation.strip() or rep.memo.strip()):
            messages.error(request, '発生の状況（またはメモ）を書いてください。')
            return redirect(request.get_full_path())
        if action == 'review':
            rep.status = STATUS_REVIEW
            rep.review_note = ''
        elif rep.status == STATUS_APPROVED and not self.can_approve():
            rep.status = STATUS_REVIEW
        elif rep.status != STATUS_APPROVED:
            rep.status = STATUS_DRAFT
        rep.save()
        messages.success(request, f'{rep.get_kind_display()}の報告を保存しました' + ('（確認待ち）' if rep.status == STATUS_REVIEW else '') + '。')
        return redirect('nursing:incident_edit', pk=rep.pk)


class IncidentPdfView(CareMixin, View):
    def get(self, request, pk):
        rep = get_object_or_404(IncidentReport, pk=pk, facility=self.facility)
        return pdf_or_html(request, 'nursing/pdf/incident.html', {'rep': rep, 'facility': self.facility, 'insurance': latest_insurance_of(rep.beneficiary) if rep.beneficiary else None,
                                                                  'back_url': reverse('nursing:incident_edit', args=[rep.pk])},
                           f'{rep.get_kind_display()}報告書_{rep.occurred_at:%Y%m%d}')


# ---------------------------------------------------------------- 薬の管理
class MedicationsView(CareMixin, View):
    """事業所全体の薬の一覧（更新の期限の近いものを上に）"""
    template_name = 'nursing/medications.html'

    def get(self, request):
        today = datetime.date.today()
        qs = Medication.objects.filter(facility=self.facility).select_related('beneficiary')
        show_inactive = request.GET.get('all') == '1'
        if not show_inactive:
            qs = qs.filter(is_active=True)
        meds = list(qs)
        for m in meds:
            m.due = m.due_state(today)
        alerts = sorted([m for m in meds if m.due], key=lambda m: (m.review_due, m.beneficiary.last_name_kana))
        by_person = {}
        for m in meds:
            by_person.setdefault(m.beneficiary, []).append(m)
        return render(request, self.template_name, {'alerts': alerts, 'by_person': sorted(by_person.items(), key=lambda kv: (kv[0].last_name_kana, kv[0].first_name_kana)),
                                                    'show_inactive': show_inactive, 'today': today, 'people': self.people(), 'timings': Medication.TIMINGS})


class PersonMedicationsView(CareMixin, View):
    """利用者ごとの薬：一覧と追加・編集"""
    template_name = 'nursing/person_medications.html'

    def get(self, request, pk):
        b = self.person_or_404(pk)
        today = datetime.date.today()
        meds = list(Medication.objects.filter(beneficiary=b))
        for m in meds:
            m.due = m.due_state(today)
        edit = Medication.objects.filter(pk=to_int(request.GET.get('edit'), 0), beneficiary=b).first() if request.GET.get('edit') else None
        return render(request, self.template_name, {'b': b, 'tab': 'meds', 'meds': meds, 'edit': edit, 'timings': Medication.TIMINGS, 'today': today,
                                                    'insurance': latest_insurance_of(b)})

    def post(self, request, pk):
        b = self.person_or_404(pk)
        p = request.POST
        back = reverse('nursing:person_meds', args=[b.pk])
        med = Medication.objects.filter(pk=to_int(p.get('med'), 0), beneficiary=b).first() if p.get('med') else None
        if med and p.get('action') == 'delete':
            med.delete()
            messages.success(request, '薬を 1 件消しました。')
            return redirect(back)
        if med and p.get('action') == 'stop':
            med.is_active = False
            med.end_on = med.end_on or datetime.date.today()
            med.save()
            messages.success(request, f'{med.name} を「服用中」から外しました。')
            return redirect(back)
        name = _text(p, 'name', 100)
        if not name:
            messages.error(request, '薬の名前を入れてください。')
            return redirect(back)
        if med is None:
            med = Medication(facility=self.facility, beneficiary=b)
        med.name = name
        med.dose = _text(p, 'dose', 100)
        med.timings = [k for k, _ in Medication.TIMINGS if k in p.getlist('timings')]
        med.timing_note = _text(p, 'timing_note', 100)
        med.purpose = _text(p, 'purpose', 100)
        med.start_on = parse_date(p.get('start_on'))
        med.end_on = parse_date(p.get('end_on'))
        med.prescriber = _text(p, 'prescriber', 100)
        med.pharmacy = _text(p, 'pharmacy', 100)
        med.review_due = parse_date(p.get('review_due'))
        med.notes = _text(p, 'notes', 300)
        med.is_active = p.get('is_active', '1') == '1'
        med.updated_by = request.user
        med.save()
        messages.success(request, f'{b.full_name}さんの薬「{med.name}」を保存しました。')
        return redirect(back)


class PersonMedicationsPdfView(CareMixin, View):
    def get(self, request, pk):
        b = self.person_or_404(pk)
        meds = Medication.objects.filter(beneficiary=b, is_active=True)
        return pdf_or_html(request, 'nursing/pdf/medications.html', {'b': b, 'facility': self.facility, 'meds': meds, 'today': datetime.date.today(),
                                                                     'insurance': latest_insurance_of(b), 'back_url': reverse('nursing:person_meds', args=[b.pk])},
                           f'服薬一覧_{b.full_name}')


# ---------------------------------------------------------------- 通所介護計画書
PLAN_BLANK_ROWS = 4


class PersonPlansView(CareMixin, View):
    template_name = 'nursing/person_plans.html'

    def get(self, request, pk):
        b = self.person_or_404(pk)
        plans = list(CarePlan.objects.filter(beneficiary=b))
        today = datetime.date.today()
        for pl in plans:
            pl.review = pl.review_state(today)
        return render(request, self.template_name, {'b': b, 'tab': 'plans', 'plans': plans, 'today': today, 'insurance': latest_insurance_of(b)})


class PlanEditView(CareMixin, View):
    template_name = 'nursing/plan_form.html'

    def get(self, request, pk, plan_pk=None):
        b = self.person_or_404(pk)
        plan = get_object_or_404(CarePlan, pk=plan_pk, beneficiary=b) if plan_pk else None
        ins = latest_insurance_of(b)
        prev = CarePlan.objects.filter(beneficiary=b).first()
        rows = (plan.row_list() if plan else (prev.row_list() if prev and request.GET.get('copy') else [])) + [{k: '' for k, _ in PLAN_ROW_KEYS}] * PLAN_BLANK_ROWS
        return render(request, self.template_name, {'b': b, 'tab': 'plans', 'plan': plan, 'rows': rows, 'row_keys': PLAN_ROW_KEYS, 'insurance': ins,
                                                    'today': datetime.date.today(), 'prev': prev if not plan else None,
                                                    'care_level_default': (plan.care_level if plan else (ins.care_level if ins else ''))})

    def post(self, request, pk, plan_pk=None):
        b = self.person_or_404(pk)
        plan = get_object_or_404(CarePlan, pk=plan_pk, beneficiary=b) if plan_pk else None
        p = request.POST
        if plan is not None:
            conflict = check_conflict(request, plan)
            if conflict:
                messages.error(request, conflict)
                return redirect('nursing:plan_edit', pk=b.pk, plan_pk=plan.pk)
            if p.get('action') == 'delete':
                plan.delete()
                messages.success(request, '計画書を消しました。')
                return redirect('nursing:person_plans', pk=b.pk)
        else:
            plan = CarePlan(facility=self.facility, beneficiary=b, created_by=request.user)
        plan.created_on = parse_date(p.get('created_on'), datetime.date.today())
        plan.period_from = parse_date(p.get('period_from'))
        plan.period_to = parse_date(p.get('period_to'))
        plan.care_level = _text(p, 'care_level', 10)
        plan.planner = _text(p, 'planner', 50)
        for k in ('wishes_person', 'wishes_family', 'policy', 'long_goal', 'short_goal', 'health_note'):
            setattr(plan, k, _text(p, k))
        rows = []
        for i in range(40):
            if not any(f'row_{i}_{k}' in p for k, _ in PLAN_ROW_KEYS):
                continue
            row = {k: _text(p, f'row_{i}_{k}', 500) for k, _ in PLAN_ROW_KEYS}
            if any(row.values()):
                rows.append(row)
        plan.rows = rows
        plan.review_due = parse_date(p.get('review_due'))
        plan.explained_on = parse_date(p.get('explained_on'))
        plan.agreed_by = _text(p, 'agreed_by', 100)
        plan.save()
        messages.success(request, f'{b.full_name}さんの通所介護計画書を保存しました。')
        if p.get('next') == 'print':
            return redirect(reverse('nursing:plan_print', args=[b.pk, plan.pk]) + '?fmt=html')
        return redirect('nursing:plan_edit', pk=b.pk, plan_pk=plan.pk)


class PlanPdfView(CareMixin, View):
    def get(self, request, pk, plan_pk):
        b = self.person_or_404(pk)
        plan = get_object_or_404(CarePlan, pk=plan_pk, beneficiary=b)
        return pdf_or_html(request, 'nursing/pdf/plan.html', {'b': b, 'plan': plan, 'facility': self.facility, 'rows': plan.row_list(min_rows=4),
                                                              'insurance': latest_insurance_of(b), 'back_url': reverse('nursing:plan_edit', args=[b.pk, plan.pk])},
                           f'通所介護計画書_{b.full_name}')


# ---------------------------------------------------------------- 人員配置
class StaffingView(CareMixin, View):
    """月の人員配置（日ごとの利用者数・職員数と判定）"""
    template_name = 'nursing/staffing.html'

    def get(self, request):
        today = datetime.date.today()
        y, m = to_int(request.GET.get('y'), today.year), to_int(request.GET.get('m'), today.month)
        if not (1 <= m <= 12 and 2000 <= y <= 2100):
            y, m = today.year, today.month
        import calendar
        days = [datetime.date(y, m, d) for d in range(1, calendar.monthrange(y, m)[1] + 1)]
        saved = {s.date: s for s in StaffingDay.objects.filter(facility=self.facility, date__range=(days[0], days[-1]))}
        # 利用者数の初期値：その日の検温板か介護記録のある人数
        from django.db.models import Count
        vit = dict(VitalsLog.objects.filter(facility=self.facility, date__range=(days[0], days[-1]), slot=VitalsLog.SLOT_AM).values_list('date').annotate(n=Count('pk')).values_list('date', 'n'))
        rec = dict(CareRecord.objects.filter(facility=self.facility, date__range=(days[0], days[-1])).values_list('date').annotate(n=Count('pk')).values_list('date', 'n'))
        rows = []
        for d in days:
            s = saved.get(d) or StaffingDay(facility=self.facility, date=d, users=max(vit.get(d, 0), rec.get(d, 0)))
            rows.append({'d': d, 'wd': WEEK_JP[d.weekday()], 's': s, 'judge': s.judge() if s.pk else None, 'auto': d not in saved, 'is_today': d == today})
        prev_y, prev_m = (y, m - 1) if m > 1 else (y - 1, 12)
        next_y, next_m = (y, m + 1) if m < 12 else (y + 1, 1)
        from .staffing import USERS_BASE, USERS_STEP
        return render(request, self.template_name, {'y': y, 'm': m, 'rows': rows, 'prev': (prev_y, prev_m), 'next': (next_y, next_m), 'today': today,
                                                    'week_jp': WEEK_JP, 'base': USERS_BASE, 'step': USERS_STEP, 'setting': self.setting,
                                                    'ng': sum(1 for r in rows if r['judge'] and r['judge']['level'] == 'ng')})

    def post(self, request):
        p = request.POST
        d = parse_date(p.get('date'))
        if d is None:
            return redirect('nursing:staffing')
        s = StaffingDay.objects.filter(facility=self.facility, date=d).first() or StaffingDay(facility=self.facility, date=d)
        s.users = _int(p, 'users', 0, 500) or 0
        s.care_staff = _int(p, 'care_staff', 0, 200) or 0
        s.nurses = _int(p, 'nurses', 0, 50) or 0
        s.counselors = _int(p, 'counselors', 0, 50) or 0
        s.trainers = _int(p, 'trainers', 0, 50) or 0
        s.note = _text(p, 'note', 200)
        s.save()
        j = s.judge()
        (messages.success if j['level'] != 'ng' else messages.warning)(request, f'{jp_date(d)} の人員配置を保存しました：{j["label"]}' + (f'（{"、".join(j["problems"])}）' if j['problems'] else '') + '。')
        return redirect(reverse('nursing:staffing') + f'?y={d.year}&m={d.month}')


# ---------------------------------------------------------------- 送迎表（利用の曜日と送迎の設定から）
WEEKDAY_FIELDS = ['weekday_mon', 'weekday_tue', 'weekday_wed', 'weekday_thu', 'weekday_fri', 'weekday_sat', None]


def transport_rows(facility, day):
    """その日に利用する人（利用の曜日に印のある人）の迎え・送り。車両ごとにまとめる"""
    from transport.models import TransportProfile, Vehicle
    field = WEEKDAY_FIELDS[day.weekday()]
    people = Beneficiary.objects.filter(facility=facility, status=Beneficiary.STATUS_ACTIVE).order_by('last_name_kana', 'first_name_kana')
    if field:
        people = people.filter(**{field: True})
    else:
        people = people.none()
    profiles = {t.beneficiary_id: t for t in TransportProfile.objects.filter(beneficiary__in=people).select_related('default_vehicle', 'default_vehicle__default_driver')}
    vehicles = list(Vehicle.objects.filter(facility=facility, is_active=True).select_related('default_driver'))
    out = {'pickup': [], 'dropoff': [], 'walk': []}
    for b in people:
        t = profiles.get(b.pk)
        if not t or not (t.pickup or t.dropoff):
            out['walk'].append(b)
            continue
        for direction, flag in (('pickup', t.pickup), ('dropoff', t.dropoff)):
            if flag:
                out[direction].append({'b': b, 'time': t.time(direction), 'place': t.place(direction) or b.address, 'vehicle': t.default_vehicle,
                                       'driver': t.default_vehicle.default_driver if t.default_vehicle else None, 'note': t.note})
    for direction in ('pickup', 'dropoff'):
        out[direction].sort(key=lambda r: ((r['vehicle'].order, r['vehicle'].pk) if r['vehicle'] else (999, 0), r['time'] is None, r['time'] or datetime.time.min))
        counts = {}
        for r in out[direction]:
            if r['vehicle']:
                counts.setdefault(r['vehicle'].pk, {'vehicle': r['vehicle'], 'count': 0})['count'] += 1
        for c in counts.values():
            c['over'] = bool(c['vehicle'].capacity and c['count'] > c['vehicle'].capacity)
        out[direction + '_counts'] = sorted(counts.values(), key=lambda c: (c['vehicle'].order, c['vehicle'].pk))
    out['vehicles'] = vehicles
    out['people'] = list(people)
    return out


class TransportDayView(CareMixin, View):
    template_name = 'nursing/transport_day.html'

    def get(self, request):
        day = parse_date(request.GET.get('d'), datetime.date.today())
        board = transport_rows(self.facility, day)
        return render(request, self.template_name, {'day': day, 'day_jp': jp_date(day), 'board': board, 'today': datetime.date.today(),
                                                    'prev': day - datetime.timedelta(days=1), 'next': day + datetime.timedelta(days=1),
                                                    'use_transport': self.facility.use_transport})


class TransportDayPdfView(CareMixin, View):
    def get(self, request):
        day = parse_date(request.GET.get('d'), datetime.date.today())
        return pdf_or_html(request, 'nursing/pdf/transport_day.html', {'facility': self.facility, 'day': day, 'day_jp': jp_date(day), 'board': transport_rows(self.facility, day),
                                                                       'back_url': reverse('nursing:transport_day') + f'?d={day.isoformat()}'},
                           f'送迎表_{day:%Y%m%d}')


# ---------------------------------------------------------------- 資料・加算の検索（RAG）
class DocsView(CareMixin, View):
    template_name = 'nursing/docs.html'

    def get(self, request):
        from ai_assist.models import ReferenceDocument
        docs = ReferenceDocument.objects.filter(facility=self.facility)
        return render(request, self.template_name, {'docs': docs, 'ai_enabled': bool(settings.ANTHROPIC_API_KEY), 'can_manage': request.user.can_manage_settings})


# ---------------------------------------------------------------- 設定
class SettingView(CareMixin, View):
    template_name = 'nursing/settings.html'

    def get(self, request):
        return render(request, self.template_name, {'setting': self.setting, 'chars_choices': RECORD_CHARS_CHOICES, 'back': request.GET.get('back', '')})

    def post(self, request):
        s = self.setting
        p = request.POST
        s.record_chars = _int(p, 'record_chars', 100, 1000) or s.record_chars
        s.family_chars = _int(p, 'family_chars', 50, 600) or s.family_chars
        s.style_note = _text(p, 'style_note', 1000)
        fever = _dec(p, 'fever_temp')
        if fever is not None:
            s.fever_temp = fever
        s.capacity = _int(p, 'capacity', 0, 500) or 0
        s.save()
        messages.success(request, '介護施設の設定を保存しました。')
        back = p.get('back', '')
        return redirect(back if back.startswith('/') else reverse('nursing:settings'))

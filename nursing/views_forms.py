"""
やすらぎの森の紙の様式（2026-11-11 にいただいた用紙）：
- 申し送り（⑥）：日ごとのリーダー・明日の予定・便対応者・2階での決定・レク人数と、日勤帯の申し送り（申し送り者ごとにひとかたまり・時間順）
- 所見・処方処置（①）：利用者ごと。Dr（黒）・リハ（青）・SW（赤）。薬を足す・変えると処方処置へ医師の名前と一緒に写す
- 上申書（みどり会②）：医師ごとの列・検査・採血・他科受診 等（紹介状・食事箋・内服）・その他
- 温度板：検温板の値を 2 週間 1 枚の温度板（T・P のグラフ・血圧・尿・便・食事・水分・注射・内服）に
- 処方箋：薬の Rp・○日分・医師・事業所の住所
"""
import datetime
from collections import OrderedDict
from types import SimpleNamespace

from django.contrib import messages
from django.db.models import Q
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from config.pdf import pdf_or_html
from config.utils import to_int

from .models import (WEEK_JP, CareProfile, CareRecord, DoctorNote, HandoverDay, HandoverEntry, JoushinDay, JoushinItem, Medication,
                     ProgressNote, VitalsExtra, VitalsLog, week_start)
from .views import CareMixin, _int, _text, jp_date, latest_insurance_of, parse_date, parse_time


def now_local():
    return timezone.localtime()


def staff_name(user):
    return (str(user) if user else '')[:50]


def age_on(b, day):
    if not b.date_of_birth:
        return None
    dob = b.date_of_birth
    return day.year - dob.year - ((day.month, day.day) < (dob.month, dob.day))


def wareki(d):
    """令和の年（2019-05-01 から）"""
    return d.year - 2018 if d >= datetime.date(2019, 5, 1) else None


# ---------------------------------------------------------------- 薬 → 処方処置
def copy_medication_to_note(med, user):
    """新しい処方・中身の変わった処方を、所見・処方処置（①）の処方処置へ写す。同じ日・同じ薬の行があれば書き直す"""
    day = med.start_on or datetime.date.today()
    head = {'inj': '注射', 'ext': '外用'}.get(med.route, 'Rp')
    text = f'{head}）{med.rx_line()}'
    if med.check_on:
        text += f'　→ {med.check_on.month}/{med.check_on.day} {med.check_doctor or med.prescriber or ""} 確認'.rstrip()
    doctor = (med.prescriber or '')[:50]
    note = DoctorNote.objects.filter(medication=med, date=day).first()
    if note:
        note.treatment, note.doctor = text, doctor
        note.save(update_fields=['treatment', 'doctor'])
        return note
    return DoctorNote.objects.create(facility=med.facility, beneficiary=med.beneficiary, date=day, writer='dr', doctor=doctor,
                                     treatment=text, medication=med, created_by=user)


# ---------------------------------------------------------------- 申し送り（⑥）
def handover_groups(entries):
    """申し送り者ごとにひとかたまり（最初に申し送った時刻の順）・中は時間順"""
    groups = OrderedDict()
    for e in sorted(entries, key=lambda x: (x.time, x.pk or 0)):
        groups.setdefault(e.giver, []).append(e)
    return [{'giver': g, 'entries': es} for g, es in groups.items()]


DAY_FROM, DAY_TO = datetime.time(8, 0), datetime.time(17, 0)


def shift_of(t):
    return 'day' if DAY_FROM <= t < DAY_TO else 'night'


def handover_entries(facility, day, shift=None):
    """その日の申し送り：手で足した申し送り＋経過記録で「申し送りに出す」に印を付けたもの（担当者を申し送り者に）"""
    qs = HandoverEntry.objects.filter(facility=facility, date=day).select_related('beneficiary')
    if shift:
        qs = qs.filter(shift=shift)
    out = list(qs)
    for e in out:
        e.from_record = False
    for n in ProgressNote.objects.filter(facility=facility, date=day, handover=True).select_related('beneficiary'):
        if shift and shift_of(n.time) != shift:
            continue
        out.append(SimpleNamespace(pk=None, date=n.date, time=n.time, giver=n.author_name, beneficiary=n.beneficiary, content=n.body, important=n.important,
                                   shift=shift_of(n.time), from_record=True, role=n.get_role_display(), note_pk=n.pk))
    return out


class HandoverView(CareMixin, View):
    template_name = 'nursing/handover.html'

    def get(self, request):
        day = parse_date(request.GET.get('d'), datetime.date.today())
        shift = request.GET.get('shift') if request.GET.get('shift') in dict(HandoverEntry.SHIFTS) else ''
        hd = HandoverDay.objects.filter(facility=self.facility, date=day).first() or HandoverDay(facility=self.facility, date=day)
        entries = handover_entries(self.facility, day, shift or None)
        return render(request, self.template_name, {
            'day': day, 'day_jp': jp_date(day), 'hd': hd, 'groups': handover_groups(entries), 'count': len(entries), 'people': self.people(),
            'important': sorted([e for e in entries if e.important], key=lambda e: e.time), 'shift': shift, 'shifts': HandoverEntry.SHIFTS,
            'default_shift': shift or shift_of(now_local().time()),
            'now_time': now_local().strftime('%H:%M'), 'me': staff_name(request.user), 'today': datetime.date.today(),
            'prev': day - datetime.timedelta(days=1), 'next': day + datetime.timedelta(days=1),
            'givers': sorted({e.giver for e in HandoverEntry.objects.filter(facility=self.facility, date__gte=day - datetime.timedelta(days=30))
                              .only('giver')}),
        })

    def post(self, request):
        p = request.POST
        day = parse_date(p.get('d'), datetime.date.today())
        back = reverse('nursing:handover') + f'?d={day.isoformat()}'
        action = p.get('action', '')
        if action == 'day':
            hd, _ = HandoverDay.objects.get_or_create(facility=self.facility, date=day)
            hd.leader = _text(p, 'leader', 50)
            hd.tomorrow = _text(p, 'tomorrow', 2000)
            hd.telemin = _text(p, 'telemin', 200)
            hd.laxative = _text(p, 'laxative', 200)
            hd.decisions = _text(p, 'decisions', 2000)
            hd.rec_am = _int(p, 'rec_am', 0, 999)
            hd.rec_pm = _int(p, 'rec_pm', 0, 999)
            hd.updated_by = request.user
            hd.save()
            messages.success(request, f'{jp_date(day)} の申し送りの欄（リーダー・明日の予定など）を保存しました。')
            return redirect(back)
        if action == 'delete':
            e = HandoverEntry.objects.filter(pk=to_int(p.get('entry'), 0), facility=self.facility).first()
            if e:
                e.delete()
                messages.success(request, '申し送りを 1 件消しました。')
            return redirect(back)
        # 申し送りを足す（直すときは entry を付ける）
        content = _text(p, 'content', 2000)
        if not content:
            messages.error(request, '申し送りの内容を入れてください。')
            return redirect(back)
        e = HandoverEntry.objects.filter(pk=to_int(p.get('entry'), 0), facility=self.facility).first() if p.get('entry') else None
        e = e or HandoverEntry(facility=self.facility, date=day, created_by=request.user)
        e.time = parse_time(p.get('time')) or now_local().time().replace(second=0, microsecond=0)
        e.giver = _text(p, 'giver', 50) or staff_name(request.user)
        e.beneficiary = self.people().filter(pk=to_int(p.get('b'), 0)).first()
        e.content = content
        e.shift = p.get('shift') if p.get('shift') in dict(HandoverEntry.SHIFTS) else shift_of(e.time)
        e.important = p.get('important') == '1'
        e.save()
        messages.success(request, f'{e.giver}さんの申し送り（{e.time:%H:%M}）を保存しました。')
        return redirect(back)


class HandoverPdfView(CareMixin, View):
    def get(self, request):
        day = parse_date(request.GET.get('d'), datetime.date.today())
        hd = HandoverDay.objects.filter(facility=self.facility, date=day).first()
        shift = request.GET.get('shift') if request.GET.get('shift') in dict(HandoverEntry.SHIFTS) else ''
        entries = handover_entries(self.facility, day, shift or None)
        return pdf_or_html(request, 'nursing/pdf/handover.html', {
            'facility': self.facility, 'day': day, 'wareki': wareki(day), 'week': WEEK_JP[day.weekday()], 'hd': hd, 'groups': handover_groups(entries),
            'important': sorted([e for e in entries if e.important], key=lambda e: e.time),
            'shift_label': {'day': '日勤帯', 'night': '夜勤帯'}.get(shift, '日勤帯・夜勤帯' if any(e.shift == 'night' for e in entries) else '日勤帯'),
            'back_url': reverse('nursing:handover') + f'?d={day.isoformat()}'}, f'申し送り_{day:%Y%m%d}')


# ---------------------------------------------------------------- 所見・処方処置（①）
class PersonNotesView(CareMixin, View):
    template_name = 'nursing/person_notes.html'

    def get(self, request, pk):
        b = self.person_or_404(pk)
        notes = DoctorNote.objects.filter(beneficiary=b).select_related('medication').order_by('-date', '-pk')[:300]
        edit = DoctorNote.objects.filter(pk=to_int(request.GET.get('edit'), 0), beneficiary=b).first() if request.GET.get('edit') else None
        return render(request, self.template_name, {'b': b, 'tab': 'notes', 'notes': notes, 'edit': edit, 'writers': DoctorNote.WRITERS,
                                                    'doctors': self.setting.doctor_list(), 'today': datetime.date.today(),
                                                    'insurance': latest_insurance_of(b)})

    def post(self, request, pk):
        b = self.person_or_404(pk)
        p = request.POST
        back = reverse('nursing:person_notes', args=[b.pk])
        note = DoctorNote.objects.filter(pk=to_int(p.get('note'), 0), beneficiary=b).first() if p.get('note') else None
        if note and p.get('action') == 'delete':
            note.delete()
            messages.success(request, '所見・処方処置を 1 件消しました。')
            return redirect(back)
        finding, treatment = _text(p, 'finding', 3000), _text(p, 'treatment', 3000)
        if not (finding or treatment):
            messages.error(request, '所見か処方処置のどちらかを入れてください。')
            return redirect(back)
        note = note or DoctorNote(facility=self.facility, beneficiary=b, created_by=request.user)
        note.date = parse_date(p.get('date'), datetime.date.today())
        note.writer = p.get('writer') if p.get('writer') in dict(DoctorNote.WRITERS) else 'dr'
        note.doctor = _text(p, 'doctor', 50)
        note.finding, note.treatment = finding, treatment
        note.save()
        messages.success(request, f'{b.full_name}さんの所見・処方処置（{note.date:%-m/%-d}）を保存しました。')
        return redirect(back)


class PersonNotesPdfView(CareMixin, View):
    def get(self, request, pk):
        b = self.person_or_404(pk)
        qs = DoctorNote.objects.filter(beneficiary=b).order_by('date', 'pk')
        start, end = parse_date(request.GET.get('from')), parse_date(request.GET.get('to'))
        if start:
            qs = qs.filter(date__gte=start)
        if end:
            qs = qs.filter(date__lte=end)
        notes = list(qs[:600])
        rows = len(notes) + sum(max(len((n.finding or '').splitlines()), len((n.treatment or '').splitlines())) - 1 for n in notes)
        return pdf_or_html(request, 'nursing/pdf/doctor_notes.html', {
            'facility': self.facility, 'b': b, 'notes': notes, 'blank_rows': range(max(0, 36 - rows)),
            'back_url': reverse('nursing:person_notes', args=[b.pk])}, f'所見処方処置_{b.full_name}')


# ---------------------------------------------------------------- 上申書（みどり会②）
def joushin_columns(setting, items):
    """医師の列：設定の順 → その日に書かれた医師（設定に無い名前）"""
    cols = setting.doctor_list()
    for it in items:
        if it.kind == 'dr' and it.doctor and it.doctor not in cols:
            cols.append(it.doctor)
    if not cols:
        cols = ['（医師）']
    out = []
    for c in cols:
        out.append({'doctor': c, 'items': [it for it in items if it.kind == 'dr' and (it.doctor or '（医師）') == c]})
    return out


class JoushinView(CareMixin, View):
    template_name = 'nursing/joushin.html'

    def get(self, request):
        day = parse_date(request.GET.get('d'), datetime.date.today())
        items = list(JoushinItem.objects.filter(facility=self.facility, date=day).select_related('beneficiary'))
        jd = JoushinDay.objects.filter(facility=self.facility, date=day).first()
        return render(request, self.template_name, {
            'day': day, 'day_jp': jp_date(day), 'columns': joushin_columns(self.setting, items), 'exams': [i for i in items if i.kind == 'exam'],
            'others': [i for i in items if i.kind == 'other'],
            'other': jd.other if jd else '', 'people': self.people(), 'doctors': self.setting.doctor_list(), 'today': datetime.date.today(),
            'prev': day - datetime.timedelta(days=1), 'next': day + datetime.timedelta(days=1), 'can_manage': request.user.can_manage_settings,
        })

    def post(self, request):
        p = request.POST
        day = parse_date(p.get('d'), datetime.date.today())
        back = reverse('nursing:joushin') + f'?d={day.isoformat()}'
        action = p.get('action', '')
        if action == 'other':
            jd, _ = JoushinDay.objects.get_or_create(facility=self.facility, date=day)
            jd.other = _text(p, 'other', 3000)
            jd.save()
            messages.success(request, '【その他】を保存しました。')
            return redirect(back)
        if action == 'delete':
            JoushinItem.objects.filter(pk=to_int(p.get('item'), 0), facility=self.facility).delete()
            messages.success(request, '上申書の行を 1 件消しました。')
            return redirect(back)
        content = _text(p, 'content', 2000)
        if not content:
            messages.error(request, '内容を入れてください。')
            return redirect(back)
        kind = p.get('kind') if p.get('kind') in ('exam', 'other') else 'dr'
        JoushinItem.objects.create(facility=self.facility, date=day, kind=kind, doctor=_text(p, 'doctor', 50) if kind == 'dr' else '',
                                   beneficiary=self.people().filter(pk=to_int(p.get('b'), 0)).first(), content=content,
                                   referral=p.get('referral') == '1', meal_slip=p.get('meal_slip') == '1', oral=p.get('oral') == '1',
                                   created_by=request.user)
        messages.success(request, '上申書に足しました。')
        return redirect(back)


class JoushinPdfView(CareMixin, View):
    def get(self, request):
        day = parse_date(request.GET.get('d'), datetime.date.today())
        items = list(JoushinItem.objects.filter(facility=self.facility, date=day).select_related('beneficiary'))
        exams = [i for i in items if i.kind == 'exam']
        jd = JoushinDay.objects.filter(facility=self.facility, date=day).first()
        return pdf_or_html(request, 'nursing/pdf/joushin.html', {
            'facility': self.facility, 'day': day, 'week': WEEK_JP[day.weekday()], 'columns': joushin_columns(self.setting, items),
            'exams': exams, 'exam_blank': range(max(0, 8 - len(exams))), 'other': jd.other if jd else '',
            'others': [i for i in items if i.kind == 'other'],
            'back_url': reverse('nursing:joushin') + f'?d={day.isoformat()}'}, f'上申書_{day:%Y%m%d}')


# ---------------------------------------------------------------- 温度板（2 週間 1 枚）
TB_DAYS = 14
TB_T_MIN, TB_T_MAX = 34, 41
# 紙の温度板の目盛り（T と P を同じ線に）：34-50・35-60・36-70・37-80・38-90・39-100・40-120・41-140
TB_SCALE = [(34, 50), (35, 60), (36, 70), (37, 80), (38, 90), (39, 100), (40, 120), (41, 140)]


def pulse_as_temp(p):
    """脈拍 → 同じ高さの体温の目盛り（目盛りの間はまっすぐにつなぐ）"""
    if p <= TB_SCALE[0][1]:
        return TB_SCALE[0][0] - (TB_SCALE[0][1] - p) / 10
    for (t0, p0), (t1, p1) in zip(TB_SCALE, TB_SCALE[1:]):
        if p <= p1:
            return t0 + (p - p0) / (p1 - p0) * (t1 - t0)
    return TB_SCALE[-1][0] + (p - TB_SCALE[-1][1]) / 20
TB_SLOT_HOURS = {'am': 9.0, 'pm': 16.0}


def temp_board(b, start, setting):
    """温度板の中身：日ごとの朝・夕・追加の検温、血圧、尿・便、食事、水分、注射、内服（その日に始まった処方）"""
    days = [start + datetime.timedelta(days=i) for i in range(TB_DAYS)]
    end = days[-1]
    logs = {(v.date, v.slot): v for v in VitalsLog.objects.filter(beneficiary=b, date__range=(start, end))}
    extras = list(VitalsExtra.objects.filter(beneficiary=b, date__range=(start, end)))
    meds = list(Medication.objects.filter(beneficiary=b).filter(Q(start_on__range=(start, end)) | Q(start_on__lt=start, is_active=True)
                                                                 | Q(start_on__isnull=True, is_active=True)).order_by('start_on', 'pk'))
    out = []
    for d in days:
        am, pm = logs.get((d, 'am')), logs.get((d, 'pm'))
        rows = [x for x in (am, pm) if x]
        water = sum(x.water_ml or 0 for x in rows) or None
        urine = sum(x.urine or 0 for x in rows) if any(x.urine is not None for x in rows) else None
        stool = sum(x.stool or 0 for x in rows) if any(x.stool is not None for x in rows) else None
        started = [m for m in meds if m.start_on == d] if d != start else [m for m in meds if (m.start_on or start) <= start]
        out.append({'date': d, 'week': WEEK_JP[d.weekday()], 'am': am, 'pm': pm, 'extras': [e for e in extras if e.date == d],
                    'water': water, 'urine': urine, 'stool': stool,
                    'inj': [m for m in started if m.route == 'inj'], 'oral': [m for m in started if m.route != 'inj'],
                    'carried': d == start})
    return out


TB_PAD = 24   # グラフの上下の余白（svg の単位。H=300）


def tb_y(t, H=300):
    return H - TB_PAD - (float(t) - TB_T_MIN) / (TB_T_MAX - TB_T_MIN) * (H - 2 * TB_PAD)


def temp_board_svg(days, fever):
    """T（体温・●）と P（脈拍・○）の折れ線。1 日を朝・夕の 2 マス、追加の検温は時刻の位置"""
    W, H = 1120, 300
    n = len(days)
    cw = W / n
    t_y = lambda t: tb_y(t, H)   # noqa: E731
    p_y = lambda p: t_y(pulse_as_temp(p))                                    # noqa: E731
    pts_t, pts_p = [], []
    for i, d in enumerate(days):
        cand = [(TB_SLOT_HOURS['am'], d['am']), (TB_SLOT_HOURS['pm'], d['pm'])] + [(e.time.hour + e.time.minute / 60, e) for e in d['extras']]
        for hour, v in sorted((c for c in cand if c[1]), key=lambda c: c[0]):
            x = i * cw + (hour - 6) / 16 * cw  # 6 時〜22 時を 1 日の幅に
            x = min(max(x, i * cw + 2), (i + 1) * cw - 2)
            extra = isinstance(v, VitalsExtra)
            if v.temp is not None:
                pts_t.append((x, min(H, max(0, t_y(v.temp))), extra, float(v.temp) >= float(fever)))
            if v.pulse:
                pts_p.append((x, min(H, max(0, p_y(v.pulse))), extra))
    lines = []
    for k in range(TB_T_MIN, TB_T_MAX + 1):
        y = t_y(k)
        lines.append(f'<line x1="0" y1="{y:.1f}" x2="{W}" y2="{y:.1f}" stroke="#999" stroke-width="0.8"/>')
        if k < TB_T_MAX:
            for j in range(1, 5):
                yy = y - j * (H / (TB_T_MAX - TB_T_MIN)) / 5
                lines.append(f'<line x1="0" y1="{yy:.1f}" x2="{W}" y2="{yy:.1f}" stroke="#ddd" stroke-width="0.5"/>')
    fy = t_y(fever)
    lines.append(f'<line x1="0" y1="{fy:.1f}" x2="{W}" y2="{fy:.1f}" stroke="#d55" stroke-width="0.8" stroke-dasharray="4 3"/>')
    for i in range(n + 1):
        lines.append(f'<line x1="{i * cw:.1f}" y1="0" x2="{i * cw:.1f}" y2="{H}" stroke="#555" stroke-width="{1.2 if i else 0}"/>')
        if i < n:
            lines.append(f'<line x1="{i * cw + cw / 2:.1f}" y1="0" x2="{i * cw + cw / 2:.1f}" y2="{H}" stroke="#ccc" stroke-width="0.5"/>')
    if len(pts_p) > 1:
        lines.append('<polyline fill="none" stroke="#c0392b" stroke-width="1.3" points="' + ' '.join(f'{x:.1f},{y:.1f}' for x, y, _ in pts_p) + '"/>')
    for x, y, extra in pts_p:
        lines.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="#fff" stroke="#c0392b" stroke-width="1.2"/>')
    if len(pts_t) > 1:
        lines.append('<polyline fill="none" stroke="#1f4e8c" stroke-width="1.5" points="' + ' '.join(f'{x:.1f},{y:.1f}' for x, y, _, _ in pts_t) + '"/>')
    for x, y, extra, hot in pts_t:
        color = '#c0392b' if hot else '#1f4e8c'
        lines.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.4" fill="{"#fff" if extra else color}" stroke="{color}" stroke-width="1.4"/>')
    return f'<svg viewBox="0 0 {W} {H}" width="100%" preserveAspectRatio="none" style="display:block;height:62mm;">{"".join(lines)}</svg>'


class TempBoardPdfView(CareMixin, View):
    def get(self, request, pk):
        b = self.person_or_404(pk)
        start = week_start(parse_date(request.GET.get('start'), datetime.date.today()))
        days = temp_board(b, start, self.setting)
        prof = CareProfile.objects.filter(beneficiary=b).first()
        scale = [{'t': t, 'p': p, 'top': round(tb_y(t) / 300 * 100, 2)} for t, p in reversed(TB_SCALE)]
        return pdf_or_html(request, 'nursing/pdf/temp_board.html', {
            'facility': self.facility, 'b': b, 'age': age_on(b, start), 'room': prof.room if prof else '', 'days': days,
            'svg': temp_board_svg(days, self.setting.fever_temp), 'scale': scale,
            'back_url': reverse('nursing:vitals_week', args=[b.pk]) + f'?w={start.isoformat()}'}, f'温度板_{b.full_name}_{start:%Y%m%d}')


class PersonRoomView(CareMixin, View):
    """温度板の「部屋」を保存する（検温板の画面から）"""

    def post(self, request, pk):
        b = self.person_or_404(pk)
        prof = CareProfile.of(b)
        prof.room = _text(request.POST, 'room', 20)
        prof.save()
        messages.success(request, f'{b.full_name}さんの部屋を「{prof.room or "（空）"}」にしました。')
        back = request.POST.get('back', '')
        return redirect(back if back.startswith('/') and not back.startswith('//') else reverse('nursing:vitals_week', args=[b.pk]))


# ---------------------------------------------------------------- 処方箋
class PrescriptionPdfView(CareMixin, View):
    def get(self, request, pk):
        b = self.person_or_404(pk)
        day = parse_date(request.GET.get('d'), datetime.date.today())
        ids = [to_int(x, 0) for x in request.GET.getlist('ids')]
        meds = Medication.objects.filter(beneficiary=b)
        meds = list(meds.filter(pk__in=ids) if any(ids) else meds.filter(is_active=True))
        doctors = sorted({m.prescriber for m in meds if m.prescriber})
        days = sorted({m.days for m in meds if m.days})
        return pdf_or_html(request, 'nursing/pdf/prescription.html', {
            'facility': self.facility, 'b': b, 'day': day, 'wareki': wareki(day), 'age': age_on(b, day), 'meds': meds,
            'doctor': '・'.join(doctors), 'days': days[0] if len(days) == 1 else None, 'label': self.setting.rx_label or '入所',
            'back_url': reverse('nursing:person_meds', args=[b.pk])}, f'処方箋_{b.full_name}_{day:%Y%m%d}')


# ---------------------------------------------------------------- 経過記録（職種横断の時系列）
ROLE_OF_WRITER = {'dr': 'doctor', 'reha': 'reha', 'sw': 'sw', 'ns': 'nurse'}
ROLE_LABELS = dict(ProgressNote.ROLES)


def timeline(b, start, end, roles=None):
    """経過記録・所見／処方処置・介護記録を、出来事の時刻の順に 1 本に（時刻の無いものはその日の書いた時刻）"""
    items = []
    for n in ProgressNote.objects.filter(beneficiary=b, date__range=(start, end)):
        items.append({'date': n.date, 'time': n.time, 'role': n.role, 'author': n.author_name, 'body': n.body, 'kind': 'note',
                      'handover': n.handover, 'important': n.important, 'obj': n})
    for d in DoctorNote.objects.filter(beneficiary=b, date__range=(start, end)):
        body = '\n'.join(x for x in ((f'所見：{d.finding}' if d.finding else ''), (f'処方処置：{d.treatment}' if d.treatment else '')) if x)
        items.append({'date': d.date, 'time': timezone.localtime(d.created_at).time() if d.created_at else None, 'role': ROLE_OF_WRITER.get(d.writer, 'doctor'),
                      'author': d.doctor or (str(d.created_by) if d.created_by else ''), 'body': body, 'kind': 'doctor', 'handover': False, 'important': False, 'obj': d})
    for r in CareRecord.objects.filter(beneficiary=b, date__range=(start, end)).exclude(body='').select_related('author'):
        items.append({'date': r.date, 'time': r.time or (timezone.localtime(r.created_at).time() if r.created_at else None), 'role': 'care',
                      'author': str(r.author) if r.author else '', 'body': r.body, 'kind': 'record', 'handover': False, 'important': False, 'obj': r})
    if roles:
        items = [x for x in items if x['role'] in roles]
    for x in items:
        x['role_label'] = ROLE_LABELS.get(x['role'], '')
    items.sort(key=lambda x: (x['date'], x['time'] or datetime.time.min))
    return items


def timeline_range(request, default_days=7):
    today = datetime.date.today()
    end = parse_date(request.GET.get('to'), today)
    start = parse_date(request.GET.get('from'), end - datetime.timedelta(days=default_days - 1))
    if start > end:
        start, end = end, start
    if (end - start).days > 366:
        start = end - datetime.timedelta(days=366)
    return start, end


class PersonTimelineView(CareMixin, View):
    template_name = 'nursing/person_timeline.html'

    def get(self, request, pk):
        b = self.person_or_404(pk)
        start, end = timeline_range(request)
        roles = [r for r in request.GET.getlist('role') if r in ROLE_LABELS]
        edit = ProgressNote.objects.filter(pk=to_int(request.GET.get('edit'), 0), beneficiary=b).first() if request.GET.get('edit') else None
        return render(request, self.template_name, {
            'b': b, 'tab': 'timeline', 'items': timeline(b, start, end, roles), 'start': start, 'end': end, 'roles': ProgressNote.ROLES, 'sel_roles': roles,
            'edit': edit, 'today': datetime.date.today(), 'now_time': now_local().strftime('%H:%M'), 'me': staff_name(request.user),
            'my_role': request.session.get('nursing_role', 'care'), 'insurance': latest_insurance_of(b),
            'query': request.GET.urlencode()})

    def post(self, request, pk):
        b = self.person_or_404(pk)
        p = request.POST
        back = reverse('nursing:person_timeline', args=[b.pk]) + (('?' + p.get('q')) if p.get('q') else '')
        note = ProgressNote.objects.filter(pk=to_int(p.get('note'), 0), beneficiary=b).first() if p.get('note') else None
        if note and p.get('action') == 'delete':
            note.delete()
            messages.success(request, '経過記録を 1 件消しました。')
            return redirect(back)
        body = _text(p, 'body', 4000)
        if not body:
            messages.error(request, '記録を入れてください。')
            return redirect(back)
        note = note or ProgressNote(facility=self.facility, beneficiary=b, created_by=request.user)
        note.date = parse_date(p.get('date'), datetime.date.today())
        note.time = parse_time(p.get('time')) or now_local().time().replace(second=0, microsecond=0)
        note.role = p.get('role') if p.get('role') in ROLE_LABELS else 'care'
        note.author_name = _text(p, 'author', 50) or staff_name(request.user)
        note.body = body
        note.handover = p.get('handover') == '1'
        note.important = p.get('important') == '1'
        note.save()
        request.session['nursing_role'] = note.role     # 次に書くときの職種の既定
        messages.success(request, f'{b.full_name}さんの経過記録（{note.date.month}/{note.date.day} {note.time:%H:%M}・{note.get_role_display()}）を保存しました。'
                         + ('申し送りにも出ます。' if note.handover else ''))
        return redirect(back)


class PersonTimelinePdfView(CareMixin, View):
    def get(self, request, pk):
        b = self.person_or_404(pk)
        start, end = timeline_range(request)
        roles = [r for r in request.GET.getlist('role') if r in ROLE_LABELS]
        return pdf_or_html(request, 'nursing/pdf/timeline.html', {
            'facility': self.facility, 'b': b, 'items': timeline(b, start, end, roles), 'start': start, 'end': end,
            'role_names': '・'.join(ROLE_LABELS[r] for r in roles), 'roles': ProgressNote.ROLES,
            'back_url': reverse('nursing:person_timeline', args=[b.pk])}, f'経過記録_{b.full_name}')


class TimelineDayView(CareMixin, View):
    """全員の経過記録（1 日）：朝に前の日の様子をまとめて見る"""
    template_name = 'nursing/timeline_day.html'

    def get(self, request):
        day = parse_date(request.GET.get('d'), datetime.date.today() - datetime.timedelta(days=1))
        notes = list(ProgressNote.objects.filter(facility=self.facility, date=day).select_related('beneficiary').order_by('time', 'pk'))
        return render(request, self.template_name, {'day': day, 'day_jp': jp_date(day), 'notes': notes, 'roles': ProgressNote.ROLES,
                                                    'prev': day - datetime.timedelta(days=1), 'next': day + datetime.timedelta(days=1),
                                                    'important': [n for n in notes if n.important]})


# ---------------------------------------------------------------- 上申書（利用者ごと）：日付ごとに用紙と同じ形でぶら下げる（2026-11-11 上申書の手書きメモ）
def joushin_days(setting, items, others_by_day=None):
    """利用者の上申書の行 → 日付ごと（新しい順）に {date, week, columns, exams, others}"""
    by_day = OrderedDict()
    for it in sorted(items, key=lambda i: (i.date, i.pk), reverse=True):
        by_day.setdefault(it.date, []).append(it)
    out = []
    for d, its in by_day.items():
        its = sorted(its, key=lambda i: i.pk)
        out.append({'date': d, 'week': WEEK_JP[d.weekday()], 'columns': joushin_columns(setting, its),
                    'exams': [i for i in its if i.kind == 'exam'], 'others': [i for i in its if i.kind == 'other']})
    return out


class PersonJoushinView(CareMixin, View):
    template_name = 'nursing/person_joushin.html'

    def get(self, request, pk):
        b = self.person_or_404(pk)
        start, end = timeline_range(request, default_days=90)
        items = list(JoushinItem.objects.filter(beneficiary=b, date__range=(start, end)))
        return render(request, self.template_name, {
            'b': b, 'tab': 'joushin', 'days': joushin_days(self.setting, items), 'start': start, 'end': end, 'kinds': JoushinItem.KINDS,
            'doctors': self.setting.doctor_list(), 'today': datetime.date.today(), 'insurance': latest_insurance_of(b), 'query': request.GET.urlencode()})

    def post(self, request, pk):
        b = self.person_or_404(pk)
        p = request.POST
        back = reverse('nursing:person_joushin', args=[b.pk]) + (('?' + p.get('q')) if p.get('q') else '')
        if p.get('action') == 'delete':
            JoushinItem.objects.filter(pk=to_int(p.get('item'), 0), beneficiary=b).delete()
            messages.success(request, '上申書の行を 1 件消しました。')
            return redirect(back)
        content = _text(p, 'content', 2000)
        if not content:
            messages.error(request, '内容を入れてください。')
            return redirect(back)
        kind = p.get('kind') if p.get('kind') in ('exam', 'other') else 'dr'
        day = parse_date(p.get('date'), datetime.date.today())
        JoushinItem.objects.create(facility=self.facility, date=day, kind=kind, doctor=_text(p, 'doctor', 50) if kind == 'dr' else '',
                                   beneficiary=b, content=content, referral=p.get('referral') == '1', meal_slip=p.get('meal_slip') == '1',
                                   oral=p.get('oral') == '1', created_by=request.user)
        messages.success(request, f'{b.full_name}さんの上申書（{day.month}/{day.day}）に足しました。その日の上申書（全員）にも出ます。')
        return redirect(back)


class PersonJoushinPdfView(CareMixin, View):
    def get(self, request, pk):
        b = self.person_or_404(pk)
        start, end = timeline_range(request, default_days=90)
        items = list(JoushinItem.objects.filter(beneficiary=b, date__range=(start, end)))
        days = joushin_days(self.setting, items)
        days.reverse()      # 印刷は古い日から
        return pdf_or_html(request, 'nursing/pdf/person_joushin.html', {
            'facility': self.facility, 'b': b, 'days': days, 'start': start, 'end': end,
            'back_url': reverse('nursing:person_joushin', args=[b.pk])}, f'上申書_{b.full_name}')

"""毎日の運営の画面：クラスの活動（週間の計画・当日の記録・療育記録への写し）と健康の記録（一覧入力・子ども別・引き渡しカード）"""
import datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from beneficiaries.models import Beneficiary
from config.concurrency import check_conflict
from config.pdf import pdf_or_html
from config.utils import to_int

from .models import WEEK_JP, ClassGroup, GroupSession, HealthLog, HealthProfile

ACTIVITY_MAX = 5


class DailyEnabledMixin(LoginRequiredMixin):
    """施設設定で毎日の運営を使わない事業所はホームへ戻す"""

    def dispatch(self, request, *args, **kwargs):
        facility = getattr(request.user, 'facility', None)
        if request.user.is_authenticated and not (facility is not None and facility.use_daily_ops):
            messages.info(request, 'この事業所では「毎日の運営」を使わない設定になっています（施設設定の「使う機能」で変えられます）。')
            return redirect('facilities:dashboard')
        return super().dispatch(request, *args, **kwargs)


def parse_day(value, default=None):
    try:
        return datetime.date.fromisoformat(value or '')
    except ValueError:
        return default or datetime.date.today()


def parse_time(value):
    value = (value or '').strip()
    if not value:
        return None
    try:
        h, m = value.split(':')[:2]
        return datetime.time(int(h), int(m))
    except (ValueError, TypeError):
        return None


def parse_temp(value):
    value = (value or '').strip().replace('℃', '').replace('，', '.').replace(',', '.')
    if not value:
        return None
    try:
        t = Decimal(value).quantize(Decimal('0.1'))
    except InvalidOperation:
        return None
    return t if Decimal('30') <= t <= Decimal('43') else None


def monday_of(day):
    return day - datetime.timedelta(days=day.weekday())


def active_children(facility):
    return Beneficiary.objects.filter(facility=facility, status=Beneficiary.STATUS_ACTIVE) \
        .order_by('last_name_kana', 'first_name_kana', 'pk')


# ------------------------------------------------------------------ クラス
class GroupsView(DailyEnabledMixin, View):
    """クラスの一覧と登録（名前・色・曜日・時刻・メンバー）"""
    template_name = 'daily/groups.html'

    def get(self, request):
        facility = request.user.facility
        groups = list(ClassGroup.objects.filter(facility=facility).prefetch_related('members'))
        today = datetime.date.today()
        todays = {s.group_id: s for s in GroupSession.objects.filter(group__facility=facility, date=today)}
        for g in groups:
            g.today_session = todays.get(g.pk)
            g.meets_today = today.weekday() in (g.weekdays or [])
        return render(request, self.template_name, {
            'groups': groups, 'children': list(active_children(facility)), 'colors': ClassGroup.COLORS,
            'week': list(enumerate(WEEK_JP[:6])), 'today': today, 'edit': to_int(request.GET.get('edit'), 0),
        })

    def post(self, request):
        facility = request.user.facility
        p = request.POST
        action = p.get('action', 'save')
        if action == 'delete':
            g = get_object_or_404(ClassGroup, pk=to_int(p.get('pk'), -1), facility=facility)
            name = g.name
            g.delete()
            messages.success(request, f'クラス「{name}」を削除しました（活動の計画と記録も消えました。療育記録に写したものは残ります）。')
            return redirect('daily:groups')
        g = get_object_or_404(ClassGroup, pk=to_int(p.get('pk'), -1), facility=facility) if p.get('pk') else ClassGroup(facility=facility)
        name = (p.get('name') or '').strip()[:50]
        if not name:
            messages.error(request, 'クラス名を入れてください。')
            return redirect('daily:groups')
        g.name = name
        g.color = p.get('color') if p.get('color') in dict(ClassGroup.COLORS) else g.color
        g.weekdays = sorted({d for d in (to_int(x, -1) for x in p.getlist('weekdays')) if 0 <= d <= 5})
        g.start_time = parse_time(p.get('start_time'))
        g.note = (p.get('note') or '').strip()[:200]
        g.order = max(0, min(999, to_int(p.get('order'), g.order or 0)))
        g.is_active = 'is_active' in p if g.pk else True
        g.save()
        ids = {to_int(x, 0) for x in p.getlist('members')}
        g.members.set(active_children(facility).filter(pk__in=ids) | g.members.exclude(status=Beneficiary.STATUS_ACTIVE).filter(pk__in=ids))
        messages.success(request, f'クラス「{g.name}」を保存しました（メンバー {g.members.count()} 人）。')
        return redirect('daily:groups')


def week_days(group, monday):
    """その週の月〜土。クラスの曜日の日と、計画・記録がある日"""
    sessions = {s.date: s for s in group.sessions.filter(date__gte=monday, date__lt=monday + datetime.timedelta(days=7))}
    out = []
    for i in range(6):
        d = monday + datetime.timedelta(days=i)
        s = sessions.get(d)
        out.append({'date': d, 'weekday': WEEK_JP[i], 'session': s, 'meets': i in (group.weekdays or []) or s is not None})
    return out


class GroupWeekView(DailyEnabledMixin, View):
    """クラスの週間の活動計画（月〜土）。その場で計画を入れて保存できる"""
    template_name = 'daily/week.html'

    def get(self, request, pk):
        g = get_object_or_404(ClassGroup, pk=pk, facility=request.user.facility)
        monday = monday_of(parse_day(request.GET.get('d')))
        return render(request, self.template_name, {
            'group': g, 'monday': monday, 'days': week_days(g, monday), 'today': datetime.date.today(),
            'prev': monday - datetime.timedelta(days=7), 'next': monday + datetime.timedelta(days=7),
            'members': list(g.members.all()), 'activity_range': range(ACTIVITY_MAX),
        })

    def post(self, request, pk):
        g = get_object_or_404(ClassGroup, pk=pk, facility=request.user.facility)
        monday = monday_of(parse_day(request.POST.get('d')))
        p = request.POST
        n = 0
        with transaction.atomic():
            for i in range(6):
                d = monday + datetime.timedelta(days=i)
                key = d.isoformat()
                if f'present_{key}' not in p:
                    continue
                aim = (p.get(f'aim_{key}') or '').strip()[:200]
                acts = [a.strip()[:60] for a in p.getlist(f'act_{key}') if a.strip()][:ACTIVITY_MAX]
                materials = (p.get(f'materials_{key}') or '').strip()[:300]
                staff = (p.get(f'staff_{key}') or '').strip()[:100]
                s = g.sessions.filter(date=d).first()
                if s is None and not (aim or acts or materials or staff):
                    continue
                if s is None:
                    s = GroupSession(group=g, date=d, start_time=g.start_time, created_by=request.user)
                s.aim, s.activities, s.materials, s.staff_name = aim, acts, materials, staff
                s.save()
                n += 1
        messages.success(request, f'{monday:%-m月%-d日}の週の計画を保存しました（{n} 日）。')
        return redirect(reverse('daily:group_week', args=[g.pk]) + f'?d={monday.isoformat()}')


class GroupWeekPdfView(DailyEnabledMixin, View):
    """週間の活動計画（A4 横）"""

    def get(self, request, pk):
        g = get_object_or_404(ClassGroup, pk=pk, facility=request.user.facility)
        monday = monday_of(parse_day(request.GET.get('d')))
        return pdf_or_html(request, 'daily/pdf/week.html', {
            'facility': request.user.facility, 'group': g, 'monday': monday, 'days': week_days(g, monday),
            'members': list(g.members.all()),
        }, f'週間の活動計画_{g.name}_{monday:%Y%m%d}')


def present_children(group, day):
    """その日の参加の候補：メンバーのうち、予約管理を使う事業所ならその日に予約（確定）がある人。予約が無い事業所はメンバー全員"""
    members = list(group.members.filter(status=Beneficiary.STATUS_ACTIVE).order_by('last_name_kana', 'first_name_kana'))
    if not group.facility.use_reservation:
        return members, {m.pk for m in members}
    from reservations.models import Reservation
    booked = set(Reservation.objects.filter(facility=group.facility, date=day, status=Reservation.STATUS_CONFIRMED,
                                            beneficiary__in=members).values_list('beneficiary_id', flat=True))
    return members, booked


@transaction.atomic
def apply_to_therapy(session, user):
    """参加した子ども一人ずつの療育記録に写す（写したことのある記録は上書き）。写した数"""
    from therapy.models import TherapyRecord
    group = session.group
    facility = group.facility
    ids = [int(x) for x in session.present]
    children = {b.pk: b for b in Beneficiary.objects.filter(facility=facility, pk__in=ids)}
    record_ids = dict(session.record_ids or {})
    n = 0
    for bid in ids:
        b = children.get(bid)
        if b is None:
            continue
        own = (session.notes or {}).get(str(bid), '').strip()
        body = f'【{group.name}】'
        if session.aim:
            body += f'ねらい：{session.aim}\n'
        else:
            body += '\n'
        if session.body.strip():
            body += session.body.strip() + '\n'
        if own:
            body += f'（{b.first_name}さん）{own}'
        body = body.strip()[:4000]
        rec = TherapyRecord.objects.filter(pk=record_ids.get(str(bid)), facility=facility, beneficiary=b).first()
        if rec is None:
            rec = TherapyRecord(facility=facility, beneficiary=b, date=session.date, created_by=user)
        rec.date = session.date
        rec.time = session.start_time
        rec.activities = session.activity_list[:ACTIVITY_MAX]
        rec.body = body
        rec.staff_name = session.staff_name[:50]
        if rec.staff_id is None and not rec.staff_name:
            rec.staff = user
        rec.save()
        record_ids[str(bid)] = rec.pk
        n += 1
    session.record_ids = record_ids
    session.applied_at = timezone.now()
    session.save(update_fields=['record_ids', 'applied_at', 'updated_at'])
    return n


class SessionView(DailyEnabledMixin, View):
    """クラスのその日：計画を直し、記録（全体の様子・参加・一人ずつのひとこと）を書き、療育記録に写す"""
    template_name = 'daily/session.html'

    def _get(self, request, pk, day):
        g = get_object_or_404(ClassGroup, pk=pk, facility=request.user.facility)
        try:
            d = datetime.date.fromisoformat(day)
        except ValueError:
            from django.http import Http404
            raise Http404
        s = g.sessions.filter(date=d).first() or GroupSession(group=g, date=d, start_time=g.start_time)
        return g, d, s

    def get(self, request, pk, day):
        g, d, s = self._get(request, pk, day)
        members, booked = present_children(g, d)
        present = set(int(x) for x in s.present) if s.pk and s.present else booked
        rows = [{'b': m, 'present': m.pk in present, 'booked': m.pk in booked, 'note': (s.notes or {}).get(str(m.pk), ''),
                 'record_id': (s.record_ids or {}).get(str(m.pk))} for m in members]
        return render(request, self.template_name, {
            'group': g, 'day': d, 'weekday': WEEK_JP[d.weekday()], 'session': s, 'rows': rows,
            'activities': (s.activity_list + [''] * ACTIVITY_MAX)[:ACTIVITY_MAX], 'use_therapy': g.facility.use_therapy_record,
            'monday': monday_of(d),
        })

    def post(self, request, pk, day):
        g, d, s = self._get(request, pk, day)
        if s.pk:
            conflict = check_conflict(request, s)
            if conflict:
                messages.error(request, conflict)
                return redirect('daily:session', pk, day)
        else:
            s.created_by = request.user
        p = request.POST
        s.start_time = parse_time(p.get('start_time'))
        s.aim = (p.get('aim') or '').strip()[:200]
        s.activities = [a.strip()[:60] for a in p.getlist('act') if a.strip()][:ACTIVITY_MAX]
        s.materials = (p.get('materials') or '').strip()[:300]
        s.staff_name = (p.get('staff_name') or '').strip()[:100]
        s.body = (p.get('body') or '').strip()[:4000]
        member_ids = set(g.members.values_list('pk', flat=True))
        s.present = sorted(i for i in (to_int(x, 0) for x in p.getlist('present')) if i in member_ids)
        s.notes = {str(i): (p.get(f'note_{i}') or '').strip()[:1000] for i in member_ids if (p.get(f'note_{i}') or '').strip()}
        s.save()
        if p.get('action') == 'apply':
            if not g.facility.use_therapy_record:
                messages.error(request, 'この事業所では療育記録を使わない設定のため、写せません。')
            elif not s.present:
                messages.error(request, '参加した人に印を付けてください。')
            else:
                n = apply_to_therapy(s, request.user)
                messages.success(request, f'保存して、{n} 人の療育記録に写しました（写したことのある記録は書き換えました）。')
                return redirect('daily:session', pk, day)
        messages.success(request, f'{g.name}の{d:%-m月%-d日}を保存しました。')
        return redirect('daily:session', pk, day)


# ------------------------------------------------------------------ 健康の記録
def day_children(facility, day):
    """その日の対象：予約管理を使う事業所はその日の予約（確定）がある人、使わない事業所は在籍中の全員"""
    if facility.use_reservation:
        from reservations.models import Reservation
        rows = list(Reservation.objects.filter(facility=facility, date=day, status=Reservation.STATUS_CONFIRMED, beneficiary__isnull=False)
                    .select_related('beneficiary').order_by('start_time', 'beneficiary__last_name_kana'))
        seen, out = set(), []
        for r in rows:
            if r.beneficiary_id not in seen:
                seen.add(r.beneficiary_id)
                out.append((r.beneficiary, r.start_time))
        return out
    return [(b, None) for b in active_children(facility)]


HEALTH_FIELDS = ['temp_arrival', 'temp_other', 'meal', 'meal_note', 'urine', 'stool', 'toilet_note', 'nap_minutes',
                 'medication_given', 'medication_note', 'mood', 'note', 'handed_to', 'handed_at']


def read_health(log, p, prefix=''):
    log.temp_arrival = parse_temp(p.get(f'{prefix}temp_arrival'))
    log.temp_other = parse_temp(p.get(f'{prefix}temp_other'))
    log.meal = p.get(f'{prefix}meal') if p.get(f'{prefix}meal') in dict(HealthLog.MEAL_CHOICES) else ''
    log.meal_note = (p.get(f'{prefix}meal_note') or '').strip()[:200]
    log.urine = to_int(p.get(f'{prefix}urine'), None) if (p.get(f'{prefix}urine') or '').strip() else None
    log.stool = to_int(p.get(f'{prefix}stool'), None) if (p.get(f'{prefix}stool') or '').strip() else None
    for f in ('urine', 'stool'):
        v = getattr(log, f)
        if v is not None:
            setattr(log, f, max(0, min(30, v)))
    log.toilet_note = (p.get(f'{prefix}toilet_note') or '').strip()[:200]
    nap = to_int(p.get(f'{prefix}nap_minutes'), None)
    log.nap_minutes = max(0, min(600, nap)) if nap is not None else None
    log.medication_given = f'{prefix}medication_given' in p
    log.medication_note = (p.get(f'{prefix}medication_note') or '').strip()[:200]
    log.mood = p.get(f'{prefix}mood') if p.get(f'{prefix}mood') in dict(HealthLog.MOOD_CHOICES) else ''
    log.note = (p.get(f'{prefix}note') or '').strip()[:2000]
    log.handed_to = (p.get(f'{prefix}handed_to') or '').strip()[:50]
    log.handed_at = parse_time(p.get(f'{prefix}handed_at'))
    return log


class HealthDayView(DailyEnabledMixin, View):
    """その日の健康の記録（一覧で入れて、まとめて保存）"""
    template_name = 'daily/health_day.html'

    def get(self, request):
        facility = request.user.facility
        day = parse_day(request.GET.get('d'))
        kids = day_children(facility, day)
        logs = {l.beneficiary_id: l for l in HealthLog.objects.filter(facility=facility, date=day)}
        profiles = {hp.beneficiary_id: hp for hp in HealthProfile.objects.filter(beneficiary__in=[b for b, _ in kids])}
        rows = [{'b': b, 'time': t, 'log': logs.get(b.pk) or HealthLog(facility=facility, beneficiary=b, date=day),
                 'alerts': profiles[b.pk].alerts if b.pk in profiles else []} for b, t in kids]
        done = sum(1 for r in rows if r['log'].pk and not r['log'].is_empty)
        return render(request, self.template_name, {
            'day': day, 'weekday': WEEK_JP[day.weekday()], 'rows': rows, 'done': done, 'today': datetime.date.today(),
            'prev': day - datetime.timedelta(days=1), 'next': day + datetime.timedelta(days=1),
            'meal_choices': HealthLog.MEAL_CHOICES, 'mood_choices': HealthLog.MOOD_CHOICES, 'use_reservation': facility.use_reservation,
            'fever': HealthLog.FEVER,
        })

    def post(self, request):
        facility = request.user.facility
        day = parse_day(request.POST.get('d'))
        p = request.POST
        ids = {b.pk for b, _ in day_children(facility, day)}
        n = 0
        with transaction.atomic():
            for bid in ids:
                prefix = f'b{bid}_'
                if f'{prefix}present' not in p:
                    continue
                log = HealthLog.objects.filter(beneficiary_id=bid, date=day).first() or HealthLog(facility=facility, beneficiary_id=bid, date=day)
                read_health(log, p, prefix)
                if log.is_empty and not log.pk:
                    continue
                log.recorded_by = request.user
                log.save()
                n += 1
        messages.success(request, f'{day:%-m月%-d日}の健康の記録を保存しました（{n} 人）。')
        return redirect(reverse('daily:health') + f'?d={day.isoformat()}')


class HealthChildView(DailyEnabledMixin, View):
    """子どもごとの健康の記録（直近 31 日）と、健康の注意（アレルギー・服薬など）"""
    template_name = 'daily/health_child.html'

    def get(self, request, pk):
        b = get_object_or_404(Beneficiary, pk=pk, facility=request.user.facility)
        logs = list(b.health_logs.order_by('-date')[:31])
        profile = HealthProfile.objects.filter(beneficiary=b).first() or HealthProfile(beneficiary=b)
        temps = [(l.date, float(l.temp_arrival)) for l in reversed(logs) if l.temp_arrival is not None]
        return render(request, self.template_name, {'b': b, 'logs': logs, 'profile': profile, 'today': datetime.date.today(),
                                                    'temp_svg': temp_chart(temps), 'fever': HealthLog.FEVER})

    def post(self, request, pk):
        b = get_object_or_404(Beneficiary, pk=pk, facility=request.user.facility)
        profile, _ = HealthProfile.objects.get_or_create(beneficiary=b)
        conflict = check_conflict(request, profile)
        if conflict:
            messages.error(request, conflict)
            return redirect('daily:health_child', pk)
        for f in ('allergies', 'medications', 'seizure', 'other'):
            setattr(profile, f, (request.POST.get(f) or '').strip()[:300])
        profile.save()
        messages.success(request, f'{b.full_name} さんの健康の注意を保存しました。')
        return redirect('daily:health_child', pk)


def temp_chart(points, width=520, height=120):
    """来所時の体温の折れ線（SVG）。37.5℃ に赤い線"""
    from django.utils.safestring import mark_safe
    if len(points) < 2:
        return ''
    lo, hi = 35.5, 38.5
    left, right, top, bottom = 34, 8, 8, 18
    w, h = width - left - right, height - top - bottom
    n = len(points)

    def xy(i, t):
        t = max(lo, min(hi, t))
        return left + w * i / (n - 1), top + h * (hi - t) / (hi - lo)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="100%" style="max-width:{width}px" role="img" aria-label="来所時の体温の推移">']
    for t in (36.0, 37.0, 38.0):
        _, y = xy(0, t)
        out.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}" stroke="#e5e1d8"/><text x="{left - 4}" y="{y + 3:.1f}" font-size="9" text-anchor="end" fill="#8a8378">{t:.0f}</text>')
    _, y = xy(0, HealthLog.FEVER)
    out.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}" stroke="#c0392b" stroke-dasharray="4 3"/>')
    pts = ' '.join(f'{x:.1f},{y:.1f}' for x, y in (xy(i, t) for i, (_, t) in enumerate(points)))
    out.append(f'<polyline points="{pts}" fill="none" stroke="#4e7d89" stroke-width="2"/>')
    for i, (d, t) in enumerate(points):
        x, y = xy(i, t)
        color = '#c0392b' if t >= HealthLog.FEVER else '#4e7d89'
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{color}"><title>{d:%-m/%-d} {t:.1f}℃</title></circle>')
    out.append(f'<text x="{left}" y="{height - 4}" font-size="9" fill="#8a8378">{points[0][0]:%-m/%-d}</text>')
    out.append(f'<text x="{width - right}" y="{height - 4}" font-size="9" fill="#8a8378" text-anchor="end">{points[-1][0]:%-m/%-d}</text>')
    out.append('</svg>')
    return mark_safe(''.join(out))


class HealthCardView(DailyEnabledMixin, View):
    """引き渡しカード：お迎えのときに保護者に見せる その日の様子（健康の記録・クラスの活動・療育記録）。印刷・スマートフォン向け"""
    template_name = 'daily/health_card.html'

    def get(self, request, pk):
        b = get_object_or_404(Beneficiary, pk=pk, facility=request.user.facility)
        day = parse_day(request.GET.get('d'))
        log = HealthLog.objects.filter(beneficiary=b, date=day).first()
        sessions = list(GroupSession.objects.filter(group__facility=b.facility, date=day, group__members=b).select_related('group'))
        sessions = [s for s in sessions if not s.present or b.pk in [int(x) for x in s.present]]
        therapy = []
        if b.facility.use_therapy_record:
            therapy = list(b.therapy_records.filter(date=day).order_by('time', 'pk'))
        profile = HealthProfile.objects.filter(beneficiary=b).first()
        return render(request, self.template_name, {'b': b, 'day': day, 'weekday': WEEK_JP[day.weekday()], 'log': log, 'sessions': sessions,
                                                    'therapy': therapy, 'alerts': profile.alerts if profile else [],
                                                    'facility': b.facility, 'print': request.GET.get('print') == '1'})

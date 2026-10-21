"""
居宅介護支援（ケアマネ）の画面。利用者の画面の上のタブ（利用者情報・支援経過・予定・実績・ケアプラン・担当者会議の要点・アセスメント）と、
取引先・月の予定・実績の一覧。画面の型「居宅介護」の事業所だけで使う。AI は使わない。
"""
import calendar
import datetime

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.db import models as db_models
from django.http import JsonResponse
from django.views import View

from beneficiaries.models import Beneficiary
from config.pdf import pdf_or_html
from homecare.views import KyotakuMixin, parse_date, parse_time

from .models import (ASSESSMENT_ITEMS, ASSESSMENT_KEYS, CARE_LEVEL_CHOICES, SCAN_EXTENSIONS, SCAN_MAX_BYTES, SCAN_MAX_FILES,
                     SERVICE_KINDS, SUMMARY_CHOICES, WEEK_JP, Assessment, CareInsurance, KaigoPlan, KaigoSetting, Meeting,
                     Provider, ScanDocument, ServiceUse, SupportLog, latest_insurance)

TEXT_MAX = 4000


def _text(p, key, n=TEXT_MAX):
    return (p.get(key) or '').strip()[:n]


def _month_days(year, month):
    return [datetime.date(year, month, d) for d in range(1, calendar.monthrange(year, month)[1] + 1)]


def _shift(year, month, n):
    m = year * 12 + month - 1 + n
    return m // 12, m % 12 + 1


class PersonMixin(KyotakuMixin):
    """利用者ごとのタブの画面"""
    tab = ''

    scan_kind = ''

    def ctx(self, b, **extra):
        c = {'b': b, 'tab': self.tab, 'insurance': latest_insurance(b), 'today': datetime.date.today(), **extra}
        if self.scan_kind:      # 紙・スキャンの書類（そのタブの種類のもの）
            c.update(scan_kind=self.scan_kind, scan_kind_label=dict(ScanDocument.KINDS)[self.scan_kind],
                     scans=list(ScanDocument.objects.filter(beneficiary=b, kind=self.scan_kind).select_related('uploaded_by')),
                     scan_accept=','.join('.' + e for e in SCAN_EXTENSIONS), scan_kinds=ScanDocument.KINDS)
        return c


# ---------------------------------------------------------------- 介護保険証
class InsuranceView(PersonMixin, View):
    tab = 'info'
    DATE_FIELDS = ('certified_on', 'issued_on', 'applied_from', 'applied_until', 'valid_from', 'valid_until', 'copay_issued_on',
                   'copay_from')

    def get(self, request, pk, ins_pk=None):
        b = self.person_or_404(pk)
        ins = get_object_or_404(CareInsurance, pk=ins_pk, beneficiary=b) if ins_pk else CareInsurance(beneficiary=b)
        if not ins_pk:
            prev = latest_insurance(b)
            if prev:       # 更新のときは番号を写して始める
                ins.insurer_number, ins.insured_number, ins.copay_ratio = prev.insurer_number, prev.insured_number, prev.copay_ratio
        return render(request, 'kaigo/insurance_form.html', self.ctx(b, ins=ins, levels=CARE_LEVEL_CHOICES,
                                                                     history=b.care_insurances.all()))

    def post(self, request, pk, ins_pk=None):
        b = self.person_or_404(pk)
        ins = get_object_or_404(CareInsurance, pk=ins_pk, beneficiary=b) if ins_pk else CareInsurance(beneficiary=b)
        p = request.POST
        back = reverse('beneficiaries:detail', args=[b.pk]) + '#care-insurance'
        if ins_pk and p.get('action') == 'delete':
            ins.delete()
            messages.success(request, '介護保険証を1件消しました。')
            return redirect(back)
        ins.insurer_number = _text(p, 'insurer_number', 8)
        ins.insured_number = _text(p, 'insured_number', 12)
        ins.care_level = p.get('care_level') if p.get('care_level') in dict(CARE_LEVEL_CHOICES) else ''
        for f in self.DATE_FIELDS:
            setattr(ins, f, parse_date(p.get(f)))
        ins.copay_ratio = int(p['copay_ratio']) if p.get('copay_ratio') in ('1', '2', '3') else None
        ins.notes = _text(p, 'notes', 200)
        ins.save()
        messages.success(request, f'{b.full_name}さんの介護保険証を保存しました。')
        return redirect(back)


# ---------------------------------------------------------------- 支援経過（第5表）
LOG_PAGE = 100      # 一度に出す件数（新しい順。「もっと前を見る」で足す）


def with_labels(logs, setting):
    """種別の名前（事業所で変えたもの）を付ける"""
    labels = setting.labels()
    logs = list(logs)
    for log in logs:
        log._labels = labels
    return logs


class LogListView(PersonMixin, View):
    tab = 'logs'
    scan_kind = 'logs'

    def _filtered(self, request, b):
        qs = SupportLog.objects.filter(beneficiary=b, facility=self.facility).select_related('provider', 'created_by')
        cat = request.GET.get('cat', '')
        if cat in dict(SupportLog.CATEGORIES):
            qs = qs.filter(category=cat)
        start, end = parse_date(request.GET.get('from')), parse_date(request.GET.get('to'))
        if start:
            qs = qs.filter(date__gte=start)
        if end:
            qs = qs.filter(date__lte=end)
        return qs, cat, start, end

    def get(self, request, pk):
        b = self.person_or_404(pk)
        logs, cat, start, end = self._filtered(request, b)
        total = logs.count()
        try:
            shown = min(max(int(request.GET.get('n') or LOG_PAGE), LOG_PAGE), 5000)
        except ValueError:
            shown = LOG_PAGE
        more = request.GET.copy()
        more['n'] = shown + LOG_PAGE
        more.pop('edit', None)
        edit = None
        if request.GET.get('edit'):
            edit = SupportLog.objects.filter(pk=request.GET['edit'], beneficiary=b).first()
        now = datetime.datetime.now()
        setting = KaigoSetting.of(self.facility)
        return render(request, 'kaigo/logs.html', self.ctx(
            b, logs=with_labels(logs[:shown], setting), total=total, more_query=more.urlencode(), cat=cat, start=start, end=end,
            edit=edit, categories=setting.categories(), setting=setting, summary_choices=SUMMARY_CHOICES,
            methods=SupportLog.METHODS, providers=Provider.objects.filter(facility=self.facility, is_active=True),
            now_date=now.date(), now_time=now.strftime('%H:%M'), query=request.GET.urlencode()))

    def post(self, request, pk):
        b = self.person_or_404(pk)
        p = request.POST
        log = SupportLog.objects.filter(pk=p.get('log'), beneficiary=b).first() if p.get('log') else None
        back = reverse('kaigo:logs', args=[b.pk])
        if log and p.get('action') == 'delete':
            log.delete()
            messages.success(request, '支援経過を1件消しました。')
            return redirect(back)
        content = _text(p, 'content', 8000)
        if not content:
            messages.error(request, '内容を書いてください。')
            return redirect(back)
        if log is None:
            log = SupportLog(facility=self.facility, beneficiary=b, created_by=request.user)
        log.date = parse_date(p.get('date'), datetime.date.today())
        log.time = parse_time(p.get('time'))
        log.category = p.get('category') if p.get('category') in dict(SupportLog.CATEGORIES) else 'other'
        log.method = p.get('method') if p.get('method') in dict(SupportLog.METHODS) else ''
        log.provider = Provider.objects.filter(pk=p.get('provider') or 0, facility=self.facility).first()
        log.content = content
        log.save()
        messages.success(request, f'支援経過を保存しました（{log.date:%Y/%-m/%-d} {log.get_category_display()}）。')
        return redirect(back)


class LogPrintView(LogListView):
    def get(self, request, pk):
        b = self.person_or_404(pk)
        logs, cat, start, end = self._filtered(request, b)
        return pdf_or_html(request, 'kaigo/pdf/logs.html', self.ctx(
            b, logs=list(reversed(with_labels(logs[:1000], KaigoSetting.of(self.facility)))), facility=self.facility,
            start=start, end=end),
            f'居宅介護支援経過_{b.full_name}')


# ---------------------------------------------------------------- 予定・実績（サービス利用票）
USE_BLANK_ROWS = 3


class UsesNowView(PersonMixin, View):
    def get(self, request, pk):
        today = datetime.date.today()
        return redirect('kaigo:uses', pk=pk, year=today.year, month=today.month)


class UsesView(PersonMixin, View):
    tab = 'uses'
    scan_kind = 'uses'

    def _rows(self, b, year, month):
        return list(ServiceUse.objects.filter(beneficiary=b, facility=self.facility, year=year, month=month)
                    .select_related('provider'))

    def get(self, request, pk, year, month):
        b = self.person_or_404(pk)
        if not (2000 <= year <= 2100 and 1 <= month <= 12):
            return redirect('kaigo:uses_now', pk=pk)
        days = _month_days(year, month)
        rows = self._rows(b, year, month)
        for r in rows:
            r.plan_set, r.done_set = set(r.plan_days or []), set(r.done_days or [])
        blanks = [ServiceUse(order=len(rows) + i) for i in range(1 if rows else USE_BLANK_ROWS)]
        for r in blanks:
            r.plan_set, r.done_set = set(), set()
        prev, nxt = _shift(year, month, -1), _shift(year, month, 1)
        return render(request, 'kaigo/uses.html', self.ctx(
            b, year=year, month=month, days=days, rows=rows + blanks, filled=rows, week=WEEK_JP, prev=prev, next=nxt,
            providers=Provider.objects.filter(facility=self.facility, is_active=True), service_kinds=SERVICE_KINDS,
            plan_total=sum(len(r.plan_set) for r in rows), done_total=sum(len(r.done_set) for r in rows),
            has_prev=ServiceUse.objects.filter(beneficiary=b, year=prev[0], month=prev[1]).exists()))

    def post(self, request, pk, year, month):
        b = self.person_or_404(pk)
        p = request.POST
        back = redirect('kaigo:uses', pk=pk, year=year, month=month)
        n_days = calendar.monthrange(year, month)[1]
        action = p.get('action', 'save')
        if action == 'copy_prev':
            if ServiceUse.objects.filter(beneficiary=b, year=year, month=month).exists():
                messages.error(request, 'この月にはもう行があります。写すのは空の月だけです。')
                return back
            py, pm = _shift(year, month, -1)
            made = 0
            for r in ServiceUse.objects.filter(beneficiary=b, facility=self.facility, year=py, month=pm):
                plan = _same_weekdays(r.plan_days, py, pm, year, month)
                ServiceUse.objects.create(facility=self.facility, beneficiary=b, year=year, month=month, order=r.order,
                                          provider=r.provider, provider_name=r.provider_name, service=r.service,
                                          start=r.start, end=r.end, plan_days=plan, done_days=[])
                made += 1
            messages.success(request, f'前の月の {made} 行を写しました（予定の日は同じ曜日に合わせています）。')
            return back
        if action == 'plan_to_done':
            for r in self._rows(b, year, month):
                r.done_days = sorted(set(r.plan_days or []))
                r.save(update_fields=['done_days', 'updated_at'])
            messages.success(request, '予定をそのまま実績にしました。使わなかった日は印を外してください。')
            return back
        existing = {str(r.pk): r for r in self._rows(b, year, month)}
        kept = 0
        for i in range(int(p.get('row_count') or 0)):
            key = p.get(f'r{i}_id', '')
            r = existing.get(key) or ServiceUse(facility=self.facility, beneficiary=b, year=year, month=month)
            provider = Provider.objects.filter(pk=p.get(f'r{i}_provider') or 0, facility=self.facility).first()
            name = _text(p, f'r{i}_provider_name', 100)
            service = _text(p, f'r{i}_service', 100)
            plan = sorted({int(x) for x in p.getlist(f'r{i}_plan') if x.isdigit() and 1 <= int(x) <= n_days})
            done = sorted({int(x) for x in p.getlist(f'r{i}_done') if x.isdigit() and 1 <= int(x) <= n_days})
            if p.get(f'r{i}_delete') or not (provider or name or service or plan or done):
                if r.pk:
                    r.delete()
                continue
            r.provider, r.provider_name, r.service = provider, ('' if provider else name), service
            r.start, r.end = _text(p, f'r{i}_start', 5), _text(p, f'r{i}_end', 5)
            r.plan_days, r.done_days, r.order = plan, done, kept
            r.save()
            kept += 1
        messages.success(request, f'{year}年{month}月の予定・実績を保存しました（{kept} 行）。')
        return back


def _same_weekdays(days, py, pm, year, month):
    """前の月の予定日を、同じ曜日・同じ週の並び（第1〜第5）でこの月に移す"""
    out = set()
    n = calendar.monthrange(year, month)[1]
    for d in days or []:
        try:
            src = datetime.date(py, pm, int(d))
        except (TypeError, ValueError):
            continue
        nth = (src.day - 1) // 7
        first = datetime.date(year, month, 1)
        offset = (src.weekday() - first.weekday()) % 7
        day = 1 + offset + nth * 7
        if day <= n:
            out.add(day)
    return sorted(out)


class UsesPrintView(UsesView):
    def get(self, request, pk, year, month):
        b = self.person_or_404(pk)
        rows = self._rows(b, year, month)
        for r in rows:
            r.plan_set, r.done_set = set(r.plan_days or []), set(r.done_days or [])
        return pdf_or_html(request, 'kaigo/pdf/uses.html', self.ctx(
            b, year=year, month=month, days=_month_days(year, month), rows=rows, week=WEEK_JP, facility=self.facility),
            f'サービス利用票_{b.full_name}_{year}{month:02d}')


class MonthlyView(KyotakuMixin, View):
    """月の予定・実績（利用者ごとの行数・予定回数・実績回数。実績の入っていない人がわかる）"""

    def get(self, request, year=None, month=None):
        today = datetime.date.today()
        year, month = year or today.year, month or today.month
        uses = {}
        for r in ServiceUse.objects.filter(facility=self.facility, year=year, month=month):
            d = uses.setdefault(r.beneficiary_id, {'rows': 0, 'plan': 0, 'done': 0})
            d['rows'] += 1
            d['plan'] += len(r.plan_days or [])
            d['done'] += len(r.done_days or [])
        people = Beneficiary.objects.filter(facility=self.facility, status=Beneficiary.STATUS_ACTIVE) \
            .order_by('last_name_kana', 'first_name_kana', 'pk')
        levels = {}
        for ins in CareInsurance.objects.filter(beneficiary__facility=self.facility).order_by('beneficiary_id', '-valid_from', '-pk'):
            levels.setdefault(ins.beneficiary_id, ins)
        rows = [{'b': b, 'use': uses.get(b.pk), 'ins': levels.get(b.pk)} for b in people]
        return render(request, 'kaigo/monthly.html', {
            'rows': rows, 'year': year, 'month': month, 'today_ymd': today.strftime('%Y%m%d'), 'prev': _shift(year, month, -1), 'next': _shift(year, month, 1),
            'counts': {'none': sum(1 for r in rows if not r['use']),
                       'no_done': sum(1 for r in rows if r['use'] and not r['use']['done'])}})


# ---------------------------------------------------------------- ケアプラン（第1〜3表）
class PlanListView(PersonMixin, View):
    tab = 'plans'
    scan_kind = 'plan'

    def get(self, request, pk):
        b = self.person_or_404(pk)
        return render(request, 'kaigo/plans.html', self.ctx(b, plans=KaigoPlan.objects.filter(beneficiary=b, facility=self.facility)))


class PlanFormView(PersonMixin, View):
    tab = 'plans'
    TEXT_FIELDS = ('wishes', 'committee_opinion', 'policy', 'daily_life', 'other_services')

    def get(self, request, pk, plan_pk=None):
        b = self.person_or_404(pk)
        if plan_pk:
            plan = get_object_or_404(KaigoPlan, pk=plan_pk, beneficiary=b, facility=self.facility)
        else:
            plan = KaigoPlan(facility=self.facility, beneficiary=b, created_on=datetime.date.today(),
                             planner=str(request.user)[:50])
            prev = KaigoPlan.objects.filter(beneficiary=b, facility=self.facility).first()
            if prev:      # 前の計画を写して始める（変更・更新のとき）
                for f in self.TEXT_FIELDS + ('goals', 'weekly', 'living_reason', 'living_reason_other', 'planner',
                                             'first_created_on'):
                    setattr(plan, f, getattr(prev, f))
            else:
                plan.plan_type, plan.first_created_on = 'first', plan.created_on
            ins = latest_insurance(b)
            if ins:
                plan.care_level, plan.certified_on = ins.care_level, ins.certified_on
                plan.cert_from, plan.cert_until = ins.valid_from, ins.valid_until
        return self._render(request, b, plan)

    def _render(self, request, b, plan):
        goals, weekly = plan.goal_rows(), plan.weekly_rows()
        filled_goals = sum(1 for g in goals if g.get('need') or g.get('long_goal') or g.get('service'))
        filled_weeks = sum(1 for w in weekly if w.get('content') or w.get('days'))
        return render(request, 'kaigo/plan_form.html', self.ctx(
            b, plan=plan, goals=goals, weekly=weekly, levels=CARE_LEVEL_CHOICES, week=list(enumerate(WEEK_JP)),
            first_blank_goal=filled_goals, first_blank_week=filled_weeks + 1,
            service_kinds=SERVICE_KINDS, providers=Provider.objects.filter(facility=self.facility, is_active=True)))

    def post(self, request, pk, plan_pk=None):
        b = self.person_or_404(pk)
        p = request.POST
        if plan_pk:
            plan = get_object_or_404(KaigoPlan, pk=plan_pk, beneficiary=b, facility=self.facility)
            if p.get('action') == 'delete':
                plan.delete()
                messages.success(request, f'居宅サービス計画書（{plan.created_on:%Y/%-m/%-d}）を消しました。')
                return redirect('kaigo:plans', pk=b.pk)
        else:
            plan = KaigoPlan(facility=self.facility, beneficiary=b, created_by=request.user)
        plan.created_on = parse_date(p.get('created_on'), datetime.date.today())
        for f in ('first_created_on', 'certified_on', 'cert_from', 'cert_until', 'explained_on'):
            setattr(plan, f, parse_date(p.get(f)))
        plan.plan_type = p.get('plan_type') if p.get('plan_type') in dict(KaigoPlan.PLAN_TYPES) else 'continue'
        plan.cert_state = p.get('cert_state') if p.get('cert_state') in dict(KaigoPlan.CERT_STATES) else 'certified'
        plan.care_level = p.get('care_level') if p.get('care_level') in dict(CARE_LEVEL_CHOICES) else ''
        plan.living_reason = p.get('living_reason') if p.get('living_reason') in dict(KaigoPlan.LIVING_REASONS) else ''
        plan.living_reason_other = _text(p, 'living_reason_other', 100)
        plan.planner, plan.agreed_by = _text(p, 'planner', 50), _text(p, 'agreed_by', 100)
        for f in self.TEXT_FIELDS:
            setattr(plan, f, _text(p, f))
        goals = []
        for i in range(int(p.get('goal_count') or 0)):
            row = {k: _text(p, f'g{i}_{k}', 1000) for k in KaigoPlan.GOAL_KEYS}
            row['insured'] = '○' if p.get(f'g{i}_insured') else ''
            if any(v for k, v in row.items() if k != 'insured'):
                goals.append(row)
        plan.goals = goals
        weekly = []
        for i in range(int(p.get('weekly_count') or 0)):
            days = sorted({int(x) for x in p.getlist(f'w{i}_days') if x.isdigit() and 0 <= int(x) <= 6})
            row = {'days': days, 'start': _text(p, f'w{i}_start', 5), 'end': _text(p, f'w{i}_end', 5),
                   'content': _text(p, f'w{i}_content', 100)}
            if row['content'] or days:
                weekly.append(row)
        plan.weekly = weekly
        plan.save()
        messages.success(request, f'{b.full_name}さんの居宅サービス計画書を保存しました。')
        return redirect('kaigo:plan_edit', pk=b.pk, plan_pk=plan.pk)


class PlanPrintView(PersonMixin, View):
    def get(self, request, pk, plan_pk):
        b = self.person_or_404(pk)
        plan = get_object_or_404(KaigoPlan, pk=plan_pk, beneficiary=b, facility=self.facility)
        return pdf_or_html(request, 'kaigo/pdf/plan.html', self.ctx(
            b, plan=plan, goals=plan.goal_rows(blank=False), grid=plan.week_grid(), week=WEEK_JP, facility=self.facility),
            f'居宅サービス計画書_{b.full_name}_{plan.created_on:%Y%m%d}')


# ---------------------------------------------------------------- サービス担当者会議の要点（第4表）
class MeetingListView(PersonMixin, View):
    tab = 'meetings'
    scan_kind = 'meeting'

    def get(self, request, pk):
        b = self.person_or_404(pk)
        return render(request, 'kaigo/meetings.html', self.ctx(b, meetings=Meeting.objects.filter(beneficiary=b, facility=self.facility)))


class MeetingFormView(PersonMixin, View):
    tab = 'meetings'
    TEXT_FIELDS = ('topics', 'discussion', 'conclusion', 'remaining')

    def get(self, request, pk, meeting_pk=None):
        b = self.person_or_404(pk)
        if meeting_pk:
            m = get_object_or_404(Meeting, pk=meeting_pk, beneficiary=b, facility=self.facility)
        else:
            prev = Meeting.objects.filter(beneficiary=b, facility=self.facility).first()
            m = Meeting(facility=self.facility, beneficiary=b, held_on=datetime.date.today(),
                        place=prev.place if prev else '自宅', count=((prev.count or 0) + 1) if prev else 1,
                        planner=prev.planner if prev else '', attendees=prev.attendees if prev else [])
        return render(request, 'kaigo/meeting_form.html', self.ctx(b, m=m, attendees=m.attendee_rows()))

    def post(self, request, pk, meeting_pk=None):
        b = self.person_or_404(pk)
        p = request.POST
        if meeting_pk:
            m = get_object_or_404(Meeting, pk=meeting_pk, beneficiary=b, facility=self.facility)
            if p.get('action') == 'delete':
                m.delete()
                messages.success(request, f'担当者会議の要点（{m.held_on:%Y/%-m/%-d}）を消しました。')
                return redirect('kaigo:meetings', pk=b.pk)
        else:
            m = Meeting(facility=self.facility, beneficiary=b, created_by=request.user)
        m.held_on = parse_date(p.get('held_on'), datetime.date.today())
        m.start, m.end = parse_time(p.get('start')), parse_time(p.get('end'))
        m.place, m.planner = _text(p, 'place', 100), _text(p, 'planner', 50)
        m.count = int(p['count']) if (p.get('count') or '').isdigit() and int(p['count']) < 1000 else None
        for f in self.TEXT_FIELDS:
            setattr(m, f, _text(p, f))
        attendees = []
        for i in range(int(p.get('attendee_count') or 0)):
            a = {'org': _text(p, f'a{i}_org', 100), 'name': _text(p, f'a{i}_name', 50)}
            if a['org'] or a['name']:
                attendees.append(a)
        m.attendees = attendees
        m.save()
        if p.get('add_log') and not meeting_pk:      # 支援経過にも1件残す
            SupportLog.objects.create(facility=self.facility, beneficiary=b, date=m.held_on, time=m.start, category='meeting',
                                      method='visit' if '自宅' in m.place else '', created_by=request.user,
                                      content=f'サービス担当者会議（{m.place}）を開催。' + (f'\n結論：{m.conclusion}' if m.conclusion else ''))
        messages.success(request, f'{b.full_name}さんの担当者会議の要点を保存しました。')
        return redirect('kaigo:meeting_edit', pk=b.pk, meeting_pk=m.pk)


class MeetingPrintView(PersonMixin, View):
    def get(self, request, pk, meeting_pk):
        b = self.person_or_404(pk)
        m = get_object_or_404(Meeting, pk=meeting_pk, beneficiary=b, facility=self.facility)
        rows = m.attendee_rows(blank=False)
        rows += [{}] * (-len(rows) % 3 or (3 if not rows else 0))
        return pdf_or_html(request, 'kaigo/pdf/meeting.html', self.ctx(
            b, m=m, attendee_lines=[rows[i:i + 3] for i in range(0, len(rows), 3)], facility=self.facility),
            f'サービス担当者会議の要点_{b.full_name}_{m.held_on:%Y%m%d}')


# ---------------------------------------------------------------- アセスメント（課題分析標準項目）
def previous_assessment(a):
    """a の一つ前のアセスメント（新しく作るときは、いちばん新しいもの）"""
    qs = Assessment.objects.filter(beneficiary=a.beneficiary, facility=a.facility)
    if a.pk:
        qs = qs.filter(db_models.Q(assessed_on__lt=a.assessed_on) | db_models.Q(assessed_on=a.assessed_on, pk__lt=a.pk))
    return qs.order_by('-assessed_on', '-pk').first()


def changed_items(a, prev):
    """前回から書きかえた項目の名前（前回が無ければ空）"""
    if prev is None:
        return []
    old, new = prev.items or {}, a.items or {}
    labels = {k: label for _, items in ASSESSMENT_ITEMS for k, label in items}
    return [labels[k] for k in ASSESSMENT_KEYS if (old.get(k) or '').strip() != (new.get(k) or '').strip()]


class AssessmentListView(PersonMixin, View):
    tab = 'assessments'
    scan_kind = 'assessment'

    def get(self, request, pk):
        b = self.person_or_404(pk)
        rows = list(Assessment.objects.filter(beneficiary=b, facility=self.facility).order_by('-assessed_on', '-pk'))
        for i, a in enumerate(rows):
            a.changed = changed_items(a, rows[i + 1]) if i + 1 < len(rows) else None
        return render(request, 'kaigo/assessments.html', self.ctx(b, assessments=rows, total=len(ASSESSMENT_KEYS)))


class AssessmentFormView(PersonMixin, View):
    tab = 'assessments'

    def get(self, request, pk, a_pk=None):
        b = self.person_or_404(pk)
        if a_pk:
            a = get_object_or_404(Assessment, pk=a_pk, beneficiary=b, facility=self.facility)
        else:
            prev = Assessment.objects.filter(beneficiary=b, facility=self.facility).first()
            a = Assessment(facility=self.facility, beneficiary=b, assessed_on=datetime.date.today(), place='自宅',
                           kind='regular' if prev else 'first', items=dict(prev.items) if prev else {},
                           assessor=prev.assessor if prev else '', summary=prev.summary if prev else '')
        prev = previous_assessment(a)
        prev_items = (prev.items or {}) if prev else {}
        groups = [(group, [(k, label, v, prev_items.get(k, '')) for k, label, v in items]) for group, items in a.item_groups()]
        return render(request, 'kaigo/assessment_form.html', self.ctx(
            b, a=a, kinds=Assessment.KINDS, prev=prev, groups=groups,
            changed=changed_items(a, prev) if a.pk else []))

    def post(self, request, pk, a_pk=None):
        b = self.person_or_404(pk)
        p = request.POST
        if a_pk:
            a = get_object_or_404(Assessment, pk=a_pk, beneficiary=b, facility=self.facility)
            if p.get('action') == 'delete':
                a.delete()
                messages.success(request, f'アセスメント（{a.assessed_on:%Y/%-m/%-d}）を消しました。')
                return redirect('kaigo:assessments', pk=b.pk)
        else:
            a = Assessment(facility=self.facility, beneficiary=b, created_by=request.user)
        a.assessed_on = parse_date(p.get('assessed_on'), datetime.date.today())
        a.kind = p.get('kind') if p.get('kind') in dict(Assessment.KINDS) else 'other'
        a.place, a.assessor = _text(p, 'place', 100), _text(p, 'assessor', 50)
        a.items = {k: _text(p, f'i_{k}') for k in ASSESSMENT_KEYS if _text(p, f'i_{k}')}
        a.summary = _text(p, 'summary')
        a.save()
        changed = changed_items(a, previous_assessment(a))
        messages.success(request, f'{b.full_name}さんのアセスメントを保存しました。'
                         + (f'前回から変わった項目：{"・".join(changed)}' if changed else ''))
        return redirect('kaigo:assessment_edit', pk=b.pk, a_pk=a.pk)


class AssessmentPrintView(PersonMixin, View):
    def get(self, request, pk, a_pk):
        b = self.person_or_404(pk)
        a = get_object_or_404(Assessment, pk=a_pk, beneficiary=b, facility=self.facility)
        return pdf_or_html(request, 'kaigo/pdf/assessment.html', self.ctx(b, a=a, facility=self.facility, groups=ASSESSMENT_ITEMS),
                           f'アセスメント_{b.full_name}_{a.assessed_on:%Y%m%d}')


# ---------------------------------------------------------------- 取引先
class ProviderListView(KyotakuMixin, View):
    FIELDS = ('name', 'service_kind', 'office_number', 'phone', 'fax', 'email', 'address', 'contact', 'notes')

    def get(self, request):
        edit = Provider.objects.filter(pk=request.GET.get('edit') or 0, facility=self.facility).first()
        return render(request, 'kaigo/providers.html', {
            'providers': Provider.objects.filter(facility=self.facility), 'edit': edit, 'service_kinds': SERVICE_KINDS})

    def post(self, request):
        p = request.POST
        pv = Provider.objects.filter(pk=p.get('provider') or 0, facility=self.facility).first() if p.get('provider') else None
        if pv and p.get('action') == 'delete':
            pv.delete()
            messages.success(request, f'取引先「{pv.name}」を消しました（支援経過・予定の行には名前が残りません。取引をやめただけなら「取引をやめる」にしてください）。')
            return redirect('kaigo:providers')
        if pv and p.get('action') == 'toggle':
            pv.is_active = not pv.is_active
            pv.save(update_fields=['is_active', 'updated_at'])
            messages.success(request, f'「{pv.name}」を{"取引中" if pv.is_active else "取引をやめた"}にしました。')
            return redirect('kaigo:providers')
        if not _text(p, 'name'):
            messages.error(request, '事業所名を書いてください。')
            return redirect('kaigo:providers')
        pv = pv or Provider(facility=self.facility)
        for f in self.FIELDS:
            n = Provider._meta.get_field(f).max_length or TEXT_MAX
            setattr(pv, f, _text(p, f, n))
        try:
            pv.full_clean(exclude=['facility'])
        except Exception:  # noqa: BLE001 - メールの書き方などが違う
            messages.error(request, 'メールアドレスの書き方を確かめてください。')
            return redirect(reverse('kaigo:providers') + (f'?edit={pv.pk}' if pv.pk else ''))
        pv.save()
        messages.success(request, f'取引先「{pv.name}」を保存しました。')
        return redirect('kaigo:providers')


# ---------------------------------------------------------------- 紙・スキャンの書類
SCAN_BACK = {'plan': 'kaigo:plans', 'meeting': 'kaigo:meetings', 'assessment': 'kaigo:assessments', 'logs': 'kaigo:logs',
             'uses': 'kaigo:uses_now'}


def scan_back(b, kind):
    if kind in SCAN_BACK:
        return reverse(SCAN_BACK[kind], args=[b.pk]) + '#scans'
    return reverse('beneficiaries:detail', args=[b.pk]) + '#care-insurance'


class ScanUploadView(PersonMixin, View):
    """紙・スキャンの書類を取り込む（PDF・写真。1回に SCAN_MAX_FILES 個まで。スマートフォンはその場で撮れる）"""

    def post(self, request, pk):
        b = self.person_or_404(pk)
        p = request.POST
        kind = p.get('kind') if p.get('kind') in dict(ScanDocument.KINDS) else 'other'
        files = request.FILES.getlist('files')[:SCAN_MAX_FILES]
        title, doc_date = _text(p, 'title', 100), parse_date(p.get('doc_date'))
        made, skipped = 0, []
        for f in files:
            ext = (f.name or '').lower().rsplit('.', 1)[-1] if '.' in (f.name or '') else ''
            if ext not in SCAN_EXTENSIONS:
                skipped.append(f'{f.name}（PDF か写真だけ入れられます）')
                continue
            if f.size > SCAN_MAX_BYTES:
                skipped.append(f'{f.name}（{SCAN_MAX_BYTES // (1024 * 1024)} MB を超えています）')
                continue
            ScanDocument.objects.create(beneficiary=b, kind=kind, doc_date=doc_date, title=title, file=f,
                                        file_name=(f.name or '')[:200], uploaded_by=request.user)
            made += 1
        if made:
            messages.success(request, f'{dict(ScanDocument.KINDS)[kind]}の書類を {made} 件取り込みました。')
        if skipped:
            messages.error(request, '取り込めなかったもの：' + '、'.join(skipped))
        if not made and not skipped:
            messages.error(request, 'ファイルを選ぶか、枠の中に置いてください（PDF・写真）。')
        return redirect(scan_back(b, kind))


class ScanEditView(PersonMixin, View):
    """件名・書類の日付・種類を直す／消す"""

    def post(self, request, pk, scan_pk):
        b = self.person_or_404(pk)
        doc = get_object_or_404(ScanDocument, pk=scan_pk, beneficiary=b)
        p = request.POST
        back = scan_back(b, doc.kind)
        if p.get('action') == 'delete':
            name = doc.label
            doc.file.delete(save=False)
            doc.delete()
            messages.success(request, f'「{name}」を消しました。')
            return redirect(back)
        doc.title = _text(p, 'title', 100)
        doc.doc_date = parse_date(p.get('doc_date'))
        if p.get('kind') in dict(ScanDocument.KINDS):
            doc.kind = p['kind']
        doc.save()
        messages.success(request, f'「{doc.label}」を直しました。')
        return redirect(scan_back(b, doc.kind))


# ---------------------------------------------------------------- 記録の AI（音声・写真の文字起こし・要約）と設定
def _json_body(request):
    import json
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except (ValueError, UnicodeDecodeError):
        return {}


class AiSummarizeView(KyotakuMixin, View):
    """話した言葉・メモ → 支援経過に残す文（指定の文字数）。{'text', 'chars', 'category'} → {'text', 'added'?}"""

    def post(self, request):
        from ai_assist import asis

        from . import ai
        data = _json_body(request)
        text = str(data.get('text') or '').strip()[:12000]
        if not text:
            return JsonResponse({'error': 'まとめる文がありません。話すか書いてから押してください。'}, status=400)
        setting = KaigoSetting.of(self.facility)
        try:
            chars = int(data.get('chars') or setting.summary_chars)
        except (TypeError, ValueError):
            chars = setting.summary_chars
        chars = min(max(chars, 100), 1500)
        key = str(data.get('category') or '')
        out, error = ai.summarize(text, chars, key, setting.labels().get(key, ''), setting)
        if error is not None:
            return error
        return JsonResponse({'text': out, 'length': len(out), **asis.check(text, out)})


class AiReadImageView(KyotakuMixin, View):
    """紙・メモの写真（PDF も）→ 書いてある文字。{'text'}"""

    def post(self, request):
        from . import ai
        f = request.FILES.get('file')
        if f is None:
            return JsonResponse({'error': '写真を選んでください。'}, status=400)
        if f.size > SCAN_MAX_BYTES:
            return JsonResponse({'error': '大きすぎます（20MB まで）。'}, status=400)
        try:
            out, error = ai.read_image(f)
        except ValueError as e:
            return JsonResponse({'error': str(e)}, status=400)
        if error is not None:
            return error
        return JsonResponse({'text': out})


class AiMeetingView(KyotakuMixin, View):
    """会議のメモ → 担当者会議の要点の下書き。{'text'} → {'topics', 'discussion', 'conclusion', 'remaining'}"""

    def post(self, request):
        from . import ai
        text = str(_json_body(request).get('text') or '').strip()[:15000]
        if not text:
            return JsonResponse({'error': '会議のメモがありません。話すか書いてから押してください。'}, status=400)
        out, error = ai.meeting(text, KaigoSetting.of(self.facility))
        if error is not None:
            return error
        return JsonResponse(out)


class SettingView(KyotakuMixin, View):
    """記録の設定：AI でまとめる文字数・文体の希望・記録の種別の名前"""

    def get(self, request):
        setting = KaigoSetting.of(self.facility)
        labels = setting.category_labels or {}
        return render(request, 'kaigo/settings.html', {
            'setting': setting, 'summary_choices': SUMMARY_CHOICES,
            'rows': [(k, label, labels.get(k, '')) for k, label in SupportLog.CATEGORIES],
            'back': request.GET.get('back', '')})

    def post(self, request):
        setting = KaigoSetting.of(self.facility)
        p = request.POST
        try:
            chars = int(p.get('summary_chars') or 400)
        except ValueError:
            chars = 400
        setting.summary_chars = min(max(chars, 100), 1500)
        setting.style_note = _text(p, 'style_note', 600)
        setting.category_labels = {k: _text(p, f'label_{k}', 20) for k, _ in SupportLog.CATEGORIES if _text(p, f'label_{k}', 20)}
        setting.save()
        messages.success(request, '記録の設定を保存しました。')
        back = p.get('back') or ''
        from django.utils.http import url_has_allowed_host_and_scheme
        if back and url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}):
            return redirect(back)
        return redirect('kaigo:settings')

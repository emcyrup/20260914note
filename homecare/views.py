"""
居宅介護（ホームヘルプ）の画面。画面の型「居宅介護」（Facility.LAYOUT_KYOTAKU）の事業所だけで使う。

- きょうの訪問（ホーム）・訪問予定（月のカレンダー）・訪問の予定を入れる／直す（毎週くり返しも）
- サービス提供記録（訪問ごと：実際の時刻・したこと・体調・様子。様子は音声入力と「書き言葉に整える」）・月の一覧と印刷
- 利用者ごとの居宅介護の設定（障害支援区分・支給量・援助の留意点）
- 居宅介護計画（AI は使わない）と印刷
- 実績記録票（月 × 利用者。算定時間は 30 分単位の目安）
職員は 1 人の想定なので、担当はログインしている人を自動で入れる。
"""
import calendar
import datetime

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from beneficiaries.models import Beneficiary
from config.utils import month_or_404

from .models import (SERVICE_TYPES, SUPPORT_LEVELS, TASK_GROUPS, TASK_LABELS, CarePlan, HomeCareProfile, Visit)

WEEK_JP = '月火水木金土日'
TEXT_MAX = 4000


class KyotakuMixin(LoginRequiredMixin):
    """居宅介護の画面の型の事業所だけ"""

    def dispatch(self, request, *args, **kwargs):
        facility = getattr(request.user, 'facility', None)
        if request.user.is_authenticated and not (facility is not None and facility.is_kyotaku):
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    @property
    def facility(self):
        return self.request.user.facility

    def people(self):
        return Beneficiary.objects.filter(facility=self.facility, status=Beneficiary.STATUS_ACTIVE)

    def person_or_404(self, pk):
        from config.utils import to_int
        return get_object_or_404(Beneficiary, pk=to_int(pk, 0), facility=self.facility)

    def visit_or_404(self, pk):
        from config.utils import to_int
        return get_object_or_404(Visit.objects.select_related('beneficiary'), pk=to_int(pk, 0), facility=self.facility)


def parse_date(value, default=None):
    try:
        return datetime.date.fromisoformat(value or '')
    except ValueError:
        return default


def parse_time(value):
    try:
        return datetime.time.fromisoformat((value or '').strip()[:5])
    except ValueError:
        return None


def jp_date(d):
    return f'{d.month}月{d.day}日（{WEEK_JP[d.weekday()]}）'


def overlaps(facility, date, start, end, exclude_pk=None):
    """同じ日に時間の重なる訪問（中止を除く）。ヘルパーは 1 人なので、重なったら知らせる"""
    qs = (Visit.objects.filter(facility=facility, date=date, start_time__lt=end, end_time__gt=start)
          .exclude(status=Visit.STATUS_CANCELLED).select_related('beneficiary'))
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return list(qs)


def overlap_message(found):
    return '、'.join(f'{v.time_label} {v.beneficiary.full_name}さん' for v in found)


# ---------------------------------------------------------------- きょうの訪問・訪問予定
class DayView(KyotakuMixin, View):
    """きょうの訪問（ホーム）。?d=YYYY-MM-DD でほかの日"""

    def get(self, request):
        today = datetime.date.today()
        day = parse_date(request.GET.get('d'), today)
        visits = list(Visit.objects.filter(facility=self.facility, date=day).select_related('beneficiary'))
        return render(request, 'homecare/day.html', {
            'day': day, 'day_label': jp_date(day), 'today': today, 'visits': visits,
            'prev': day - datetime.timedelta(days=1), 'next': day + datetime.timedelta(days=1),
            'left': sum(1 for v in visits if v.status == Visit.STATUS_PLANNED),
        })


class MonthNowView(KyotakuMixin, View):
    def get(self, request):
        t = datetime.date.today()
        return redirect('homecare:month', year=t.year, month=t.month)


class MonthView(KyotakuMixin, View):
    """訪問予定（月のカレンダー。日曜はじまり）。?b=利用者 で絞る"""

    def get(self, request, year, month):
        year, month = month_or_404(year, month)
        first = datetime.date(year, month, 1)
        last = datetime.date(year, month, calendar.monthrange(year, month)[1])
        qs = Visit.objects.filter(facility=self.facility, date__range=(first, last)).select_related('beneficiary')
        who = None
        if request.GET.get('b'):
            who = Beneficiary.objects.filter(facility=self.facility, pk=request.GET.get('b')).first()
            if who:
                qs = qs.filter(beneficiary=who)
        by_day = {}
        for v in qs:
            by_day.setdefault(v.date, []).append(v)
        weeks = [[{'date': d, 'in_month': d.month == month, 'visits': by_day.get(d, [])}
                  for d in week] for week in calendar.Calendar(firstweekday=6).monthdatescalendar(year, month)]
        prev = first - datetime.timedelta(days=1)
        nxt = last + datetime.timedelta(days=1)
        active = [v for v in qs if v.status != Visit.STATUS_CANCELLED]
        return render(request, 'homecare/month.html', {
            'year': year, 'month': month, 'weeks': weeks, 'today': datetime.date.today(),
            'prev': prev, 'next': nxt, 'people': self.people(), 'who': who,
            'count': len(active), 'hours': sum(v.planned_minutes for v in active) / 60,
        })


class VisitFormView(KyotakuMixin, View):
    """訪問の予定を入れる（pk なし）・直す（pk あり）。新しく入れるときは「この月の同じ曜日にも入れる」ができる"""

    def get(self, request, pk=None):
        visit = self.visit_or_404(pk) if pk else None
        initial = {}
        if visit is None:
            initial = {'date': parse_date(request.GET.get('d'), datetime.date.today()), 'beneficiary': request.GET.get('b', ''),
                       'start_time': '10:00', 'end_time': '11:00', 'service_type': 'body'}
            last = (Visit.objects.filter(facility=self.facility, beneficiary_id=request.GET.get('b') or 0)
                    .order_by('-date', '-pk').first()) if request.GET.get('b', '').isdigit() else None
            if last:           # 同じ人の前の訪問の時刻と種類を初めの値に
                initial.update(start_time=f'{last.start_time:%H:%M}', end_time=f'{last.end_time:%H:%M}',
                               service_type=last.service_type)
        return self._render(request, visit, initial)

    def _render(self, request, visit, initial):
        if visit is not None:
            initial = {'date': visit.date, 'beneficiary': str(visit.beneficiary_id), 'start_time': f'{visit.start_time:%H:%M}',
                       'end_time': f'{visit.end_time:%H:%M}', 'service_type': visit.service_type, 'plan_note': visit.plan_note}
        return self._render_errors(request, visit, initial, [])

    def post(self, request, pk=None):
        visit = self.visit_or_404(pk) if pk else None
        p = request.POST
        if visit is not None and p.get('action') == 'delete':
            back = reverse('homecare:month', args=[visit.date.year, visit.date.month])
            label = f'{jp_date(visit.date)} {visit.time_label} {visit.beneficiary.full_name}さん'
            visit.delete()
            messages.success(request, f'{label}の訪問を消しました。')
            return redirect(back)
        b = Beneficiary.objects.filter(facility=self.facility, pk=p.get('beneficiary') or 0).first()
        date, start, end = parse_date(p.get('date')), parse_time(p.get('start_time')), parse_time(p.get('end_time'))
        kind = p.get('service_type') if p.get('service_type') in dict(SERVICE_TYPES) else 'body'
        errors = []
        if b is None:
            errors.append('利用者を選んでください。')
        if date is None:
            errors.append('日付を入れてください。')
        if start is None or end is None:
            errors.append('開始と終了の時刻を入れてください。')
        elif end <= start:
            errors.append('終了は開始より後の時刻にしてください。')
        values = {'beneficiary': p.get('beneficiary', ''), 'date': date or p.get('date'), 'start_time': p.get('start_time', ''),
                  'end_time': p.get('end_time', ''), 'service_type': kind, 'plan_note': (p.get('plan_note') or '').strip()[:200]}
        if errors:
            return self._render_errors(request, visit, values, errors)
        dates = [date]
        if visit is None and p.get('repeat') == '1':      # この月の同じ曜日（この日より後）にも
            d = date + datetime.timedelta(days=7)
            while d.month == date.month:
                dates.append(d)
                d += datetime.timedelta(days=7)
        made, clashes = [], []
        for d in dates:
            clash = overlaps(self.facility, d, start, end, exclude_pk=visit.pk if visit else None)
            if clash:
                clashes.append(f'{jp_date(d)}：{overlap_message(clash)}')
            if visit is None:
                made.append(Visit.objects.create(facility=self.facility, beneficiary=b, date=d, start_time=start, end_time=end,
                                                 service_type=kind, plan_note=values['plan_note'], staff=request.user))
        if visit is not None:
            visit.beneficiary, visit.date, visit.start_time, visit.end_time = b, date, start, end
            visit.service_type, visit.plan_note = kind, values['plan_note']
            visit.save()
            messages.success(request, f'{jp_date(date)} {visit.time_label} {b.full_name}さんの訪問を直しました。')
        else:
            messages.success(request, f'{b.full_name}さんの訪問を {len(made)} 件入れました'
                             + (f'（{"・".join(f"{x.date.day}日" for x in made)}）' if len(made) > 1 else f'（{jp_date(date)} {made[0].time_label}）') + '。')
        if clashes:
            messages.warning(request, 'ほかの訪問と時間が重なっています（ヘルパーは 1 人です）：' + ' ／ '.join(clashes))
        return redirect('homecare:month', year=date.year, month=date.month)

    def _render_errors(self, request, visit, values, errors):
        return render(request, 'homecare/visit_form.html', {
            'visit': visit, 'v': values, 'people': self.people(), 'types': SERVICE_TYPES, 'errors': errors})


# ---------------------------------------------------------------- サービス提供記録
class RecordView(KyotakuMixin, View):
    """1 回の訪問のサービス提供記録（実際の時刻・したこと・体調・様子）。「中止にする」もここ"""

    def get(self, request, pk):
        visit = self.visit_or_404(pk)
        return self._render(request, visit)

    def _render(self, request, visit):
        profile = HomeCareProfile.objects.filter(beneficiary=visit.beneficiary).first()
        prev = (Visit.objects.filter(facility=self.facility, beneficiary=visit.beneficiary, status=Visit.STATUS_DONE)
                .exclude(pk=visit.pk).filter(date__lte=visit.date).order_by('-date', '-start_time').first())
        return render(request, 'homecare/record.html', {
            'visit': visit, 'profile': profile, 'prev': prev, 'task_groups': TASK_GROUPS,
            'checked': set(visit.tasks or []), 'conditions': Visit.CONDITION_CHOICES, 'day_label': jp_date(visit.date),
            'as_value': f'{visit.actual_start or visit.start_time:%H:%M}', 'ae_value': f'{visit.actual_end or visit.end_time:%H:%M}',
        })

    def post(self, request, pk):
        visit = self.visit_or_404(pk)
        p = request.POST
        if p.get('action') == 'cancel':
            visit.status = Visit.STATUS_CANCELLED
            visit.cancel_reason = (p.get('cancel_reason') or '').strip()[:200]
            visit.save(update_fields=['status', 'cancel_reason', 'updated_at'])
            messages.info(request, f'{jp_date(visit.date)} {visit.beneficiary.full_name}さんの訪問を中止にしました。')
            return redirect(f"{reverse('homecare:day')}?d={visit.date:%Y-%m-%d}")
        if p.get('action') == 'reopen':
            visit.status = Visit.STATUS_PLANNED
            visit.save(update_fields=['status', 'updated_at'])
            messages.info(request, '予定に戻しました。')
            return redirect('homecare:record', pk=visit.pk)
        start, end = parse_time(p.get('actual_start')), parse_time(p.get('actual_end'))
        if start is None or end is None or end <= start:
            messages.error(request, '実際の開始と終了の時刻を入れてください（終了は開始より後）。')
            return self._render(request, visit)
        visit.actual_start, visit.actual_end = start, end
        visit.tasks = [t for t in p.getlist('tasks') if t in TASK_LABELS]
        visit.temperature = _decimal(p.get('temperature'), 30, 45)
        visit.bp_high, visit.bp_low = _int(p.get('bp_high'), 40, 300), _int(p.get('bp_low'), 20, 200)
        visit.pulse = _int(p.get('pulse'), 20, 250)
        visit.condition = p.get('condition') if p.get('condition') in dict(Visit.CONDITION_CHOICES) else ''
        visit.body = (p.get('body') or '').strip()[:TEXT_MAX]
        visit.status = Visit.STATUS_DONE
        visit.staff = visit.staff or request.user
        visit.recorded_at = timezone.now()
        visit.save()
        messages.success(request, f'{jp_date(visit.date)} {visit.beneficiary.full_name}さんのサービス提供記録を保存しました。')
        return redirect(f"{reverse('homecare:day')}?d={visit.date:%Y-%m-%d}")


def _int(value, lo, hi):
    try:
        v = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return v if lo <= v <= hi else None


def _decimal(value, lo, hi):
    from decimal import Decimal, InvalidOperation
    try:
        v = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return v.quantize(Decimal('0.1')) if lo <= v <= hi else None


TIDY_PROMPT = """あなたは居宅介護（ホームヘルプ）のヘルパーを手伝うAIです。
ヘルパーが話した言葉（音声入力の文字）を、サービス提供記録の「様子・特記事項」に書く書き言葉に整えるだけの作業をします。
- 話に無いことは書かない。AI の見解・評価・助言・一般論を足さない。「〜が見られた」「穏やかに」などの飾りも足さない
- 内容・事実・順番・程度は変えない。言いよどみ・くだけた言葉・くり返しは消し、文末は「〜した」「〜だった」の断定の形にする
- 本人・家族の言葉の「」の中は話したとおりに残す
- 句読点を付け、話の区切りで改行する。要約・箇条書き・見出し・英語・絵文字・マークダウンは使わない
- 返すのは整えた文だけ"""


class TidyView(KyotakuMixin, View):
    """様子・特記事項：話した言葉を書き言葉に整える（整文だけ）。{'text'} → {'text', 'added'?}"""

    def post(self, request):
        import json

        from ai_assist import asis
        from ai_assist.quick import ask_ai
        from ai_assist.text import clean_ai_text
        try:
            text = (json.loads(request.body.decode('utf-8') or '{}').get('text') or '').strip()[:TEXT_MAX]
        except (ValueError, AttributeError):
            text = ''
        if not text:
            return JsonResponse({'error': '整える文がありません。'}, status=400)
        raw, error = ask_ai(TIDY_PROMPT, f'【ヘルパーが話したこと】\n{text}', 2000)
        if error is not None:
            return error
        out = clean_ai_text(raw)
        return JsonResponse({'text': out, **asis.check(text, out)})


class RecordListView(KyotakuMixin, View):
    """サービス提供記録の一覧（月・利用者で絞る）"""

    def get(self, request):
        today = datetime.date.today()
        ym = request.GET.get('ym') or f'{today:%Y-%m}'
        try:
            year, month = (int(x) for x in ym.split('-'))
            year, month = month_or_404(year, month)
        except (ValueError, Http404):
            year, month = today.year, today.month
        first = datetime.date(year, month, 1)
        last = datetime.date(year, month, calendar.monthrange(year, month)[1])
        qs = Visit.objects.filter(facility=self.facility, date__range=(first, last)).select_related('beneficiary')
        who = Beneficiary.objects.filter(facility=self.facility, pk=request.GET.get('b') or 0).first()
        if who:
            qs = qs.filter(beneficiary=who)
        visits = list(qs.order_by('-date', '-start_time'))
        return render(request, 'homecare/records.html', {
            'visits': visits, 'year': year, 'month': month, 'ym': f'{year}-{month:02d}', 'who': who, 'people': self.people(),
            'done': sum(1 for v in visits if v.is_recorded),
            'waiting': [v for v in visits if v.status == Visit.STATUS_PLANNED and v.date <= today],
        })


class RecordPrintView(KyotakuMixin, View):
    """サービス提供記録の印刷（利用者 × 月）"""

    def get(self, request, year, month, beneficiary_pk):
        from config.pdf import pdf_or_html
        year, month = month_or_404(year, month)
        b = self.person_or_404(beneficiary_pk)
        first = datetime.date(year, month, 1)
        last = datetime.date(year, month, calendar.monthrange(year, month)[1])
        visits = list(Visit.objects.filter(facility=self.facility, beneficiary=b, date__range=(first, last),
                                           status=Visit.STATUS_DONE).order_by('date', 'start_time'))
        return pdf_or_html(request, 'homecare/pdf/records.html', {
            'b': b, 'year': year, 'month': month, 'visits': visits, 'facility': self.facility, 'week': WEEK_JP,
        }, f'{year}年{month}月_サービス提供記録_{b.full_name}')


# ---------------------------------------------------------------- 利用者ごとの設定
class PersonView(KyotakuMixin, View):
    """利用者ごとの居宅介護の設定（障害支援区分・支給量・援助の留意点）と、計画・今月の訪問"""

    def get(self, request, pk):
        b = self.person_or_404(pk)
        profile, _ = HomeCareProfile.objects.get_or_create(beneficiary=b)
        today = datetime.date.today()
        visits = list(Visit.objects.filter(facility=self.facility, beneficiary=b, date__year=today.year,
                                           date__month=today.month).order_by('date', 'start_time'))
        return render(request, 'homecare/person.html', {
            'b': b, 'profile': profile, 'levels': SUPPORT_LEVELS,
            'grant_rows': [(k, label, profile.grant_of(k)) for k, label in SERVICE_TYPES],
            'plans': CarePlan.objects.filter(facility=self.facility, beneficiary=b), 'visits': visits, 'today': today,
        })

    def post(self, request, pk):
        b = self.person_or_404(pk)
        profile, _ = HomeCareProfile.objects.get_or_create(beneficiary=b)
        p = request.POST
        profile.support_level = p.get('support_level') if p.get('support_level') in dict(SUPPORT_LEVELS) else ''
        grants = {}
        for k, _label in SERVICE_TYPES:
            try:
                v = round(float(p.get(f'grant_{k}') or 0) * 2) / 2
            except ValueError:
                v = 0
            if 0 < v <= 744:
                grants[k] = v
        profile.grants = grants
        profile.cautions = (p.get('cautions') or '').strip()[:TEXT_MAX]
        profile.save()
        messages.success(request, f'{b.full_name}さんの居宅介護の設定を保存しました。')
        return redirect('homecare:person', pk=b.pk)


# ---------------------------------------------------------------- 居宅介護計画
PLAN_TEXT_FIELDS = ('wishes', 'situation', 'issues', 'long_goal', 'short_goal', 'notes')


class PlanListView(KyotakuMixin, View):
    def get(self, request):
        people = list(self.people())
        latest = {}
        for plan in CarePlan.objects.filter(facility=self.facility).order_by('beneficiary_id', '-created_on', '-pk'):
            latest.setdefault(plan.beneficiary_id, plan)
        today = datetime.date.today()
        rows = [{'b': b, 'plan': latest.get(b.pk),
                 'expired': bool(latest.get(b.pk) and latest[b.pk].period_end and latest[b.pk].period_end < today)}
                for b in people]
        return render(request, 'homecare/plans.html', {'rows': rows, 'today': today})


class PlanFormView(KyotakuMixin, View):
    """居宅介護計画を作る（?b=利用者。前の計画があれば写して始める）・直す"""

    def get(self, request, pk=None):
        if pk:
            plan = get_object_or_404(CarePlan, pk=pk, facility=self.facility)
        else:
            b = self.person_or_404(request.GET.get('b') or 0)
            prev = CarePlan.objects.filter(facility=self.facility, beneficiary=b).first()
            today = datetime.date.today()
            plan = CarePlan(facility=self.facility, beneficiary=b, created_on=today, period_start=today,
                            period_end=_add_months(today, 6) - datetime.timedelta(days=1))
            if prev:
                for f in PLAN_TEXT_FIELDS + ('services',):
                    setattr(plan, f, getattr(prev, f))
        return self._render(request, plan)

    def _render(self, request, plan):
        return render(request, 'homecare/plan_form.html', {
            'plan': plan, 'b': plan.beneficiary, 'types': SERVICE_TYPES, 'rows': plan.service_rows(),
            'profile': HomeCareProfile.objects.filter(beneficiary=plan.beneficiary).first(),
        })

    def post(self, request, pk=None):
        p = request.POST
        if pk:
            plan = get_object_or_404(CarePlan, pk=pk, facility=self.facility)
            if p.get('action') == 'delete':
                b = plan.beneficiary
                plan.delete()
                messages.success(request, f'{b.full_name}さんの居宅介護計画（{plan.created_on:%Y/%-m/%-d}）を消しました。')
                return redirect('homecare:person', pk=b.pk)
        else:
            plan = CarePlan(facility=self.facility, beneficiary=self.person_or_404(p.get('beneficiary') or 0),
                            created_by=request.user)
        plan.created_on = parse_date(p.get('created_on'), datetime.date.today())
        plan.period_start, plan.period_end = parse_date(p.get('period_start')), parse_date(p.get('period_end'))
        plan.explained_on = parse_date(p.get('explained_on'))
        plan.agreed_by = (p.get('agreed_by') or '').strip()[:100]
        for f in PLAN_TEXT_FIELDS:
            setattr(plan, f, (p.get(f) or '').strip()[:TEXT_MAX])
        rows = []
        for i in range(CarePlan.SERVICE_ROWS):
            row = {k: (p.get(f's{i}_{k}') or '').strip()[:200] for k in ('days', 'start', 'end', 'type', 'content')}
            if row['type'] not in dict(SERVICE_TYPES):
                row['type'] = ''
            if any(row.values()):
                rows.append(row)
        plan.services = rows
        plan.save()
        messages.success(request, f'{plan.beneficiary.full_name}さんの居宅介護計画を保存しました。')
        return redirect('homecare:plan_edit', pk=plan.pk)


def _add_months(d, n):
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return datetime.date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


class PlanPrintView(KyotakuMixin, View):
    def get(self, request, pk):
        from config.pdf import pdf_or_html
        plan = get_object_or_404(CarePlan.objects.select_related('beneficiary'), pk=pk, facility=self.facility)
        return pdf_or_html(request, 'homecare/pdf/plan.html', {
            'plan': plan, 'b': plan.beneficiary, 'facility': self.facility, 'rows': plan.service_rows(),
            'profile': HomeCareProfile.objects.filter(beneficiary=plan.beneficiary).first(),
        }, f'居宅介護計画_{plan.beneficiary.full_name}_{plan.created_on:%Y%m%d}')


# ---------------------------------------------------------------- 実績記録票
def jisseki_rows(facility, beneficiary, year, month):
    """実績記録票の行（実施と中止の訪問）と、サービスの種類ごとの合計（算定時間）"""
    first = datetime.date(year, month, 1)
    last = datetime.date(year, month, calendar.monthrange(year, month)[1])
    visits = list(Visit.objects.filter(facility=facility, beneficiary=beneficiary, date__range=(first, last))
                  .exclude(status=Visit.STATUS_PLANNED, date__gte=datetime.date.today())
                  .order_by('date', 'start_time'))
    totals = {k: 0.0 for k, _ in SERVICE_TYPES}
    for v in visits:
        if v.status == Visit.STATUS_DONE:
            totals[v.service_type] += v.billing_hours
    return visits, totals


class JisseikiNowView(KyotakuMixin, View):
    def get(self, request):
        t = datetime.date.today()
        return redirect('homecare:jisseki', year=t.year, month=t.month)


class JisseikiView(KyotakuMixin, View):
    """実績記録票：月の利用者ごとの合計（算定時間の目安）と支給量。印刷は利用者ごと"""

    def get(self, request, year, month):
        year, month = month_or_404(year, month)
        labels = dict(SERVICE_TYPES)
        rows = []
        for b in self.people():
            visits, totals = jisseki_rows(self.facility, b, year, month)
            profile = HomeCareProfile.objects.filter(beneficiary=b).first()
            items = []
            for k, label in SERVICE_TYPES:
                grant = profile.grant_of(k) if profile else 0
                if totals[k] or grant:
                    items.append({'label': labels[k], 'used': totals[k], 'grant': grant, 'over': bool(grant) and totals[k] > grant})
            rows.append({'b': b, 'items': items, 'done': sum(1 for v in visits if v.status == Visit.STATUS_DONE),
                         'unrecorded': sum(1 for v in visits if v.status == Visit.STATUS_PLANNED)})
        first = datetime.date(year, month, 1)
        prev = first - datetime.timedelta(days=1)
        nxt = datetime.date(year, month, calendar.monthrange(year, month)[1]) + datetime.timedelta(days=1)
        return render(request, 'homecare/jisseki.html', {'rows': rows, 'year': year, 'month': month, 'prev': prev, 'next': nxt})


class JisseikiPrintView(KyotakuMixin, View):
    def get(self, request, year, month, beneficiary_pk):
        from config.pdf import pdf_or_html
        year, month = month_or_404(year, month)
        b = self.person_or_404(beneficiary_pk)
        visits, totals = jisseki_rows(self.facility, b, year, month)
        profile = HomeCareProfile.objects.filter(beneficiary=b).first()
        cert = b.latest_certificate
        blank = max(0, 24 - len(visits))
        return pdf_or_html(request, 'homecare/pdf/jisseki.html', {
            'b': b, 'year': year, 'month': month, 'visits': visits, 'facility': self.facility, 'cert': cert,
            'week': WEEK_JP, 'blank_rows': range(blank),
            'totals': [(label, totals[k], profile.grant_of(k) if profile else 0) for k, label in SERVICE_TYPES
                       if totals[k] or (profile and profile.grant_of(k))],
        }, f'{year}年{month}月_実績記録票_{b.full_name}')

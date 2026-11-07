"""
相談支援の計画：一覧・入力（基本情報／計画／週間の 3 つの面）・AI の下書き・PDF（申請者の現状・様式2-1・様式2-2）。
施設設定で「相談支援の計画を使う」にした事業所だけ。
"""
import datetime
import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from beneficiaries.models import Beneficiary
from config.concurrency import check_conflict
from config.pdf import pdf_or_html

from .models import CHILD_STATE_KEYS, FAMILY_KEYS, ROW_KEYS, SERVICE_KEYS, WEEKDAYS, ConsultationPlan

logger = logging.getLogger(__name__)
ROW_BLANK = 3
FAMILY_BLANK = 2
SERVICE_BLANK = 2
WEEK_BLANK = 3


class ConsultationMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        facility = getattr(request.user, 'facility', None)
        if request.user.is_authenticated and not (facility is not None and facility.use_consultation):
            messages.info(request, 'この事業所では相談支援の計画を使わない設定になっています（施設設定の「使う機能」で変えられます）。')
            return redirect('facilities:dashboard')
        return super().dispatch(request, *args, **kwargs)


def _plan_or_404(request, pk):
    return get_object_or_404(ConsultationPlan.objects.select_related('beneficiary'), pk=pk, facility=request.user.facility)


def _date(s):
    try:
        return datetime.date.fromisoformat((s or '').strip())
    except ValueError:
        return None


def _age(dob, on=None):
    on = on or datetime.date.today()
    return on.year - dob.year - ((on.month, on.day) < (dob.month, dob.day)) if dob else ''


class IndexView(ConsultationMixin, View):
    def get(self, request):
        facility = request.user.facility
        plans = ConsultationPlan.objects.filter(facility=facility).select_related('beneficiary')
        return render(request, 'consultation/index.html', {
            'plans': plans,
            'beneficiaries': Beneficiary.objects.filter(facility=facility, status=Beneficiary.STATUS_ACTIVE).order_by('last_name_kana', 'first_name_kana'),
        })


class EditView(ConsultationMixin, View):
    """新規（?beneficiary=ID）と編集。1 画面に 3 つの面（基本情報・計画・週間計画表）"""
    template_name = 'consultation/form.html'

    def _ctx(self, request, cp):
        rows = cp.row_list() if cp else []
        rows += [{k: '' for k, _ in ROW_KEYS}] * ROW_BLANK
        family = [f for f in (cp.family if cp else []) if isinstance(f, dict)] + [{k: '' for k, _ in FAMILY_KEYS}] * FAMILY_BLANK
        services = [x for x in (cp.services if cp else []) if isinstance(x, dict)] + [{k: '' for k, _ in SERVICE_KEYS}] * SERVICE_BLANK
        weekly = {}
        for key, _label in WEEKDAYS:
            weekly[key] = (cp.weekly_rows(key) if cp else []) + [{'time': '', 'text': ''}] * WEEK_BLANK
        from django.conf import settings
        return {
            'cp': cp, 'b': cp.beneficiary if cp else None, 'facility': request.user.facility,
            'child_state_keys': CHILD_STATE_KEYS, 'child_state': (cp.child_state if cp else {}) or {},
            'rows': rows, 'row_keys': ROW_KEYS, 'family': family, 'family_keys': FAMILY_KEYS,
            'services': services, 'service_keys': SERVICE_KEYS, 'weekdays': WEEKDAYS, 'weekly': weekly,
            'status_choices': ConsultationPlan.STATUS_CHOICES, 'today': datetime.date.today(),
            'ai_enabled': bool(settings.ANTHROPIC_API_KEY), 'new_office': request.user.facility.name,
            'beneficiaries': Beneficiary.objects.filter(facility=request.user.facility, status=Beneficiary.STATUS_ACTIVE).order_by('last_name_kana', 'first_name_kana'),
        }

    def get(self, request, pk=None):
        cp = _plan_or_404(request, pk) if pk else None
        ctx = self._ctx(request, cp)
        if cp is None:
            b = Beneficiary.objects.filter(facility=request.user.facility, pk=request.GET.get('beneficiary') or 0).first()
            ctx['b'] = b
        return render(request, self.template_name, ctx)

    def post(self, request, pk=None):
        facility = request.user.facility
        p = request.POST
        if pk:
            cp = _plan_or_404(request, pk)
            conflict = check_conflict(request, cp)
            if conflict:
                messages.error(request, conflict)
                return redirect('consultation:edit', pk=pk)
        else:
            b = get_object_or_404(Beneficiary, pk=p.get('beneficiary') or 0, facility=facility)
            cp = ConsultationPlan(facility=facility, beneficiary=b, created_by=request.user)
        for f in ('office_name', 'planner', 'monitoring_period', 'support_class', 'copay_cap', 'cert_number', 'child_cert_number'):
            setattr(cp, f, p.get(f, '').strip()[:100])
        for f in ('overview', 'history', 'medical', 'complaint_child', 'complaint_family', 'wishes', 'policy', 'long_goal', 'short_goal',
                  'weekly_other', 'life_summary'):
            setattr(cp, f, p.get(f, '').strip())
        cp.plan_date = _date(p.get('plan_date'))
        cp.consent_date = _date(p.get('consent_date'))
        if p.get('status') in dict(ConsultationPlan.STATUS_CHOICES):
            cp.status = p['status']
        cp.child_state = {k: p.get(f'cs_{k}', '').strip() for k, _ in CHILD_STATE_KEYS}
        cp.family = _table(p, 'fam', FAMILY_KEYS)
        cp.services = _table(p, 'sv', SERVICE_KEYS)
        cp.rows = _table(p, 'row', ROW_KEYS)
        weekly = {}
        for key, _label in WEEKDAYS:
            items = _table(p, f'wk_{key}', [('time', ''), ('text', '')])
            if items:
                weekly[key] = items
        cp.weekly = weekly
        cp.save()
        messages.success(request, '相談支援の計画を保存しました。')
        nxt = p.get('next')
        if nxt in ('basic', 'plan', 'weekly'):
            return redirect(f"{reverse('consultation:pdf_' + nxt, args=[cp.pk])}?fmt=html")
        return redirect('consultation:edit', pk=cp.pk)


def _table(post, prefix, keys, max_rows=60):
    """行ごとの入力（prefix_<i>_<key>）を [dict] に。全部空の行は捨てる"""
    out = []
    for i in range(max_rows):
        if not any(f'{prefix}_{i}_{k}' in post for k, _ in keys):
            continue
        row = {k: post.get(f'{prefix}_{i}_{k}', '').strip()[:1000] for k, _ in keys}
        if any(row.values()):
            out.append(row)
    return out


class DeleteView(ConsultationMixin, View):
    def post(self, request, pk):
        cp = _plan_or_404(request, pk)
        cp.delete()
        messages.success(request, '相談支援の計画を削除しました。')
        return redirect('consultation:index')


class AiDraftView(ConsultationMixin, View):
    """材料から申請者の現状・計画の下書き（JSON。画面側で欄に入れる）"""

    def post(self, request, pk):
        from django.conf import settings

        from ai_assist import trial

        from . import ai
        cp = _plan_or_404(request, pk)
        if not settings.ANTHROPIC_API_KEY:
            return JsonResponse({'error': 'ANTHROPIC_API_KEY が設定されていません。'}, status=500)
        over = trial.check(request.user.facility)
        if over:
            return JsonResponse({'error': over}, status=403)
        try:
            months = max(1, min(24, int(request.POST.get('months', 6))))
        except ValueError:
            months = 6
        try:
            data, n = ai.draft(cp, months=months)
        except Exception as e:  # noqa: BLE001
            logger.exception('相談支援の計画の下書きでエラー')
            return JsonResponse({'error': f'AIでの処理中にエラーが発生しました: {type(e).__name__}: {e}'}, status=500)
        if not data:
            return JsonResponse({'error': f'直近 {months} か月の記録も、アセスメント・書類・個別支援計画もありません。'}, status=404)
        trial.use(request.user.facility)
        return JsonResponse({'draft': data, 'records': n, 'months': months})


def _pdf_ctx(request, cp):
    b = cp.beneficiary
    return {'cp': cp, 'b': b, 'facility': request.user.facility, 'age': _age(b.date_of_birth), 'weekdays': WEEKDAYS,
            'child_state_rows': cp.child_state_rows(), 'rows': cp.row_list(min_rows=3),
            'family': [f for f in (cp.family or []) if isinstance(f, dict)] or [{}] * 3,
            'services': [x for x in (cp.services or []) if isinstance(x, dict)] or [{}] * 3,
            'hours': list(range(6, 24)) + [0, 1, 2, 3, 4, 5]}


class BasicPdfView(ConsultationMixin, View):
    def get(self, request, pk):
        cp = _plan_or_404(request, pk)
        return pdf_or_html(request, 'consultation/pdf/basic.html', _pdf_ctx(request, cp), f'申請者の現状_{cp.beneficiary.full_name}')


class PlanPdfView(ConsultationMixin, View):
    def get(self, request, pk):
        cp = _plan_or_404(request, pk)
        return pdf_or_html(request, 'consultation/pdf/plan.html', _pdf_ctx(request, cp), f'サービス等利用計画_{cp.beneficiary.full_name}')


class WeeklyPdfView(ConsultationMixin, View):
    def get(self, request, pk):
        cp = _plan_or_404(request, pk)
        ctx = _pdf_ctx(request, cp)
        ctx['weekly'] = {k: cp.weekly_rows(k) for k, _ in WEEKDAYS}
        return pdf_or_html(request, 'consultation/pdf/weekly.html', ctx, f'週間計画表_{cp.beneficiary.full_name}')

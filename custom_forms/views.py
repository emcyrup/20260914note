"""
事業所様式（はぴねす様式）の作成・編集・PDF 出力
施設設定の「帳票様式」が「はぴねす様式」の施設だけが使える。
"""
import urllib.parse
from datetime import date, datetime

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from beneficiaries.models import Beneficiary
from esignatures.models import EsignatureRecord
from facilities.context_processors import get_terms
from facilities.models import Facility
from support_plans.models import PlanGoal, SupportPlan

from .models import AgencyMeetingReport, SpecializedSupportPlan

# 詳細版の行（支援区分・支援項目）
DETAIL_ROWS = [
    ('self_health',     '本人支援', '健康・生活'),
    ('self_motor_move', '本人支援', '運動・感覚（移動・歩行）'),
    ('self_motor_flex', '本人支援', '運動・感覚（柔軟性・装具）'),
    ('self_cognition',  '本人支援', '認知・行動'),
    ('self_language',   '本人支援', '言語・コミュニケーション'),
    ('self_social',     '本人支援', '人間関係・社会性'),
    ('family',          '家族支援', '保護者相談・レスパイト・家庭ケア連携'),
    ('transition',      '移行支援', '学校生活適応／将来の進学・地域移行'),
    ('community',       '地域支援・地域連携', '主治医・学校・短期入所・相談支援機関連携'),
]
DETAIL_ROW_LABELS = {k: f'{a}／{b}' for k, a, b in DETAIL_ROWS}
PLAN_EXTRA_FIELDS = [
    ('usage_form', '利用形態', '例：放課後等デイサービス（月・水・金曜日 週3回）'),
    ('specialists', '担当専門職', '例：理学療法士、保育士'),
    ('child_wishes', '本人の希望・意向', ''),
    ('guardian_wishes', '保護者の希望・意向', ''),
    ('service_hours', '支援の標準的な提供時間等（曜日・頻度、時間）', '例：月・水・金 15:00〜17:30'),
    ('medical_care', '医療的ケア・配慮事項・機関連携', '主治医連携／医療的ケア等／関係機関連携'),
    ('review_cycle', '計画見直し周期', '例：原則6ヶ月毎（必要時随時）'),
    ('explain_method', '説明方法', '例：面談／オンライン'),
    ('relation', '児童との続柄', '例：母'),
    ('delivery_method', '交付方法', '例：面談時手渡し／郵送／電磁的記録'),
]
GOAL_EXTRA_FIELDS = [
    ('category', '支援区分・支援項目（詳細版の行）'),
    ('item', '項目（別紙1）'),
    ('timing', 'おおよその達成時期（文章）'),
    ('procedure', '支援の手順・配慮事項・環境調整'),
    ('staff', '担当者・提供機関／担当職種'),
    ('notes', '留意事項（本人の役割を含む）'),
    ('criteria', '達成基準・評価区分'),
]


class FormSetMixin(LoginRequiredMixin):
    """はぴねす様式の施設だけが使える"""

    def dispatch(self, request, *args, **kwargs):
        facility = getattr(request.user, 'facility', None)
        if request.user.is_authenticated and (facility is None or facility.form_set != Facility.FORM_SET_HAPPINESS):
            messages.info(request, 'この事業所では事業所様式を使わない設定です（施設設定 → 帳票様式 で変更できます）。')
            return redirect('reports:index')
        return super().dispatch(request, *args, **kwargs)


def _pdf_or_html(request, template, ctx, filename):
    """?fmt=html なら画面表示（印刷確認）、それ以外は PDF"""
    ctx = dict(ctx, pdf=request.GET.get('fmt') != 'html')
    html = render(request, template, ctx).content.decode('utf-8')
    if not ctx['pdf']:
        return HttpResponse(html)
    try:
        from weasyprint import HTML
        pdf = HTML(string=html, base_url=request.build_absolute_uri('/')).write_pdf()
    except (ImportError, OSError) as e:
        return HttpResponse(f'PDF を作成できません（サーバーに PDF 用ライブラリがありません）: {e}\n「画面で見る」から印刷してください。',
                            status=500, content_type='text/plain; charset=utf-8')
    res = HttpResponse(pdf, content_type='application/pdf')
    res['Content-Disposition'] = f'attachment; filename="form.pdf"; filename*=UTF-8\'\'{urllib.parse.quote(filename)}.pdf'
    return res


def _date(s):
    try:
        return datetime.strptime(s or '', '%Y-%m-%d').date()
    except ValueError:
        return None


def _time(s):
    try:
        return datetime.strptime(s or '', '%H:%M').time()
    except ValueError:
        return None


# =============================================
# 一覧
# =============================================
class IndexView(FormSetMixin, View):
    def get(self, request):
        facility = request.user.facility
        return render(request, 'custom_forms/index.html', {
            'meetings': AgencyMeetingReport.objects.filter(facility=facility).select_related('beneficiary')[:30],
            'specialized': SpecializedSupportPlan.objects.filter(facility=facility).select_related('beneficiary')[:30],
            'plans': SupportPlan.objects.filter(facility=facility).exclude(status=SupportPlan.STATUS_CLOSED).select_related('beneficiary'),
        })


# =============================================
# 関係機関連携加算Ⅱ 報告書
# =============================================
class MeetingEditView(FormSetMixin, View):
    def _ctx(self, request, obj):
        facility = request.user.facility
        return {
            'obj': obj,
            'beneficiaries': Beneficiary.objects.filter(facility=facility).order_by('status', 'last_name_kana', 'first_name_kana'),
            'staff': facility.staff_accounts.filter(is_active=True),
            'formats': AgencyMeetingReport.FORMAT_CHOICES,
            'participants': (obj.participants if obj else []) + [{'affiliation': '', 'name': ''}] * 8,
            'today': date.today(),
        }

    def get(self, request, pk=None):
        obj = get_object_or_404(AgencyMeetingReport, pk=pk, facility=request.user.facility) if pk else None
        ctx = self._ctx(request, obj)
        ctx['participants'] = ctx['participants'][:8]
        return render(request, 'custom_forms/meeting_form.html', ctx)

    def post(self, request, pk=None):
        facility = request.user.facility
        obj = get_object_or_404(AgencyMeetingReport, pk=pk, facility=facility) if pk else AgencyMeetingReport(facility=facility)
        p = request.POST
        beneficiary = Beneficiary.objects.filter(facility=facility, pk=p.get('beneficiary')).first()
        d = _date(p.get('date'))
        if not beneficiary or not d:
            messages.error(request, f'{get_terms(request.user)["beneficiary"]}と会議開催日を入力してください。')
            return redirect(request.path)
        obj.beneficiary = beneficiary
        obj.date = d
        obj.start_time, obj.end_time = _time(p.get('start_time')), _time(p.get('end_time'))
        obj.place = p.get('place', '').strip()[:200]
        obj.format = p.get('format') if p.get('format') in dict(AgencyMeetingReport.FORMAT_CHOICES) else 'face'
        obj.participants = [{'affiliation': a.strip()[:100], 'name': n.strip()[:100]}
                            for a, n in zip(p.getlist('p_affiliation'), p.getlist('p_name')) if a.strip() or n.strip()]
        for f in ('purpose', 'result', 'opinions', 'policy'):
            setattr(obj, f, p.get(f, '').strip())
        obj.recorder = facility.staff_accounts.filter(pk=p.get('recorder')).first() or request.user
        obj.save()
        messages.success(request, f'{beneficiary.full_name} の関係機関連携報告書（{d}）を保存しました。')
        return redirect('custom_forms:index')


class MeetingDeleteView(FormSetMixin, View):
    def post(self, request, pk):
        obj = get_object_or_404(AgencyMeetingReport, pk=pk, facility=request.user.facility)
        obj.delete()
        messages.success(request, '関係機関連携報告書を削除しました。')
        return redirect('custom_forms:index')


class MeetingPdfView(FormSetMixin, View):
    def get(self, request, pk):
        obj = get_object_or_404(AgencyMeetingReport.objects.select_related('beneficiary', 'recorder'), pk=pk, facility=request.user.facility)
        return _pdf_or_html(request, 'custom_forms/pdf/meeting.html', {'obj': obj, 'facility': request.user.facility},
                            f'関係機関連携報告書_{obj.beneficiary.full_name}_{obj.date:%Y%m%d}')


# =============================================
# 専門的支援実施計画書
# =============================================
class SpecializedEditView(FormSetMixin, View):
    def _ctx(self, request, obj):
        facility = request.user.facility
        return {
            'obj': obj, 'M': SpecializedSupportPlan,
            'beneficiaries': Beneficiary.objects.filter(facility=facility).order_by('status', 'last_name_kana', 'first_name_kana'),
            'staff': facility.staff_accounts.filter(is_active=True),
            'movements': [(f, l, getattr(obj, f) if obj else '') for f, l in SpecializedSupportPlan.MOVEMENTS],
        }

    def get(self, request, pk=None):
        obj = get_object_or_404(SpecializedSupportPlan, pk=pk, facility=request.user.facility) if pk else None
        return render(request, 'custom_forms/specialized_form.html', self._ctx(request, obj))

    def post(self, request, pk=None):
        facility = request.user.facility
        obj = get_object_or_404(SpecializedSupportPlan, pk=pk, facility=facility) if pk else SpecializedSupportPlan(facility=facility)
        p = request.POST
        beneficiary = Beneficiary.objects.filter(facility=facility, pk=p.get('beneficiary')).first()
        if not beneficiary:
            messages.error(request, f'{get_terms(request.user)["beneficiary"]}を選んでください。')
            return redirect(request.path)
        obj.beneficiary = beneficiary
        obj.period_start, obj.period_end = _date(p.get('period_start')), _date(p.get('period_end'))
        for f in ('wishes', 'key_areas', 'goals', 'implementation'):
            setattr(obj, f, p.get(f, '').strip())
        for f in ('pain_site', 'cardio', 'other_physical', 'other_movement', 'support_other', 'explained_to'):
            setattr(obj, f, p.get(f, '').strip()[:200])
        obj.rom_parts = [x for x in p.getlist('rom_parts') if x in SpecializedSupportPlan.ROM_PARTS]
        obj.weak_parts = [x for x in p.getlist('weak_parts') if x in SpecializedSupportPlan.WEAK_PARTS]
        obj.balance = p.get('balance') if p.get('balance') in ('yes', 'no') else ''
        obj.muscle_tone = p.get('muscle_tone') if p.get('muscle_tone') in ('high', 'low') else ''
        assist = dict(SpecializedSupportPlan.ASSIST_CHOICES)
        for f, _ in SpecializedSupportPlan.MOVEMENTS:
            setattr(obj, f, p.get(f) if p.get(f) in assist else '')
        obj.abms = {k: p.get(f'abms_{k}', '').strip()[:20] for k, _ in SpecializedSupportPlan.ABMS_ITEMS}
        obj.abms_t = {k: p.get(f'abms_t_{k}', '').strip()[:20] for k, _ in SpecializedSupportPlan.ABMS_T_ITEMS}
        obj.support_items = [x for x in p.getlist('support_items') if x in SpecializedSupportPlan.SUPPORT_ITEMS]
        obj.explained_date = _date(p.get('explained_date'))
        obj.explained_by = facility.staff_accounts.filter(pk=p.get('explained_by')).first()
        obj.save()
        messages.success(request, f'{beneficiary.full_name} の専門的支援実施計画書を保存しました。')
        return redirect('custom_forms:index')


class SpecializedDeleteView(FormSetMixin, View):
    def post(self, request, pk):
        obj = get_object_or_404(SpecializedSupportPlan, pk=pk, facility=request.user.facility)
        obj.delete()
        messages.success(request, '専門的支援実施計画書を削除しました。')
        return redirect('custom_forms:index')


class SpecializedPdfView(FormSetMixin, View):
    def get(self, request, pk):
        obj = get_object_or_404(SpecializedSupportPlan.objects.select_related('beneficiary', 'explained_by'), pk=pk, facility=request.user.facility)
        ctx = {
            'obj': obj, 'M': SpecializedSupportPlan, 'facility': request.user.facility,
            'abms_rows': [(l, (obj.abms or {}).get(k, '')) for k, l in SpecializedSupportPlan.ABMS_ITEMS],
            'abms_t_rows': [(l, (obj.abms_t or {}).get(k, '')) for k, l in SpecializedSupportPlan.ABMS_T_ITEMS],
        }
        return _pdf_or_html(request, 'custom_forms/pdf/specialized.html', ctx,
                            f'専門的支援実施計画書_{obj.beneficiary.full_name}')


# =============================================
# 個別支援計画書（別紙1／詳細版）
# =============================================
def _plan_or_404(request, pk):
    return get_object_or_404(SupportPlan.objects.select_related('beneficiary', 'manager'), pk=pk, facility=request.user.facility)


def _plan_context(request, plan):
    goals = list(plan.goals.order_by('goal_type', 'order', 'pk'))
    consent = plan.get_step(SupportPlan.STEP_CONSENT)
    signatures = list(EsignatureRecord.objects.filter(target_type='support_plan', target_id=plan.pk).order_by('signed_at'))
    sig = signatures[0] if signatures else None
    consent_date = consent.consent_date if consent and consent.consent_method == 'paper' else (sig.signed_at.date() if sig else None)
    return {
        'plan': plan, 'b': plan.beneficiary, 'facility': request.user.facility, 'x': plan.form_extra or {},
        'assessment': plan.get_step(SupportPlan.STEP_ASSESSMENT), 'draft': plan.get_step(SupportPlan.STEP_DRAFT),
        'meeting': plan.get_step(SupportPlan.STEP_MEETING), 'consent': consent, 'monitoring': plan.get_step(SupportPlan.STEP_MONITORING),
        'goals': goals, 'long_goals': [g for g in goals if g.goal_type == PlanGoal.TYPE_LONG],
        'short_goals': [g for g in goals if g.goal_type == PlanGoal.TYPE_SHORT],
        'signature': sig, 'consent_date': consent_date, 'today': date.today(),
        'relation': (plan.form_extra or {}).get('relation') or (sig.relationship if sig else ''),
        'age': _age(plan.beneficiary.date_of_birth),
    }


def _age(dob, today=None):
    today = today or date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day)) if dob else ''


class PlanExtraView(FormSetMixin, View):
    """様式の追加項目（計画・目標ごと）を編集する"""

    def get(self, request, pk):
        plan = _plan_or_404(request, pk)
        return render(request, 'custom_forms/plan_extra_form.html', {
            'plan': plan, 'x': plan.form_extra or {}, 'fields': PLAN_EXTRA_FIELDS, 'goal_fields': GOAL_EXTRA_FIELDS,
            'goals': plan.goals.order_by('goal_type', 'order', 'pk'), 'detail_rows': DETAIL_ROWS,
        })

    def post(self, request, pk):
        plan = _plan_or_404(request, pk)
        p = request.POST
        plan.form_extra = {k: p.get(k, '').strip() for k, _, _ in PLAN_EXTRA_FIELDS}
        plan.save(update_fields=['form_extra'])
        valid_cats = {k for k, _, _ in DETAIL_ROWS}
        for g in plan.goals.all():
            extra = {k: p.get(f'g{g.pk}_{k}', '').strip() for k, _ in GOAL_EXTRA_FIELDS}
            if extra.get('category') not in valid_cats:
                extra['category'] = ''
            g.form_extra = extra
            g.save(update_fields=['form_extra'])
        messages.success(request, '様式の追加項目を保存しました。')
        return redirect('support_plans:detail', pk=plan.pk)


class PlanSheet1View(FormSetMixin, View):
    """個別支援計画書（別紙1）"""

    def get(self, request, pk):
        plan = _plan_or_404(request, pk)
        ctx = _plan_context(request, plan)
        rows = []
        for i, g in enumerate(ctx['goals'], 1):
            e = g.form_extra or {}
            rows.append({
                'item': e.get('item') or g.get_goal_type_display(), 'goal': g.content,
                'content': g.support_content + (f'（{g.frequency}）' if g.frequency else ''),
                'timing': e.get('timing') or (g.target_date.strftime('%Y/%m') if g.target_date else ''),
                'staff': e.get('staff') or request.user.facility.name, 'notes': e.get('notes', ''), 'priority': str(i),
            })
        rows += [{'item': '', 'goal': '', 'content': '', 'timing': '', 'staff': '', 'notes': '', 'priority': ''}] * max(0, 6 - len(rows))
        ctx['rows'] = rows
        x = ctx['x']
        b = plan.beneficiary
        ctx['service_hours'] = x.get('service_hours') or (f'{b.scheduled_weekdays_display}曜日' if b.scheduled_weekdays_display else '')
        d = ctx['draft']
        ctx['created_on'] = d.completed_at.date() if d and d.completed_at else plan.created_at.date()
        return _pdf_or_html(request, 'custom_forms/pdf/plan_sheet1.html', ctx, f'個別支援計画書_別紙1_{b.full_name}')


class PlanDetailFormView(FormSetMixin, View):
    """放課後等デイサービス 個別支援計画書（詳細版）"""

    def get(self, request, pk):
        plan = _plan_or_404(request, pk)
        ctx = _plan_context(request, plan)
        by_cat = {}
        for g in ctx['goals']:
            by_cat.setdefault((g.form_extra or {}).get('category', ''), []).append(g)
        rows = []
        for key, division, item in DETAIL_ROWS:
            gs = by_cat.get(key, [])
            if not gs:
                rows.append({'division': division, 'item': item, 'goal': None, 'e': {}, 'priority': ''})
            for g in gs:
                rows.append({'division': division, 'item': item, 'goal': g, 'e': g.form_extra or {},
                             'priority': str(ctx['goals'].index(g) + 1)})
        ctx['rows'] = rows
        ctx['unassigned'] = by_cat.get('', [])
        d, m = ctx['draft'], ctx['monitoring']
        ctx['created_on'] = d.completed_at.date() if d and d.completed_at else plan.created_at.date()
        prev = SupportPlan.objects.filter(beneficiary=plan.beneficiary, created_at__lt=plan.created_at).order_by('-created_at').first()
        ctx['prev_created_on'] = prev.created_at.date() if prev else None
        ctx['review_cycle'] = ctx['x'].get('review_cycle') or (f'原則{m.interval_months}ヶ月毎（必要時随時）' if m else '')
        ctx['next_monitoring'] = plan.next_monitoring_due if plan.status == SupportPlan.STATUS_ACTIVE else None
        return _pdf_or_html(request, 'custom_forms/pdf/plan_detail.html', ctx, f'個別支援計画書_詳細版_{plan.beneficiary.full_name}')

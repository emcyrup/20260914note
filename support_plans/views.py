from datetime import date

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from beneficiaries.models import Beneficiary
from esignatures.models import EsignatureRecord

from .forms import (
    AssessmentForm, ConsentDeliveryForm, MonitoringRecordForm, MonitoringSettingForm, PlanDraftForm, PlanGoalForm,
    StaffMeetingForm, SupportPlanForm,
)
from .models import MonitoringRecord, PlanGoal, SupportPlan
from config.pdf import media_url_fetcher
from config.utils import to_int
from config.concurrency import STEP_KINDS, check_conflict

STEP_FORMS = {
    SupportPlan.STEP_ASSESSMENT: AssessmentForm,
    SupportPlan.STEP_DRAFT:      PlanDraftForm,
    SupportPlan.STEP_MEETING:    StaffMeetingForm,
    SupportPlan.STEP_CONSENT:    ConsentDeliveryForm,
    SupportPlan.STEP_MONITORING: MonitoringSettingForm,
}


class PlanMixin(LoginRequiredMixin):
    """自施設の計画だけを扱う"""

    def get_plan(self, pk):
        return get_object_or_404(
            SupportPlan.objects.select_related('beneficiary', 'manager'),
            pk=pk, facility=self.request.user.facility,
        )


# =============================================
# 一覧
# =============================================
class PlanListView(LoginRequiredMixin, View):
    def get(self, request):
        facility = request.user.facility
        status = request.GET.get('status', 'open')
        qs = SupportPlan.objects.filter(facility=facility).select_related('beneficiary', 'manager')
        if status == 'open':
            qs = qs.exclude(status=SupportPlan.STATUS_CLOSED)
        elif status in dict(SupportPlan.STATUS_CHOICES):
            qs = qs.filter(status=status)
        plans = list(qs)
        overdue = [p for p in plans if p.monitoring_overdue]
        # 計画が1つもない在籍中の利用者
        no_plan = Beneficiary.objects.filter(facility=facility, status=Beneficiary.STATUS_ACTIVE).annotate(
            n=Count('support_plans', filter=~Q(support_plans__status=SupportPlan.STATUS_CLOSED))
        ).filter(n=0)
        return render(request, 'support_plans/list.html', {
            'plans': plans, 'status': status, 'overdue': overdue, 'no_plan': no_plan,
            'steps': SupportPlan.STEPS, 'today': date.today(),
        })


# =============================================
# 新規作成
# =============================================
class PlanCreateView(LoginRequiredMixin, View):
    def _beneficiaries(self, request):
        return Beneficiary.objects.filter(facility=request.user.facility, status=Beneficiary.STATUS_ACTIVE)

    def _default_title(self, beneficiary):
        n = beneficiary.support_plans.count() + 1
        return f'第{n}期 個別支援計画'

    def get(self, request):
        b_id = request.GET.get('beneficiary')
        beneficiary = self._beneficiaries(request).filter(pk=b_id).first() if b_id else None
        form = SupportPlanForm(facility=request.user.facility,
                               initial={'title': self._default_title(beneficiary) if beneficiary else '第1期 個別支援計画'})
        return render(request, 'support_plans/form.html', {
            'form': form, 'beneficiaries': self._beneficiaries(request), 'selected': beneficiary,
        })

    def post(self, request):
        beneficiary = get_object_or_404(self._beneficiaries(request), pk=to_int(request.POST.get('beneficiary'), -1))
        form = SupportPlanForm(request.POST, facility=request.user.facility)
        if not form.is_valid():
            return render(request, 'support_plans/form.html', {
                'form': form, 'beneficiaries': self._beneficiaries(request), 'selected': beneficiary,
            })
        plan = form.save(commit=False)
        plan.facility = request.user.facility
        plan.beneficiary = beneficiary
        plan.created_by = request.user
        plan.save()
        for n, _ in SupportPlan.STEPS:
            plan.get_step(n)
        messages.success(request, f'{beneficiary.full_name} さんの「{plan.title}」を作成しました。ステップ1 アセスメントから始めてください。')
        return redirect('support_plans:step', pk=plan.pk, n=SupportPlan.STEP_ASSESSMENT)


# =============================================
# 詳細（進捗の全体像）
# =============================================
class PlanDetailView(PlanMixin, View):
    def get(self, request, pk):
        plan = self.get_plan(pk)
        return render(request, 'support_plans/detail.html', {
            'plan': plan, 'progress': plan.step_progress(), 'today': date.today(),
            'goals': plan.goals.all(),
            'monitoring_records': plan.monitoring_records.all()[:5],
            'successor': getattr(plan, 'successor', None),
        })


# =============================================
# 各ステップ
# =============================================
class PlanStepView(PlanMixin, View):
    template_name = 'support_plans/step.html'

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        self.plan = self.get_plan(kwargs['pk'])
        self.n = kwargs['n']
        if self.n not in dict(SupportPlan.STEPS):
            return redirect('support_plans:detail', pk=self.plan.pk)
        if not self.plan.can_open_step(self.n):
            prev = dict(SupportPlan.STEPS)[self.n - 1]
            messages.warning(request, f'ステップ{self.n - 1}「{prev}」が完了していないため、ステップ{self.n} はまだ開けません。')
            return redirect('support_plans:step', pk=self.plan.pk, n=self.plan.current_step)
        self.step = self.plan.get_step(self.n)
        self.readonly = self.plan.is_step_done(self.n) or self.plan.status == SupportPlan.STATUS_CLOSED
        return super().dispatch(request, *args, **kwargs)

    def _context(self, form, **extra):
        plan = self.plan
        ctx = {
            'plan': plan, 'n': self.n, 'step': self.step, 'form': form, 'readonly': self.readonly,
            'label': dict(SupportPlan.STEPS)[self.n],
            'description': SupportPlan.STEP_DESCRIPTIONS[self.n],
            'progress': plan.step_progress(),
            'requirements': self.step.requirements(),
            'today': date.today(),
            'can_reopen': plan.can_reopen_step(self.n),
            'is_last': self.n == SupportPlan.STEP_MONITORING,
            'ai_enabled': bool(settings.ANTHROPIC_API_KEY),
        }
        if self.n == SupportPlan.STEP_DRAFT:
            edit_id = self.request.GET.get('goal')
            editing = plan.goals.filter(pk=edit_id).first() if edit_id else None
            ctx['goals_long']  = plan.goals.filter(goal_type=PlanGoal.TYPE_LONG)
            ctx['goals_short'] = plan.goals.filter(goal_type=PlanGoal.TYPE_SHORT)
            ctx['editing_goal'] = editing
            ctx['goal_form'] = extra.pop('goal_form', None) or PlanGoalForm(instance=editing, facility=plan.facility)
            ctx['assessment'] = plan.get_step(SupportPlan.STEP_ASSESSMENT)
            from datetime import timedelta
            ctx['draft_start'] = date.today() - timedelta(days=91)
            ctx['draft_end'] = date.today()
        if self.n == SupportPlan.STEP_MEETING:
            ctx['goals'] = plan.goals.all()
        if self.n == SupportPlan.STEP_CONSENT:
            ctx['signatures'] = EsignatureRecord.objects.filter(target_type='support_plan', target_id=plan.pk, facility=plan.facility)
            ctx['guardians'] = plan.beneficiary.guardians.all()
        if self.n == SupportPlan.STEP_MONITORING:
            ctx['records'] = plan.monitoring_records.select_related('conducted_by')
            ctx['record_form'] = extra.pop('record_form', None) or MonitoringRecordForm(
                facility=plan.facility,
                initial={'date': date.today(), 'conducted_by': self.request.user, 'next_due': self._suggest_next_due()},
            )
            ctx['goals'] = plan.goals.all()
            ctx['successor'] = getattr(plan, 'successor', None)
            ctx['next_due'] = plan.next_monitoring_due
            ctx['overdue'] = plan.monitoring_overdue
        ctx['step'] = self.step
        ctx['step_kind'] = STEP_KINDS.get(self.n, '')
        ctx.update(extra)
        return ctx

    def _suggest_next_due(self):
        from .models import _add_months
        return _add_months(date.today(), self.step.interval_months)

    def get(self, request, pk, n):
        form = STEP_FORMS[n](instance=self.step, facility=self.plan.facility)
        if self.readonly:
            for field in form.fields.values():
                field.widget.attrs['disabled'] = True
        return render(request, self.template_name, self._context(form))

    def post(self, request, pk, n):
        if self.readonly:
            messages.info(request, 'このステップは完了済みです。修正する場合は「完了を取り消して修正」を押してください。')
            return redirect('support_plans:step', pk=pk, n=n)
        conflict = check_conflict(request, self.step)
        if conflict:
            form = STEP_FORMS[n](request.POST, instance=STEP_FORMS[n].Meta.model.objects.get(pk=self.step.pk), facility=self.plan.facility)
            messages.error(request, conflict)
            return render(request, self.template_name, self._context(form, conflict=conflict))
        form = STEP_FORMS[n](request.POST, instance=self.step, facility=self.plan.facility)
        if not form.is_valid():
            messages.error(request, '入力内容に誤りがあります。')
            return render(request, self.template_name, self._context(form))
        form.save()
        action = request.POST.get('action', 'save')
        if action == 'complete':
            remaining = self.plan.complete_step(n, request.user)
            if remaining:
                messages.warning(request, 'まだ完了できません。残り：' + '、'.join(remaining))
                return redirect('support_plans:step', pk=pk, n=n)
            label = dict(SupportPlan.STEPS)[n]
            if n < SupportPlan.STEP_MONITORING:
                nxt = dict(SupportPlan.STEPS)[n + 1]
                messages.success(request, f'ステップ{n}「{label}」を完了しました。次はステップ{n + 1}「{nxt}」です。')
                return redirect('support_plans:step', pk=pk, n=n + 1)
        messages.success(request, '保存しました。')
        return redirect('support_plans:step', pk=pk, n=n)


class PlanStepReopenView(PlanMixin, View):
    def post(self, request, pk, n):
        plan = self.get_plan(pk)
        if not plan.can_reopen_step(n):
            messages.error(request, 'このステップは取り消せません（直前に完了したステップだけ修正できます）。')
            return redirect('support_plans:detail', pk=pk)
        plan.reopen_step(n)
        messages.info(request, f'ステップ{n}「{dict(SupportPlan.STEPS)[n]}」の完了を取り消しました。修正後にもう一度完了してください。')
        return redirect('support_plans:step', pk=pk, n=n)


# =============================================
# ステップ2：目標
# =============================================
class GoalSaveView(PlanMixin, View):
    def post(self, request, pk, goal_pk=None):
        plan = self.get_plan(pk)
        if plan.current_step != SupportPlan.STEP_DRAFT:
            messages.error(request, '目標はステップ2「計画（原案）の作成」の間だけ編集できます。')
            return redirect('support_plans:detail', pk=pk)
        goal = get_object_or_404(plan.goals, pk=goal_pk) if goal_pk else None
        form = PlanGoalForm(request.POST, instance=goal, facility=plan.facility)
        conflict = check_conflict(request, goal) if goal else None
        if conflict or not form.is_valid():
            view = PlanStepView()
            view.request, view.plan, view.n = request, plan, SupportPlan.STEP_DRAFT
            view.step, view.readonly = plan.get_step(SupportPlan.STEP_DRAFT), False
            draft_form = PlanDraftForm(instance=view.step, facility=plan.facility)
            if conflict:
                goal = PlanGoal.objects.get(pk=goal.pk)   # 版の表示は DB の最新に合わせる
                messages.error(request, conflict)
            else:
                messages.error(request, '目標の入力内容に誤りがあります。')
            return render(request, view.template_name, view._context(draft_form, goal_form=form, editing_goal=goal, goal_conflict=conflict))
        g = form.save(commit=False)
        g.plan = plan
        if not goal_pk:
            g.order = plan.goals.filter(goal_type=g.goal_type).count()
        g.save()
        messages.success(request, f'{g.get_goal_type_display()}を{"更新" if goal_pk else "追加"}しました。')
        return redirect('support_plans:step', pk=pk, n=SupportPlan.STEP_DRAFT)


class GoalDeleteView(PlanMixin, View):
    def post(self, request, pk, goal_pk):
        plan = self.get_plan(pk)
        if plan.current_step != SupportPlan.STEP_DRAFT:
            messages.error(request, '目標はステップ2の間だけ削除できます。')
            return redirect('support_plans:detail', pk=pk)
        goal = get_object_or_404(plan.goals, pk=goal_pk)
        goal.delete()
        messages.success(request, '目標を削除しました。')
        return redirect('support_plans:step', pk=pk, n=SupportPlan.STEP_DRAFT)


# =============================================
# ステップ5：モニタリング記録
# =============================================
class MonitoringRecordCreateView(PlanMixin, View):
    def post(self, request, pk):
        plan = self.get_plan(pk)
        if plan.current_step != SupportPlan.STEP_MONITORING or plan.status == SupportPlan.STATUS_CLOSED:
            messages.error(request, 'モニタリングはステップ5に進んでから記録できます。')
            return redirect('support_plans:detail', pk=pk)
        form = MonitoringRecordForm(request.POST, facility=plan.facility)
        if not form.is_valid():
            view = PlanStepView()
            view.request, view.plan, view.n = request, plan, SupportPlan.STEP_MONITORING
            view.step, view.readonly = plan.get_step(SupportPlan.STEP_MONITORING), False
            setting_form = MonitoringSettingForm(instance=view.step, facility=plan.facility)
            messages.error(request, 'モニタリング記録の入力内容に誤りがあります。')
            return render(request, view.template_name, view._context(setting_form, record_form=form))
        rec = form.save(commit=False)
        rec.plan = plan
        rec.save()
        step = plan.get_step(SupportPlan.STEP_MONITORING)
        if not step.completed_at:
            step.completed_at = timezone.now()
            step.completed_by = request.user
            step.save(update_fields=['completed_at', 'completed_by', 'updated_at'])
        if rec.review_needed:
            messages.warning(request, 'モニタリングを記録しました。見直しが必要と判断したので「次の計画を作成」から新しい計画を始められます。')
        else:
            messages.success(request, f'モニタリングを記録しました。次回期限：{rec.next_due or "未設定"}')
        return redirect('support_plans:step', pk=pk, n=SupportPlan.STEP_MONITORING)


class MonitoringRecordDeleteView(PlanMixin, View):
    def post(self, request, pk, record_pk):
        plan = self.get_plan(pk)
        rec = get_object_or_404(plan.monitoring_records, pk=record_pk)
        rec.delete()
        messages.success(request, 'モニタリング記録を削除しました。')
        return redirect('support_plans:step', pk=pk, n=SupportPlan.STEP_MONITORING)


# =============================================
# 見直し → 次の計画
# =============================================
class SuccessorCreateView(PlanMixin, View):
    def post(self, request, pk):
        plan = self.get_plan(pk)
        if hasattr(plan, 'successor') and plan.successor:
            return redirect('support_plans:detail', pk=plan.successor.pk)
        if plan.current_step != SupportPlan.STEP_MONITORING:
            messages.error(request, '次の計画はモニタリング（ステップ5）まで進んでから作成できます。')
            return redirect('support_plans:detail', pk=pk)
        n = plan.beneficiary.support_plans.count() + 1
        new = SupportPlan.objects.create(
            facility=plan.facility, beneficiary=plan.beneficiary, title=f'第{n}期 個別支援計画',
            manager=plan.manager, predecessor=plan, created_by=request.user,
        )
        for s, _ in SupportPlan.STEPS:
            new.get_step(s)
        # 前回のアセスメントを下書きとして引き継ぐ
        old_a, new_a = plan.get_step(1), new.get_step(1)
        new_a.condition, new_a.environment, new_a.wishes = old_a.condition, old_a.environment, old_a.wishes
        new_a.save()
        messages.success(request, f'「{new.title}」を作成しました。前回のアセスメント内容を下書きとして引き継いでいます。ステップ1 から見直してください。')
        return redirect('support_plans:step', pk=new.pk, n=SupportPlan.STEP_ASSESSMENT)


# =============================================
# 印刷用（交付する計画書）
# =============================================
def _print_context(plan):
    return {
        'plan': plan,
        'assessment': plan.get_step(1), 'draft': plan.get_step(2), 'meeting': plan.get_step(3),
        'consent': plan.get_step(4),
        'goals': plan.goals.all(),
        'goals_long': plan.goals.filter(goal_type=PlanGoal.TYPE_LONG),
        'goals_short': plan.goals.filter(goal_type=PlanGoal.TYPE_SHORT),
        'signatures': EsignatureRecord.objects.filter(target_type='support_plan', target_id=plan.pk, facility=plan.facility),
        'records': plan.monitoring_records.select_related('conducted_by').order_by('date'),
        'facility': plan.facility, 'today': date.today(),
    }


def _pdf_response(request, template, ctx, ascii_name, utf8_name):
    """WeasyPrint で PDF を返す（画像は絶対URLで解決）"""
    import urllib.parse

    from django.http import HttpResponse
    try:
        from weasyprint import HTML
    except (ImportError, OSError) as e:
        return HttpResponse(f'PDF を作成できません（WeasyPrint が使えません）: {e}', status=500, content_type='text/plain; charset=utf-8')

    ctx = dict(ctx, pdf=True)
    html = render(request, template, ctx).content.decode('utf-8')
    try:
        from config.pdf import render_pdf
        pdf = render_pdf(html, base_url=request.build_absolute_uri('/'))
    except OSError as e:  # サーバーに WeasyPrint の共有ライブラリ（pango 等）が無い
        return HttpResponse(f'PDF を作成できません（サーバーに PDF 用ライブラリがありません）: {e}', status=500, content_type='text/plain; charset=utf-8')
    res = HttpResponse(pdf, content_type='application/pdf')
    res['Content-Disposition'] = f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{urllib.parse.quote(utf8_name)}'
    return res


class PlanPdfView(PlanMixin, View):
    """個別支援計画書 PDF"""

    def get(self, request, pk):
        plan = self.get_plan(pk)
        return _pdf_response(request, 'support_plans/print.html', _print_context(plan),
                             f'support_plan_{plan.pk}.pdf', f'個別支援計画書_{plan.beneficiary.full_name}_{plan.title}.pdf')


class MonitoringReportView(PlanMixin, View):
    """モニタリング報告書（HTML 表示・印刷）"""

    def get(self, request, pk):
        plan = self.get_plan(pk)
        return render(request, 'support_plans/monitoring_report.html', _print_context(plan))


class MonitoringReportPdfView(PlanMixin, View):
    def get(self, request, pk):
        plan = self.get_plan(pk)
        return _pdf_response(request, 'support_plans/monitoring_report.html', _print_context(plan),
                             f'monitoring_{plan.pk}.pdf', f'モニタリング報告書_{plan.beneficiary.full_name}_{plan.title}.pdf')


class PlanPrintView(PlanMixin, View):
    def get(self, request, pk):
        plan = self.get_plan(pk)
        return render(request, 'support_plans/print.html', {
            'plan': plan,
            'assessment': plan.get_step(1), 'draft': plan.get_step(2), 'meeting': plan.get_step(3),
            'consent': plan.get_step(4),
            'goals_long': plan.goals.filter(goal_type=PlanGoal.TYPE_LONG),
            'goals_short': plan.goals.filter(goal_type=PlanGoal.TYPE_SHORT),
            'signatures': EsignatureRecord.objects.filter(target_type='support_plan', target_id=plan.pk, facility=plan.facility),
            'facility': plan.facility, 'today': date.today(),
        })


# =============================================
# AI 支援：アセスメント下書き・原案下書き・根拠の記録
# =============================================
class AssessmentDraftView(PlanMixin, View):
    """過去の日誌からアセスメントの下書き（JSON を返し、画面側で入力欄に入れる）"""

    def post(self, request, pk):
        from django.conf import settings
        from django.http import JsonResponse

        from . import ai

        plan = self.get_plan(pk)
        if plan.current_step != SupportPlan.STEP_ASSESSMENT:
            return JsonResponse({'error': 'アセスメントは完了済みです。'}, status=400)
        if not settings.ANTHROPIC_API_KEY:
            return JsonResponse({'error': 'ANTHROPIC_API_KEY が設定されていません。'}, status=500)
        from ai_assist import trial
        over = trial.check(request.user.facility)
        if over:
            return JsonResponse({'error': over}, status=403)
        try:
            months = max(1, min(24, int(request.POST.get('months', 6))))
        except ValueError:
            months = 6
        try:
            data, n = ai.assessment_draft(plan, months=months)
        except Exception as e:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).exception('アセスメント下書きでエラー')
            return JsonResponse({'error': f'AIでの処理中にエラーが発生しました: {type(e).__name__}: {e}'}, status=500)
        if not data:
            return JsonResponse({'error': f'直近 {months} か月の日誌がありません。'}, status=404)
        trial.use(request.user.facility)
        return JsonResponse({'draft': data, 'records': n, 'months': months})


class PlanDraftView(PlanMixin, View):
    """期間の日誌から原案（方針・目標）を下書きし、根拠の記録をひもづける"""

    def post(self, request, pk):
        from django.conf import settings

        from . import ai

        plan = self.get_plan(pk)
        if plan.current_step != SupportPlan.STEP_DRAFT:
            messages.error(request, '原案の下書きはステップ2の間だけ作れます。')
            return redirect('support_plans:detail', pk=pk)
        if not settings.ANTHROPIC_API_KEY:
            messages.error(request, 'ANTHROPIC_API_KEY が設定されていないため AI は使えません。')
            return redirect('support_plans:step', pk=pk, n=2)
        from ai_assist import trial
        over = trial.check(request.user.facility)
        if over:
            messages.error(request, over)
            return redirect('support_plans:step', pk=pk, n=2)
        start, end = _period_from_request(request)
        try:
            created, n = ai.plan_draft(plan, start, end)
        except Exception as e:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).exception('原案下書きでエラー')
            messages.error(request, f'AIでの処理中にエラーが発生しました: {type(e).__name__}: {e}')
            return redirect('support_plans:step', pk=pk, n=2)
        trial.use(request.user.facility)
        if n == 0:
            messages.warning(request, f'{start} 〜 {end} の日誌がないため下書きを作れませんでした。期間を広げてください。')
        else:
            messages.success(request, f'{start} 〜 {end} の日誌 {n} 件から、方針と目標 {created} 件の下書きを作りました。根拠の記録をひもづけています。内容を確認・修正してください（下書きのままでは計画になりません）。')
        return redirect('support_plans:step', pk=pk, n=2)


def _period_from_request(request):
    """期間（既定：今日から3か月前まで）"""
    from datetime import timedelta
    today = date.today()
    try:
        start = date.fromisoformat(request.POST.get('start') or request.GET.get('start') or '')
    except ValueError:
        start = today - timedelta(days=91)
    try:
        end = date.fromisoformat(request.POST.get('end') or request.GET.get('end') or '')
    except ValueError:
        end = today
    if start > end:
        start, end = end, start
    return start, end


class GoalEvidenceView(PlanMixin, View):
    """目標の根拠になった記録を選ぶ（期間内の日誌を関連度順に並べる）"""

    def get(self, request, pk, goal_pk):
        from . import ai

        plan = self.get_plan(pk)
        goal = get_object_or_404(plan.goals, pk=goal_pk)
        start, end = _period_from_request(request)
        candidates = ai.find_evidence(plan.beneficiary, goal.content + ' ' + goal.support_content, start, end, top_k=30)
        linked = set(goal.evidence_records.values_list('pk', flat=True))
        linked_records = goal.evidence_records.order_by('date')
        return render(request, 'support_plans/goal_evidence.html', {
            'plan': plan, 'goal': goal, 'start': start, 'end': end,
            'candidates': candidates, 'linked': linked, 'linked_records': linked_records,
            'readonly': plan.current_step != SupportPlan.STEP_DRAFT,
        })

    def post(self, request, pk, goal_pk):
        plan = self.get_plan(pk)
        goal = get_object_or_404(plan.goals, pk=goal_pk)
        if plan.current_step != SupportPlan.STEP_DRAFT:
            messages.error(request, '根拠の記録はステップ2の間だけ変更できます。')
            return redirect('support_plans:detail', pk=pk)
        from records.models import DailyRecord
        ids = request.POST.getlist('record_ids')
        goal.evidence_records.set(DailyRecord.objects.filter(pk__in=ids, beneficiary=plan.beneficiary))
        messages.success(request, f'「{goal.content}」の根拠として日誌 {goal.evidence_records.count()} 件をひもづけました。')
        return redirect('support_plans:step', pk=pk, n=2)

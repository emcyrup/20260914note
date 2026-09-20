"""
計画書中心の画面（シンプル）
  利用者一覧（カード）／利用者（プロフィール＋期ごとの6タブ）／完了期日一覧／スタッフ／保護者／連絡帳／施設
"""
from datetime import date

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import models as db_models
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View

from accounts.models import StaffAccount
from beneficiaries.forms import BeneficiaryForm
from beneficiaries.models import Beneficiary, Guardian, RecipientCertificate
from config.concurrency import check_conflict
from support_plans.models import SupportPlan

from . import services
from .forms import (ConsentForm, DraftForm, FacilityInfoForm, GuardianEditForm, ImportForm, InterviewForm,
                    MeetingForm, MonitoringForm, ProfileForm)
from .models import DOMAINS, GOAL_CATEGORIES, GOAL_FIELDS, ContactNote, Interview, WEEKDAYS


def _student_or_404(request, pk):
    return get_object_or_404(Beneficiary, pk=pk, facility=request.user.facility)


def _plan_or_404(request, plan_pk):
    return get_object_or_404(SupportPlan, pk=plan_pk, facility=request.user.facility)


def _active_filter(request, qs):
    """検索フォーム：氏名で絞り、既定では退所済みを含めない"""
    q = (request.GET.get('q') or '').strip()
    if q:
        qs = qs.filter(db_models.Q(last_name__icontains=q) | db_models.Q(first_name__icontains=q)
                       | db_models.Q(last_name_kana__icontains=q) | db_models.Q(first_name_kana__icontains=q))
    if request.GET.get('include_inactive') != '1':
        qs = qs.filter(status=Beneficiary.STATUS_ACTIVE)
    return qs


def _student_card(b, plan=None):
    plan = plan if plan is not None else services.latest_plan(b)
    cert = b.latest_certificate
    return {
        'beneficiary': b, 'plan': plan, 'period': services.period_number(plan) if plan else None,
        'next_update': services.next_update_date(plan), 'cert': cert,
        'stages': services.stage_status(plan),
    }


# =============================================
# 利用者一覧
# =============================================
class StudentListView(LoginRequiredMixin, View):
    def get(self, request):
        qs = _active_filter(request, Beneficiary.objects.filter(facility=request.user.facility))
        qs = services.prefetch_for_cards(qs.order_by('last_name_kana', 'first_name_kana', 'pk'))
        cards = [_student_card(b) for b in qs]
        return render(request, 'planbook/students.html', {
            'cards': cards, 'q': request.GET.get('q', ''), 'include_inactive': request.GET.get('include_inactive') == '1',
            'stages': services.STAGES,
        })


class StudentCreateView(LoginRequiredMixin, View):
    """「追加」：姓名と生年月日だけで作り、プロフィールへ"""

    def get(self, request):
        return render(request, 'planbook/student_new.html', {'form': BeneficiaryForm()})

    def post(self, request):
        form = BeneficiaryForm(request.POST)
        if not form.is_valid():
            messages.error(request, '入力内容を確認してください。')
            return render(request, 'planbook/student_new.html', {'form': form})
        b = form.save(commit=False)
        b.facility = request.user.facility
        b.save()
        messages.success(request, f'{b.full_name} さんを追加しました。続けてプロフィールを入力してください。')
        return redirect('planbook:student', pk=b.pk)


class StudentDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        b = _student_or_404(request, pk)
        if not (request.user.is_admin or request.user.is_superuser or request.user.is_child_dev_manager):
            messages.error(request, '利用者を削除できるのは管理者・児発管だけです。')
            return redirect('planbook:student', pk=pk)
        if b.support_plans.exists() or b.daily_records.exists():
            # 記録がある利用者は消さずに退所にする（計画・日誌は残す）
            b.status = Beneficiary.STATUS_INACTIVE
            b.discharge_date = b.discharge_date or date.today()
            b.save(update_fields=['status', 'discharge_date', 'updated_at'])
            messages.info(request, f'{b.full_name} さんは計画・記録があるため削除せず「退所」にしました。')
        else:
            name = b.full_name
            b.delete()
            messages.success(request, f'{name} さんを削除しました。')
        return redirect('planbook:students')


# =============================================
# 利用者（プロフィール＋期ごとのタブ）
# =============================================
def _student_context(request, b, plan, tab):
    plans = services.period_plans(b)
    periods = [{'n': i, 'plan': p, 'is_current': plan is not None and p.pk == plan.pk} for i, p in enumerate(plans, 1)]
    stages = services.stage_status(plan)
    cert = b.latest_certificate
    return {
        'beneficiary': b, 'plan': plan, 'periods': periods, 'tab': tab, 'stages': stages,
        'stage_list': services.STAGES, 'next_update': services.next_update_date(plan), 'cert': cert,
        'period_no': services.period_number(plan) if plan else None,
        'today': date.today(),
    }


class StudentView(LoginRequiredMixin, View):
    """プロフィールタブ（期がなければここだけ）"""

    def get(self, request, pk):
        b = _student_or_404(request, pk)
        plan = services.latest_plan(b)
        ctx = _student_context(request, b, plan, 'profile')
        cert = b.latest_certificate
        ctx.update({'form': ProfileForm(instance=b, facility=b.facility), 'guardians': b.guardians.all(),
                    'cert_number': cert.certificate_number if cert else '', 'cert_until': cert.valid_until if cert else None})
        return render(request, 'planbook/student.html', ctx)

    def post(self, request, pk):
        b = _student_or_404(request, pk)
        conflict = check_conflict(request, b)
        if conflict:
            messages.error(request, conflict)
            return redirect('planbook:student', pk=pk)
        form = ProfileForm(request.POST, instance=b, facility=b.facility)
        if not form.is_valid():
            messages.error(request, '入力内容に誤りがあります。')
            plan = services.latest_plan(b)
            ctx = _student_context(request, b, plan, 'profile')
            ctx.update({'form': form, 'guardians': b.guardians.all()})
            return render(request, 'planbook/student.html', ctx)
        form.save()
        # 受給者番号・期限（最新の受給者証を更新。なければ作る）
        number = (request.POST.get('cert_number') or '').strip()[:20]
        until = request.POST.get('cert_until') or ''
        if number or until:
            from planbook.services import _parse_date
            until_d = _parse_date(until)
            cert = b.latest_certificate
            if cert is None:
                cert = RecipientCertificate(beneficiary=b, granted_days=0, monthly_cap=0, valid_from=date.today(),
                                            valid_until=until_d or date.today())
            cert.certificate_number = number or cert.certificate_number
            if until_d:
                cert.valid_until = until_d
                if cert.valid_from > until_d:
                    cert.valid_from = until_d
            cert.save()
        messages.success(request, 'プロフィールを保存しました。')
        return redirect('planbook:student', pk=pk)


class PeriodCreateView(LoginRequiredMixin, View):
    def post(self, request, pk):
        b = _student_or_404(request, pk)
        plan = services.create_period(b, request.user)
        messages.success(request, f'第{services.period_number(plan)}期の計画を作りました。面談記録から始めてください。')
        return redirect('planbook:plan_tab', pk=pk, plan_pk=plan.pk, tab='interview')


TAB_FORMS = {'interview': InterviewForm, 'draft': DraftForm, 'meeting': MeetingForm, 'plan': ConsentForm}


class PlanTabView(LoginRequiredMixin, View):
    """期ごとの5タブ（モニタリング／面談記録／原案／スタッフ会議／計画書）"""

    def _obj(self, plan, tab):
        if tab == 'interview':
            return Interview.objects.get_or_create(plan=plan)[0]
        return plan.get_step(services.STAGE_STEP[tab])

    def _ctx(self, request, b, plan, tab, form=None):
        ctx = _student_context(request, b, plan, tab)
        if tab == 'monitoring':
            ctx['records'] = plan.monitoring_records.select_related('conducted_by')
            ctx['record_form'] = form or MonitoringForm(facility=plan.facility, initial={
                'date': date.today(), 'conducted_by': request.user, 'next_due': plan.next_monitoring_due})
            ctx['goals'] = [{'goal': g, 'x': services.goal_extra(g)} for g in plan.goals.all()]
            ctx['next_due'] = plan.next_monitoring_due
            ctx['overdue'] = plan.monitoring_overdue
            return ctx
        obj = self._obj(plan, tab)
        locked = obj.is_completed or plan.status == SupportPlan.STATUS_CLOSED
        f = form or TAB_FORMS[tab](instance=obj, facility=plan.facility)
        if locked:
            for field in f.fields.values():
                field.widget.attrs['disabled'] = True
        ctx.update({'obj': obj, 'form': f, 'locked': locked, 'stage': tab})
        if tab == 'interview':
            ctx['schedule_rows'] = obj.schedule_rows()
            ctx['weekdays'] = WEEKDAYS
            ctx['missing'] = obj.missing_for_completion()
            ctx['interview_rows'] = obj.interview_rows()
            ctx['prev_interview'] = None
            if plan.predecessor_id:
                ctx['prev_interview'] = Interview.objects.filter(plan_id=plan.predecessor_id).first()
        if tab in ('interview', 'draft'):
            ctx['goals'] = [{'goal': g, 'x': services.goal_extra(g)} for g in plan.goals.all()]
            ctx['goal_fields'] = GOAL_FIELDS
            ctx['goal_categories'] = GOAL_CATEGORIES
            ctx['domains'] = DOMAINS
        if tab == 'meeting':
            ctx['goals'] = [{'goal': g, 'x': services.goal_extra(g)} for g in plan.goals.all()]
        if tab == 'plan':
            from esignatures.models import EsignatureRecord
            ctx['signatures'] = EsignatureRecord.objects.filter(target_type='support_plan', target_id=plan.pk, facility=plan.facility)
            ctx['guardians'] = b.guardians.all()
            ctx['goals'] = [{'goal': g, 'x': services.goal_extra(g)} for g in plan.goals.all()]
            ctx['draft'] = plan.get_step(SupportPlan.STEP_DRAFT)
            ctx['interview'] = Interview.objects.filter(plan=plan).first()
        return ctx

    def get(self, request, pk, plan_pk, tab):
        b = _student_or_404(request, pk)
        plan = get_object_or_404(SupportPlan, pk=plan_pk, beneficiary=b)
        if tab not in dict(services.STAGES):
            return redirect('planbook:student', pk=pk)
        return render(request, 'planbook/student.html', self._ctx(request, b, plan, tab))

    def post(self, request, pk, plan_pk, tab):
        b = _student_or_404(request, pk)
        plan = get_object_or_404(SupportPlan, pk=plan_pk, beneficiary=b)
        if tab not in TAB_FORMS:
            return redirect('planbook:plan_tab', pk=pk, plan_pk=plan_pk, tab=tab)
        obj = self._obj(plan, tab)
        if obj.is_completed or plan.status == SupportPlan.STATUS_CLOSED:
            messages.info(request, 'この記録は完了（編集ロック）しています。修正するには「ロックを外す」を押してください。')
            return redirect('planbook:plan_tab', pk=pk, plan_pk=plan_pk, tab=tab)
        conflict = check_conflict(request, obj)
        if conflict:
            messages.error(request, conflict)
            return redirect('planbook:plan_tab', pk=pk, plan_pk=plan_pk, tab=tab)
        form = TAB_FORMS[tab](request.POST, instance=obj, facility=plan.facility)
        if not form.is_valid():
            messages.error(request, '入力内容に誤りがあります。')
            return render(request, 'planbook/student.html', self._ctx(request, b, plan, tab, form=form))
        obj = form.save()
        if tab == 'interview':
            if request.POST.get('copy_prev_schedule') == '1' and plan.predecessor_id:
                prev = Interview.objects.filter(plan_id=plan.predecessor_id).first()
                obj.schedule = dict(prev.schedule or {}) if prev else obj.schedule
            else:
                obj.schedule = Interview.clean_schedule(request.POST)
            obj.save(update_fields=['schedule', 'updated_at'])
            services.sync_interview_to_assessment(obj)
            if 'goal_idx' in request.POST:
                services.save_goals(plan, request.POST)
        if tab == 'draft' and 'goal_idx' in request.POST:
            services.save_goals(plan, request.POST)
        messages.success(request, '保存しました。')
        if request.POST.get('action') == 'complete':
            return StageCompleteView().post(request, plan_pk, tab)
        return redirect('planbook:plan_tab', pk=pk, plan_pk=plan_pk, tab=tab)


class GoalsSaveView(LoginRequiredMixin, View):
    def post(self, request, plan_pk):
        plan = _plan_or_404(request, plan_pk)
        n = services.save_goals(plan, request.POST)
        messages.success(request, f'支援内容を {n} 件保存しました。')
        return redirect('planbook:plan_tab', pk=plan.beneficiary_id, plan_pk=plan.pk, tab=request.POST.get('tab', 'draft'))


class StageCompleteView(LoginRequiredMixin, View):
    def post(self, request, plan_pk, stage):
        plan = _plan_or_404(request, plan_pk)
        if stage not in services.STAGE_STEP:
            return redirect('planbook:student', pk=plan.beneficiary_id)
        if stage == 'interview':
            iv = Interview.objects.get_or_create(plan=plan)[0]
            missing = iv.missing_for_completion()
            if missing:
                messages.warning(request, '完了するには次を入力してください：' + '・'.join(missing))
                return redirect('planbook:plan_tab', pk=plan.beneficiary_id, plan_pk=plan.pk, tab=stage)
            iv.complete(request.user)
        else:
            step = plan.get_step(services.STAGE_STEP[stage])
            remaining = [r['label'] for r in step.requirements() if not r['done']]
            if remaining and stage == 'plan':
                messages.warning(request, '完了するには次を入力してください：' + '・'.join(remaining))
                return redirect('planbook:plan_tab', pk=plan.beneficiary_id, plan_pk=plan.pk, tab=stage)
        services.complete_stage(plan, stage, request.user)
        messages.success(request, f'{services.STAGE_LABELS[stage]}を完了しました（編集ロック）。')
        nxt = {'interview': 'draft', 'draft': 'meeting', 'meeting': 'plan', 'plan': 'monitoring'}[stage]
        return redirect('planbook:plan_tab', pk=plan.beneficiary_id, plan_pk=plan.pk, tab=nxt)


class StageReopenView(LoginRequiredMixin, View):
    def post(self, request, plan_pk, stage):
        plan = _plan_or_404(request, plan_pk)
        if stage not in services.STAGE_STEP:
            return redirect('planbook:student', pk=plan.beneficiary_id)
        if stage == 'interview':
            iv = Interview.objects.get_or_create(plan=plan)[0]
            iv.reopen()
        services.reopen_stage(plan, stage)
        messages.info(request, f'{services.STAGE_LABELS[stage]}のロックを外しました。修正後にもう一度完了してください。')
        return redirect('planbook:plan_tab', pk=plan.beneficiary_id, plan_pk=plan.pk, tab=stage)


class MonitoringAddView(LoginRequiredMixin, View):
    def post(self, request, plan_pk):
        plan = _plan_or_404(request, plan_pk)
        form = MonitoringForm(request.POST, facility=plan.facility)
        if not form.is_valid():
            messages.error(request, '入力内容に誤りがあります。')
            b = plan.beneficiary
            return render(request, 'planbook/student.html', PlanTabView()._ctx(request, b, plan, 'monitoring', form=form))
        rec = form.save(commit=False)
        rec.plan = plan
        rec.save()
        messages.success(request, 'モニタリングを記録しました。')
        return redirect('planbook:plan_tab', pk=plan.beneficiary_id, plan_pk=plan.pk, tab='monitoring')


class MonitoringDeleteView(LoginRequiredMixin, View):
    def post(self, request, plan_pk, record_pk):
        plan = _plan_or_404(request, plan_pk)
        plan.monitoring_records.filter(pk=record_pk).delete()
        messages.success(request, 'モニタリングの記録を削除しました。')
        return redirect('planbook:plan_tab', pk=plan.beneficiary_id, plan_pk=plan.pk, tab='monitoring')


# =============================================
# 完了期日一覧
# =============================================
class DeadlineListView(LoginRequiredMixin, View):
    def get(self, request):
        qs = _active_filter(request, Beneficiary.objects.filter(facility=request.user.facility))
        qs = services.prefetch_for_cards(qs.order_by('last_name_kana', 'first_name_kana', 'pk'))
        rows = []
        for b in qs:
            plans = services.period_plans(b)
            sel = request.GET.get(f'p{b.pk}')
            plan = None
            if plans:
                try:
                    plan = plans[int(sel) - 1] if sel else plans[-1]
                except (ValueError, IndexError):
                    plan = plans[-1]
            rows.append({
                'beneficiary': b, 'plans': plans, 'plan': plan,
                'period': services.period_number(plan) if plan else None,
                'stages': services.stage_status(plan), 'next_update': services.next_update_date(plan),
            })
        sort = request.GET.get('sort', 'name')
        if sort == 'due':
            rows.sort(key=lambda r: (r['next_update'] is None, r['next_update'] or date.max))
        return render(request, 'planbook/deadlines.html', {
            'rows': rows, 'q': request.GET.get('q', ''), 'include_inactive': request.GET.get('include_inactive') == '1',
            'sort': sort, 'today': date.today(),
        })


# =============================================
# スタッフ・保護者・連絡帳・施設
# =============================================
class StaffListView(LoginRequiredMixin, View):
    def get(self, request):
        q = (request.GET.get('q') or '').strip()
        staff = StaffAccount.objects.filter(facility=request.user.facility).order_by('-is_active', 'role', 'username')
        if q:
            staff = staff.filter(db_models.Q(display_name__icontains=q) | db_models.Q(username__icontains=q))
        return render(request, 'planbook/staff.html', {'staff': staff, 'q': q,
                                                       'can_manage': request.user.is_admin or request.user.is_superuser})


class GuardianListView(LoginRequiredMixin, View):
    def get(self, request):
        q = (request.GET.get('q') or '').strip()
        gs = Guardian.objects.filter(beneficiary__facility=request.user.facility).select_related('beneficiary') \
            .order_by('beneficiary__last_name_kana', 'beneficiary__first_name_kana', '-is_primary', 'pk')
        if q:
            gs = gs.filter(db_models.Q(last_name__icontains=q) | db_models.Q(first_name__icontains=q)
                           | db_models.Q(beneficiary__last_name__icontains=q) | db_models.Q(beneficiary__first_name__icontains=q))
        students = Beneficiary.objects.filter(facility=request.user.facility, status=Beneficiary.STATUS_ACTIVE)
        return render(request, 'planbook/guardians.html', {'guardians': gs, 'q': q, 'students': students})


class GuardianDetailView(LoginRequiredMixin, View):
    def _get(self, request, pk):
        return get_object_or_404(Guardian, pk=pk, beneficiary__facility=request.user.facility)

    def get(self, request, pk):
        g = self._get(request, pk)
        return render(request, 'planbook/guardian.html', {'g': g, 'form': GuardianEditForm(instance=g),
                                                          'note_count': g.contact_notes.count()})

    def post(self, request, pk):
        g = self._get(request, pk)
        form = GuardianEditForm(request.POST, instance=g)
        if form.is_valid():
            form.save()
            messages.success(request, '保護者情報を保存しました。')
            return redirect('planbook:guardian', pk=pk)
        messages.error(request, '入力内容を確認してください。')
        return render(request, 'planbook/guardian.html', {'g': g, 'form': form, 'note_count': g.contact_notes.count()})


class GuardianDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        g = get_object_or_404(Guardian, pk=pk, beneficiary__facility=request.user.facility)
        name = g.full_name
        g.delete()
        messages.success(request, f'{name} 様の保護者情報を削除しました。')
        return redirect('planbook:guardians')


class NoteListView(LoginRequiredMixin, View):
    def get(self, request):
        return render(request, 'planbook/notes.html', {'threads': services.note_threads(request.user.facility)})


class NoteThreadView(LoginRequiredMixin, View):
    def _guardian(self, request, pk):
        return get_object_or_404(Guardian, pk=pk, beneficiary__facility=request.user.facility)

    def get(self, request, guardian_pk):
        g = self._guardian(request, guardian_pk)
        ContactNote.objects.filter(guardian=g, sender=ContactNote.FROM_GUARDIAN, is_read=False).update(is_read=True)
        notes = g.contact_notes.select_related('author')
        return render(request, 'planbook/note_thread.html', {'g': g, 'notes': notes,
                                                             'line_ready': request.user.facility.use_line and g.line_linked})

    def post(self, request, guardian_pk):
        g = self._guardian(request, guardian_pk)
        body = (request.POST.get('body') or '').strip()
        if not body:
            messages.error(request, '内容を入力してください。')
            return redirect('planbook:note_thread', guardian_pk=guardian_pk)
        note = services.post_staff_note(request.user.facility, g, request.user, body[:2000])
        if note.line_sent:
            messages.success(request, '連絡帳に記入し、LINE で送りました。')
        elif note.line_error:
            messages.warning(request, f'連絡帳に記入しましたが、LINE は送れませんでした（{note.line_error}）。')
        else:
            messages.success(request, '連絡帳に記入しました。')
        return redirect('planbook:note_thread', guardian_pk=guardian_pk)


class FacilityInfoView(LoginRequiredMixin, View):
    def get(self, request):
        f = request.user.facility
        return render(request, 'planbook/facility.html', {
            'facility': f, 'form': FacilityInfoForm(instance=f),
            'student_count': Beneficiary.objects.filter(facility=f, status=Beneficiary.STATUS_ACTIVE).count(),
            'can_edit': request.user.can_manage_settings,
        })

    def post(self, request):
        f = request.user.facility
        if not request.user.can_manage_settings:
            messages.error(request, '施設情報を変えられるのは管理者だけです。')
            return redirect('planbook:facility')
        form = FacilityInfoForm(request.POST, instance=f)
        if form.is_valid():
            form.save()
            messages.success(request, '施設のアカウント情報を保存しました。')
            return redirect('planbook:facility')
        messages.error(request, '入力内容を確認してください。')
        return render(request, 'planbook/facility.html', {'facility': f, 'form': form, 'can_edit': True,
                                                          'student_count': Beneficiary.objects.filter(facility=f).count()})


# =============================================
# ファイルから登録
# =============================================
class StudentImportTemplateView(LoginRequiredMixin, View):
    def get(self, request):
        res = HttpResponse(services.import_template_csv().encode('utf-8-sig'), content_type='text/csv; charset=utf-8')
        res['Content-Disposition'] = 'attachment; filename="students_template.csv"'
        return res


class StudentImportView(LoginRequiredMixin, View):
    def get(self, request):
        return render(request, 'planbook/student_import.html', {'form': ImportForm(), 'headers': services.IMPORT_HEADERS})

    def post(self, request):
        form = ImportForm(request.POST, request.FILES)
        if not form.is_valid():
            messages.error(request, 'ファイルを選んでください。')
            return render(request, 'planbook/student_import.html', {'form': form, 'headers': services.IMPORT_HEADERS})
        try:
            rows = services.read_import_rows(form.cleaned_data['file'])
        except Exception as e:  # noqa: BLE001
            messages.error(request, f'ファイルを読めませんでした：{e}')
            return render(request, 'planbook/student_import.html', {'form': form, 'headers': services.IMPORT_HEADERS})
        if not rows:
            messages.error(request, 'データの行がありません（1行目は見出し、2行目から利用者）。')
            return render(request, 'planbook/student_import.html', {'form': form, 'headers': services.IMPORT_HEADERS})
        created, errors = services.import_beneficiaries(request.user.facility, rows)
        if created:
            messages.success(request, f'{created} 名を登録しました。')
        for line, reason in errors[:20]:
            messages.warning(request, f'{line} 行目：{reason}')
        return redirect('planbook:students')

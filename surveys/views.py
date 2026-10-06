"""アンケート・自己評価の画面（職員側）"""
import datetime
import hashlib

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from config.concurrency import check_conflict
from config.pdf import pdf_or_html
from config.utils import to_int

from .models import SelfEvaluation, Survey, SurveyResponse, fiscal_label, fiscal_year_of
from .questions import default_questions, questions_from_text, questions_to_text

DEFAULT_INTRO = {
    Survey.KIND_GUARDIAN: ('日頃より当事業所の運営にご理解・ご協力をいただき、ありがとうございます。\n'
                           'よりよい支援のため、保護者の皆さまのご意見をお聞かせください。回答は無記名で、集計した結果と改善の内容を公表します。\n'
                           '設問ごとに「はい・どちらともいえない・いいえ・わからない」からお選びください。ご意見があれば自由にお書きください。'),
    Survey.KIND_STAFF: ('事業所の自己評価のための従業者評価です。日頃の支援を振り返り、各項目について「はい・いいえ」で答え、\n'
                        '工夫している点や課題と思うことを書いてください（無記名。集計して自己評価の総括に使います）。'),
}


class SurveyEnabledMixin(LoginRequiredMixin):
    """施設設定でアンケート・自己評価を使わない事業所はホームへ戻す"""

    def dispatch(self, request, *args, **kwargs):
        facility = getattr(request.user, 'facility', None)
        if request.user.is_authenticated and not (facility is not None and facility.use_survey):
            messages.info(request, 'この事業所ではアンケート・自己評価を使わない設定になっています（施設設定の「使う機能」で変えられます）。')
            return redirect('facilities:dashboard')
        return super().dispatch(request, *args, **kwargs)


def guess_service(facility):
    return Survey.SERVICE_JIHATSU if '児童発達支援' in facility.name else Survey.SERVICE_HOUDAY


def qr_svg(url, scale=4):
    """回答ページの QR（画面と PDF 用の SVG。外部サービスに送らずサーバーで作る）"""
    import io
    import segno
    buf = io.BytesIO()
    segno.make(url, error='m').save(buf, kind='svg', scale=scale, border=2, xmldecl=False, svgns=True, dark='#222')
    from django.utils.safestring import mark_safe
    return mark_safe(buf.getvalue().decode('utf-8'))


def linked_guardians(facility):
    """LINE で送れる保護者（在籍中の利用者の、LINE 連携ずみの保護者。同じ LINE には 1 回）"""
    from beneficiaries.models import Guardian
    seen, out = set(), []
    for g in Guardian.objects.filter(beneficiary__facility=facility, beneficiary__status='active', line_linked=True) \
                             .exclude(line_user_id='').select_related('beneficiary').order_by('beneficiary__last_name_kana', 'pk'):
        if g.line_user_id in seen:
            continue
        seen.add(g.line_user_id)
        out.append(g)
    return out


class IndexView(SurveyEnabledMixin, View):
    template_name = 'surveys/index.html'

    def get(self, request):
        facility = request.user.facility
        surveys = list(Survey.objects.filter(facility=facility))
        counts = {r['survey']: r['n'] for r in SurveyResponse.objects.filter(survey__facility=facility).values('survey')
                  .annotate(n=__import__('django.db.models', fromlist=['Count']).Count('pk'))}
        for s in surveys:
            s.n_responses = counts.get(s.pk, 0)
        years = {}
        for s in surveys:
            years.setdefault(s.fiscal_year, {'year': s.fiscal_year, 'label': fiscal_label(s.fiscal_year), 'surveys': [], 'self': None})['surveys'].append(s)
        for e in SelfEvaluation.objects.filter(facility=facility):
            years.setdefault(e.fiscal_year, {'year': e.fiscal_year, 'label': fiscal_label(e.fiscal_year), 'surveys': [], 'self': None})
            years[e.fiscal_year]['self'] = e
        this_year = fiscal_year_of()
        return render(request, self.template_name, {
            'years': [years[y] for y in sorted(years, reverse=True)], 'this_year': this_year, 'this_label': fiscal_label(this_year),
            'service': guess_service(facility), 'services': Survey.SERVICE_CHOICES,
        })


class SurveyCreateView(SurveyEnabledMixin, View):
    template_name = 'surveys/form.html'

    def get(self, request):
        facility = request.user.facility
        kind = request.GET.get('kind') if request.GET.get('kind') in dict(Survey.KIND_CHOICES) else Survey.KIND_GUARDIAN
        service = request.GET.get('service') if request.GET.get('service') in dict(Survey.SERVICE_CHOICES) else guess_service(facility)
        year = to_int(request.GET.get('year'), fiscal_year_of())
        s = Survey(facility=facility, kind=kind, service=service, fiscal_year=year,
                   title=f'{fiscal_label(year)} {dict(Survey.KIND_CHOICES)[kind]}', intro=DEFAULT_INTRO[kind],
                   closes_on=datetime.date.today() + datetime.timedelta(days=21))
        return render(request, self.template_name, {'survey': s, 'questions_text': questions_to_text(default_questions(kind, service)),
                                                    'kinds': Survey.KIND_CHOICES, 'services': Survey.SERVICE_CHOICES, 'locked': False})

    def post(self, request):
        s = Survey(facility=request.user.facility, created_by=request.user)
        error = _apply(s, request.POST, locked=False)
        if error:
            messages.error(request, error)
            return render(request, self.template_name, {'survey': s, 'questions_text': request.POST.get('questions', ''),
                                                        'kinds': Survey.KIND_CHOICES, 'services': Survey.SERVICE_CHOICES, 'locked': False})
        s.save()
        messages.success(request, f'「{s.title}」を作りました。回答ページのアドレスと QR を配ってください。')
        return redirect('surveys:detail', s.pk)


def _apply(s, p, locked):
    """フォーム → Survey。問題があれば文を返す"""
    if not locked:
        if p.get('kind') in dict(Survey.KIND_CHOICES):
            s.kind = p['kind']
        if p.get('service') in dict(Survey.SERVICE_CHOICES):
            s.service = p['service']
        qs = questions_from_text(p.get('questions', ''))
        if not qs:
            return '設問を 1 つ以上入れてください。'
        s.questions = qs
    s.fiscal_year = max(2000, min(2100, to_int(p.get('fiscal_year'), s.fiscal_year or fiscal_year_of())))
    s.title = (p.get('title') or '').strip()[:100] or f'{fiscal_label(s.fiscal_year)} {s.get_kind_display()}'
    s.intro = (p.get('intro') or '').strip()[:3000]
    s.is_open = 'is_open' in p
    try:
        s.closes_on = datetime.date.fromisoformat(p.get('closes_on')) if p.get('closes_on') else None
    except ValueError:
        s.closes_on = None
    return None


def _survey(request, pk):
    return get_object_or_404(Survey, pk=pk, facility=request.user.facility)


class SurveyDetailView(SurveyEnabledMixin, View):
    template_name = 'surveys/detail.html'

    def get(self, request, pk):
        s = _survey(request, pk)
        url = request.build_absolute_uri(s.public_path())
        return render(request, self.template_name, {
            'survey': s, 'url': url, 'qr': qr_svg(url), 'summary': s.summary(),
            'responses': list(s.responses.all()), 'guardians': linked_guardians(request.user.facility) if s.is_guardian else [],
            'line_ready': bool(request.user.facility.line_channel_access_token),
            'self_eval': SelfEvaluation.objects.filter(facility=request.user.facility, fiscal_year=s.fiscal_year, service=s.service).first(),
        })


class SurveyEditView(SurveyEnabledMixin, View):
    template_name = 'surveys/form.html'

    def get(self, request, pk):
        s = _survey(request, pk)
        locked = s.responses.exists()
        return render(request, self.template_name, {'survey': s, 'questions_text': questions_to_text(s.questions),
                                                    'kinds': Survey.KIND_CHOICES, 'services': Survey.SERVICE_CHOICES, 'locked': locked})

    def post(self, request, pk):
        s = _survey(request, pk)
        conflict = check_conflict(request, s)
        if conflict:
            messages.error(request, conflict)
            return redirect('surveys:edit', pk)
        locked = s.responses.exists()
        error = _apply(s, request.POST, locked=locked)
        if error:
            messages.error(request, error)
            return redirect('surveys:edit', pk)
        s.save()
        messages.success(request, '保存しました。' + ('（回答があるので設問は変えていません）' if locked else ''))
        return redirect('surveys:detail', pk)


class SurveyLineView(SurveyEnabledMixin, View):
    """LINE 連携ずみの保護者に回答ページのアドレスを送る"""

    def post(self, request, pk):
        from line_integration.sending import push_text
        s = _survey(request, pk)
        facility = request.user.facility
        if not s.is_guardian:
            messages.error(request, '職員向けのアンケートは LINE では送りません。アドレスか QR を職員に渡してください。')
            return redirect('surveys:detail', pk)
        url = request.build_absolute_uri(s.public_path())
        text = (f'【{facility.name}】{s.title}のお願い\n'
                f'{(s.intro or "").strip()}\n\n'
                f'こちらから回答をお願いします（{s.closes_on:%-m月%-d日}まで）：\n' if s.closes_on else
                f'【{facility.name}】{s.title}のお願い\n{(s.intro or "").strip()}\n\nこちらから回答をお願いします：\n') + url
        sent = failed = 0
        for g in linked_guardians(facility):
            ok, _err = push_text(facility, g.line_user_id, text)
            sent += ok
            failed += (not ok)
        s.line_sent_at = timezone.now()
        s.line_sent_count += sent
        s.save(update_fields=['line_sent_at', 'line_sent_count', 'updated_at'])
        if sent:
            messages.success(request, f'LINE で {sent} 人に送りました。' + (f'（送れなかった人 {failed}）' if failed else ''))
        else:
            messages.warning(request, 'LINE で送れた人がいません。施設設定のチャネルアクセストークンと、保護者の LINE 連携を確かめてください。')
        return redirect('surveys:detail', pk)


class SurveyDeleteView(SurveyEnabledMixin, View):
    def post(self, request, pk):
        s = _survey(request, pk)
        if not (request.user.is_admin or request.user.is_superuser):
            messages.error(request, 'アンケートの削除は管理者だけができます。')
            return redirect('surveys:detail', pk)
        title = s.title
        s.delete()
        messages.success(request, f'「{title}」を回答ごと削除しました。')
        return redirect('surveys:index')


class ResponseDeleteView(SurveyEnabledMixin, View):
    def post(self, request, pk, response_pk):
        s = _survey(request, pk)
        r = get_object_or_404(s.responses, pk=response_pk)
        r.delete()
        messages.success(request, '回答を 1 件消しました。')
        return redirect(reverse('surveys:detail', args=[pk]) + '#responses')


class NoticePdfView(SurveyEnabledMixin, View):
    """配布用の案内（A4。QR とアドレス。LINE を使わない保護者に渡す）"""

    def get(self, request, pk):
        s = _survey(request, pk)
        url = request.build_absolute_uri(s.public_path())
        return pdf_or_html(request, 'surveys/pdf/notice.html', {'facility': request.user.facility, 'survey': s, 'url': url, 'qr': qr_svg(url, scale=6)},
                           f'アンケート案内_{s.title}')


class ResultsPdfView(SurveyEnabledMixin, View):
    """集計結果（公表用。保護者評価は「保護者等からの事業所評価の集計結果」、従業者評価は集計表）"""

    def get(self, request, pk):
        s = _survey(request, pk)
        ev = SelfEvaluation.objects.filter(facility=request.user.facility, fiscal_year=s.fiscal_year, service=s.service).first()
        improvements = {i['text']: i.get('improvement', '') for i in (ev.guardian_items if ev else [])}
        summary = s.summary()
        for row in summary['rows']:
            row['improvement'] = improvements.get(row['text'], '')
        return pdf_or_html(request, 'surveys/pdf/results.html', {'facility': request.user.facility, 'survey': s, 'summary': summary,
                                                                 'self_eval': ev, 'today': datetime.date.today()},
                           f'集計結果_{s.title}')


# ------------------------------------------------------------------ 自己評価
class SelfEvaluationCreateView(SurveyEnabledMixin, View):
    def post(self, request):
        facility = request.user.facility
        year = to_int(request.POST.get('fiscal_year'), fiscal_year_of())
        service = request.POST.get('service') if request.POST.get('service') in dict(Survey.SERVICE_CHOICES) else guess_service(facility)
        ev, created = SelfEvaluation.objects.get_or_create(facility=facility, fiscal_year=year, service=service,
                                                           defaults={'created_by': request.user})
        if created:
            ev.guardian_survey = Survey.objects.filter(facility=facility, fiscal_year=year, service=service, kind=Survey.KIND_GUARDIAN).first()
            ev.staff_survey = Survey.objects.filter(facility=facility, fiscal_year=year, service=service, kind=Survey.KIND_STAFF).first()
            ev.seed_items()
            ev.fill_from_staff()
            ev.save()
            messages.success(request, f'{ev.fiscal_label}の自己評価を作りました。項目ごとに「はい・いいえ」と工夫・改善を書き、総括を入れてください。')
        return redirect('surveys:self_edit', ev.pk)


class SelfEvaluationView(SurveyEnabledMixin, View):
    template_name = 'surveys/self_form.html'

    def _get(self, request, pk):
        return get_object_or_404(SelfEvaluation, pk=pk, facility=request.user.facility)

    def get(self, request, pk):
        ev = self._get(request, pk)
        facility = request.user.facility
        staff_summary = ev.staff_survey.summary() if ev.staff_survey_id else None
        guardian_summary = ev.guardian_survey.summary() if ev.guardian_survey_id else None
        staff_by_no = {r['no']: r for r in staff_summary['rows']} if staff_summary else {}
        g_by_no = {r['no']: r for r in guardian_summary['rows']} if guardian_summary else {}
        items = [dict(i, agg=staff_by_no.get(i['no'])) for i in ev.items]
        gitems = [dict(i, agg=g_by_no.get(i['no'])) for i in ev.guardian_items]
        return render(request, self.template_name, {
            'ev': ev, 'items': items, 'gitems': gitems, 'staff_summary': staff_summary, 'guardian_summary': guardian_summary,
            'surveys': Survey.objects.filter(facility=facility, fiscal_year=ev.fiscal_year, service=ev.service),
        })

    def post(self, request, pk):
        ev = self._get(request, pk)
        conflict = check_conflict(request, ev)
        if conflict:
            messages.error(request, conflict)
            return redirect('surveys:self_edit', pk)
        p = request.POST
        action = p.get('action', 'save')
        facility = request.user.facility
        if action == 'relink':
            ev.guardian_survey = Survey.objects.filter(facility=facility, pk=to_int(p.get('guardian_survey'), 0), kind=Survey.KIND_GUARDIAN).first()
            ev.staff_survey = Survey.objects.filter(facility=facility, pk=to_int(p.get('staff_survey'), 0), kind=Survey.KIND_STAFF).first()
            ev.seed_items()
            ev.save()
            messages.success(request, 'アンケートの結びつけと項目を更新しました。')
            return redirect('surveys:self_edit', pk)
        if action == 'fill':
            n = ev.fill_from_staff()
            ev.save()
            messages.success(request, f'従業者評価の集計から {n} 項目の結果を入れました（空の欄だけ）。')
            return redirect('surveys:self_edit', pk)
        for i in ev.items:
            key = f'i{i["no"]}'
            i['result'] = p.get(f'{key}_result') if p.get(f'{key}_result') in ('yes', 'no') else ''
            i['strength'] = (p.get(f'{key}_strength') or '').strip()[:2000]
            i['improvement'] = (p.get(f'{key}_improvement') or '').strip()[:2000]
        for i in ev.guardian_items:
            i['improvement'] = (p.get(f'g{i["no"]}_improvement') or '').strip()[:2000]
        ev.summary = (p.get('summary') or '').strip()[:8000]
        ev.publish_note = (p.get('publish_note') or '').strip()[:200]
        try:
            ev.published_on = datetime.date.fromisoformat(p.get('published_on')) if p.get('published_on') else None
        except ValueError:
            ev.published_on = None
        ev.save()
        messages.success(request, '自己評価を保存しました。')
        return redirect('surveys:self_edit', pk)


class SelfEvaluationPdfView(SurveyEnabledMixin, View):
    """公表用：事業所における自己評価結果（項目・はい／いいえ・工夫・改善）と、保護者評価の集計・改善目標、総括"""

    def get(self, request, pk):
        ev = get_object_or_404(SelfEvaluation, pk=pk, facility=request.user.facility)
        guardian_summary = ev.guardian_survey.summary() if ev.guardian_survey_id else None
        improvements = {i['text']: i.get('improvement', '') for i in ev.guardian_items}
        if guardian_summary:
            for row in guardian_summary['rows']:
                row['improvement'] = improvements.get(row['text'], '')
        staff_summary = ev.staff_survey.summary() if ev.staff_survey_id else None
        return pdf_or_html(request, 'surveys/pdf/self.html', {
            'facility': request.user.facility, 'ev': ev, 'guardian_summary': guardian_summary, 'staff_summary': staff_summary,
            'today': datetime.date.today(),
        }, f'自己評価結果_{ev.fiscal_label}')

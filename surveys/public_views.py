"""回答ページ（ログインなし）"""
import hashlib
import logging

from django.core.cache import cache
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.views import View

from reservations import tokens

from .models import TOKEN_KIND, Survey, SurveyResponse

logger = logging.getLogger(__name__)
POST_LIMIT, POST_WINDOW = 5, 600        # 同じ端末から 10 分に 5 回まで


def client_key(request):
    raw = (request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip() or request.META.get('REMOTE_ADDR', '')) \
        + '|' + request.META.get('HTTP_USER_AGENT', '')[:120]
    return hashlib.sha256(raw.encode('utf-8', 'ignore')).hexdigest()[:32]


class AnswerView(View):
    template_name = 'surveys/public/answer.html'

    def dispatch(self, request, *args, **kwargs):
        token = kwargs.get('token', '')
        if not tokens.is_valid(TOKEN_KIND, token):
            raise Http404('アドレスが正しくありません')
        self.survey = get_object_or_404(Survey.objects.select_related('facility'), token=token)
        response = super().dispatch(request, *args, **kwargs)
        response['X-Robots-Tag'] = 'noindex, nofollow'
        response['Cache-Control'] = 'no-store'
        return response

    def _ctx(self, **extra):
        s = self.survey
        sections, last = [], None
        for q in s.questions:
            if q['section'] != last:
                sections.append({'name': q['section'], 'questions': []})
                last = q['section']
            sections[-1]['questions'].append(q)
        ctx = {'survey': s, 'facility': s.facility, 'sections': sections, 'choices': s.choices, 'accepting': s.accepting}
        ctx.update(extra)
        return ctx

    def get(self, request, token):
        return render(request, self.template_name, self._ctx(answers={}, comments={}, free_text=''))

    def post(self, request, token):
        s = self.survey
        if not s.accepting:
            return render(request, self.template_name, self._ctx(answers={}, comments={}, free_text=''))
        key = f'survey_post:{client_key(request)}'
        count = cache.get(key, 0)
        if count >= POST_LIMIT:
            return render(request, self.template_name, self._ctx(answers={}, comments={}, free_text='',
                                                                 error='短い時間に何度も送られています。しばらくしてからお試しください。'))
        cache.set(key, count + 1, POST_WINDOW)
        valid = {k for k, _ in s.choices}
        answers, comments = {}, {}
        for q in s.questions:
            v = request.POST.get(f'q{q["no"]}')
            if v in valid:
                answers[str(q['no'])] = v
            c = (request.POST.get(f'c{q["no"]}') or '').strip()[:1000]
            if c:
                comments[str(q['no'])] = c
        free_text = (request.POST.get('free_text') or '').strip()[:4000]
        if not answers:
            return render(request, self.template_name, self._ctx(answers=answers, comments=comments, free_text=free_text,
                                                                 error='1 つ以上の設問に答えてください。'))
        SurveyResponse.objects.create(survey=s, answers=answers, comments=comments, free_text=free_text,
                                      respondent=(request.POST.get('respondent') or '').strip()[:50], client_key=client_key(request))
        return render(request, 'surveys/public/done.html', {'survey': s, 'facility': s.facility})

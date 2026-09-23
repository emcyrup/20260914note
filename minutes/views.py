"""議事録の画面（別タブで開いて、話しながら記録する）"""
import datetime

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from ai_assist.quick import ask_ai, tidy_sections
from config.utils import to_int

from .models import KEEP, Minutes

TEXT_MAX = 30000      # 話した内容（1時間ほど話したぶん）
SUMMARY_MAX = 12000

ORGANIZE_PROMPT = """あなたは放課後等デイサービスの職員を手伝うAIです。
会議・打ち合わせ・保護者との面談・療育の振り返りなどで話した内容（音声入力の文字起こし）を、
あとから読んだ人が経過を思い浮かべられる**詳しい議事録**に整理します。

【形（必ず守る）】
【概要】
（何について話したかを1〜2文で）

【話題の名前】
・小見出し：内容を1〜3文で
・小見出し：内容
（話題・議題・場面ごとに【】の見出しを分ける。話に出てきた順に並べる）

【決まったこと】
・（話の中で決まったことだけ。無ければこの見出しごと書かない）

【次にやること】
・（だれが・いつまでに・何をするか。話に出た分だけ。無ければこの見出しごと書かない）

【書き方】
- 発言は「」で、話したとおりに残す（大事な発言・子どもの言葉・保護者の要望など）
- 何をきっかけに・どうなったか（提案 → 反応、促し → 反応）が分かるように書く。数・日付・回数も話にあれば残す
- 常体（〜した。〜する。）で書く。敬語・あいさつ・前置きは書かない
- 話に無いことは足さない。推測や評価のことばは、話した人が言ったときだけ書く
- 「えー」「あの」などの言いよどみ、言い直し、雑談、同じ話のくり返しは除く
- 文字起こしの聞き間違いと思われる語は、前後から明らかなときだけ直す
- 常用漢字とひらがな・カタカナで書く。英語・絵文字・マークダウン（# や ** ）は使わない。見出しは【】、項目は「・」だけを使う
- 返すのは議事録の文だけ"""


def _default_title(day):
    return f'{day.month}月{day.day}日の記録'


class MinutesView(LoginRequiredMixin, View):
    """議事録：新しく書く／履歴（新しい10件）から開いて直す"""
    template_name = 'minutes/index.html'

    def get(self, request):
        facility = request.user.facility
        history = list(Minutes.objects.filter(facility=facility).select_related('created_by')[:KEEP])
        current = None
        if request.GET.get('id'):
            current = get_object_or_404(Minutes, pk=to_int(request.GET.get('id'), -1), facility=facility)
        today = datetime.date.today()
        return render(request, self.template_name, {
            'history': history, 'current': current, 'keep': KEEP, 'today': today,
            'default_title': _default_title(today), 'full': len(history) >= KEEP,
            'text_max': TEXT_MAX, 'summary_max': SUMMARY_MAX,
        })

    def post(self, request):
        facility = request.user.facility
        p = request.POST
        if p.get('action') == 'delete':
            m = get_object_or_404(Minutes, pk=to_int(p.get('id'), -1), facility=facility)
            m.delete()
            messages.success(request, f'「{m.title}」を削除しました。')
            return redirect('minutes:index')
        try:
            held_on = datetime.date.fromisoformat(p.get('held_on') or '')
        except ValueError:
            held_on = datetime.date.today()
        fields = {
            'title': (p.get('title') or '').strip()[:100] or _default_title(held_on),
            'held_on': held_on,
            'transcript': (p.get('transcript') or '').strip()[:TEXT_MAX],
            'summary': (p.get('summary') or '').strip()[:SUMMARY_MAX],
        }
        if not fields['transcript'] and not fields['summary']:
            messages.error(request, '話した内容か議事録のどちらかを入れてから保存してください。')
            return redirect(request.get_full_path())
        if p.get('id'):
            m = get_object_or_404(Minutes, pk=to_int(p.get('id'), -1), facility=facility)
            for k, v in fields.items():
                setattr(m, k, v)
            m.save()
            messages.success(request, f'「{m.title}」を保存しました。')
        else:
            m = Minutes.objects.create(facility=facility, created_by=request.user, **fields)
            removed = Minutes.prune(facility)
            msg = f'「{m.title}」を保存しました。'
            if removed:
                msg += f'（新しい{KEEP}件を残すため、古い議事録を{removed}件消しました）'
            messages.success(request, msg)
        return redirect(f"{reverse('minutes:index')}?id={m.pk}")


class OrganizeView(LoginRequiredMixin, View):
    """話した内容を AI で議事録（見出し・箇条書き・決まったこと・次にやること）に整理して返す。保存はしない"""

    def post(self, request):
        text = (request.POST.get('text') or '').strip()
        if not text:
            return JsonResponse({'error': '話した内容が空です。先に音声入力か文字で入れてください。'}, status=400)
        title = (request.POST.get('title') or '').strip()[:100]
        head = f'【件名】{title}\n' if title else ''
        raw, error = ask_ai(ORGANIZE_PROMPT, f'{head}【話した内容】\n{text[:TEXT_MAX]}', 8000)
        if error:
            return error
        result = tidy_sections(raw)[:SUMMARY_MAX]
        if not result:
            return JsonResponse({'error': 'AIの返答が空でした。もう一度お試しください。'}, status=500)
        return JsonResponse({'result': result})


class PrintView(LoginRequiredMixin, View):
    def get(self, request, pk):
        m = get_object_or_404(Minutes, pk=pk, facility=request.user.facility)
        return render(request, 'minutes/print.html', {'m': m, 'facility': request.user.facility})

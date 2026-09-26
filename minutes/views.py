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
THERAPY_BODY_MAX = 4000   # 療育記録の「記録」の欄に入れられる長さ（therapy の画面と同じ）

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
- 文字起こしに「話者1：」「話者2：」のような行があるときは、だれの発言かが分かるように書く（例：話者1「…」）。
  話者の番号は録音の区切りごとに付け直されることがあるので、話の流れから同じ人と分かるときは同じ人としてまとめ、
  名前や役割（司会・担当職員・保護者など）が話の中で分かるときは、番号の代わりにそれを使う
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
        children = []
        if getattr(facility, 'use_therapy_record', False):       # 療育記録として保存するときの利用者の選択肢
            from beneficiaries.models import Beneficiary
            children = list(Beneficiary.objects.filter(facility=facility, status=Beneficiary.STATUS_ACTIVE))
        return render(request, self.template_name, {
            'history': history, 'current': current, 'keep': KEEP, 'today': today,
            'default_title': _default_title(today), 'full': len(history) >= KEEP,
            'text_max': TEXT_MAX, 'summary_max': SUMMARY_MAX, 'children': children,
            'therapy_body_max': THERAPY_BODY_MAX,
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
        if p.get('save_as') == 'therapy':
            return self._save_as_therapy(request, facility, fields)
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


    @staticmethod
    def _save_as_therapy(request, facility, fields):
        """議事録ではなく、選んだ利用者の療育記録（記録の欄）として保存する。議事録の履歴には残さない"""
        from beneficiaries.models import Beneficiary
        from therapy.models import TherapyRecord
        p = request.POST
        if not getattr(facility, 'use_therapy_record', False):
            messages.error(request, 'この事業所では療育記録を使っていません。')
            return redirect(request.get_full_path())
        beneficiary = Beneficiary.objects.filter(facility=facility, pk=to_int(p.get('beneficiary'), -1),
                                                 status=Beneficiary.STATUS_ACTIVE).first()
        if beneficiary is None:
            messages.error(request, '療育記録として保存するには、どの利用者の記録かを選んでください。')
            return redirect(request.get_full_path())
        try:
            time = datetime.time.fromisoformat(p.get('time') or '')
        except ValueError:
            time = None
        body = fields['summary'] or fields['transcript']
        if fields['title'] and not fields['title'].endswith('の記録'):
            body = f"【{fields['title']}】\n{body}"
        cut = len(body) > THERAPY_BODY_MAX
        rec = TherapyRecord.objects.create(
            facility=facility, beneficiary=beneficiary, date=fields['held_on'], time=time,
            staff=request.user, body=body[:THERAPY_BODY_MAX], created_by=request.user)
        msg = f'{beneficiary.full_name} さんの {fields["held_on"]:%-m/%-d} の療育記録として保存しました。「やったこと」は療育記録の画面で足せます。'
        if cut:
            msg += f'（記録の欄は {THERAPY_BODY_MAX} 字までのため、後ろを切りました）'
        messages.success(request, msg)
        return redirect(f"{reverse('therapy:child', args=[beneficiary.pk])}?ym={rec.date:%Y-%m}#rec{rec.pk}")


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

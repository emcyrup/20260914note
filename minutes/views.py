"""議事録の画面（別タブで開いて、話しながら記録する）"""
import datetime

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from ai_assist import asis
from ai_assist.quick import ask_ai, tidy_sections
from config.concurrency import check_conflict, keep_unsaved, pop_unsaved
from config.utils import to_int

from .models import KEEP, LIST_PER_PAGE, Minutes

TEXT_MAX = 30000      # 話した内容（1時間ほど話したぶん）

# 面談のはじめに読み上げる「録音のご案内」と、契約のときに署名をもらう同意書の項目（同意書の PDF と画面の両方で使う）
CONSENT_NOTICE = ('この面談は、お話しいただいた内容を正確に記録するために録音し、文字起こしと要約の作成に AI（外部のサービス）を使います。'
                  '録音と文字は事業所の記録として保管し、面談以外の目的には使いません。'
                  '録音をやめてほしいときは、いつでもおっしゃってください。よろしいでしょうか。')
CONSENT_ITEMS = [
    ('目的', '面談・相談の内容を正確に記録し、お子さまへの支援に生かすため'),
    ('方法', '事業所の端末で録音し、文字起こしと要約の作成に AI（外部のサービス）を利用します。音声は文字にしたあと端末に残しません'),
    ('保管と利用', '文字起こしと要約は事業所の記録として保管し、支援に関わる職員だけが見ます。法令に基づく場合を除き、外部には提供しません'),
    ('保存期間', '事業所の記録の保存期間に従って保管し、期間が過ぎたら消去します'),
    ('撤回', 'この同意はいつでも取り消せます。取り消したあとの面談は録音しません。取り消しによる不利益はありません'),
]
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
- 話者の印が無くても、保護者との面談と分かる文字起こしでは、話の内容と口調から職員と保護者の発言を推定して
  「職員「…」」「保護者「…」」のように書く。どちらか決めにくい発言は、だれの発言かを付けずに書く
- 常用漢字とひらがな・カタカナで書く。英語・絵文字・マークダウン（# や ** ）は使わない。見出しは【】、項目は「・」だけを使う
- 返すのは議事録の文だけ"""


def _default_title(day):
    return f'{day.month}月{day.day}日の記録'


class MinutesView(LoginRequiredMixin, View):
    """議事録：新しく書く／履歴（新しい10件。全件は一覧）から開いて直す"""
    template_name = 'minutes/index.html'

    def get(self, request):
        facility = request.user.facility
        qs = Minutes.objects.filter(facility=facility)
        history = list(qs.select_related('created_by')[:KEEP])
        total = qs.count()
        current = None
        if request.GET.get('id'):
            current = get_object_or_404(Minutes, pk=to_int(request.GET.get('id'), -1), facility=facility)
        today = datetime.date.today()
        children = []
        if getattr(facility, 'use_therapy_record', False):       # 療育記録として保存するときの利用者の選択肢
            from beneficiaries.models import Beneficiary
            children = list(Beneficiary.objects.filter(facility=facility, status=Beneficiary.STATUS_ACTIVE))
        return render(request, self.template_name, {
            'history': history, 'current': current, 'keep': KEEP, 'today': today, 'consent_notice': CONSENT_NOTICE,
            'unsaved': pop_unsaved(request, 'minutes', current.pk) if current else None,
            'default_title': _default_title(today), 'total': total,
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
        save_as = p.get('save_as') or 'minutes'
        if save_as in ('therapy', 'both'):
            return self._save_as_therapy(request, facility, fields, keep=(save_as == 'both'))
        m, back = self._save_minutes(request, facility, fields)
        if back is not None:
            return back
        messages.success(request, f'「{m.title}」を保存しました。')
        return redirect(f"{reverse('minutes:index')}?id={m.pk}")

    @staticmethod
    def _save_minutes(request, facility, fields):
        """議事録を保存（id があれば上書き）。(議事録, ほかの職員と重なったときの戻り先) を返す"""
        p = request.POST
        if p.get('id'):
            m = get_object_or_404(Minutes, pk=to_int(p.get('id'), -1), facility=facility)
            conflict = check_conflict(request, m)
            if conflict:
                keep_unsaved(request, 'minutes', m.pk, {k: str(v) for k, v in fields.items()})
                messages.error(request, conflict + '（入れた内容は画面の上に残してあります）')
                return m, redirect(f"{reverse('minutes:index')}?id={m.pk}")
            for k, v in fields.items():
                setattr(m, k, v)
            m.save()
            return m, None
        # 件数の上限は無い（以前は新しい 10 件だけ残して古いものを消していたが、10 件を超えると
        # 保存できない・消えると受け取られたのでやめた。古いものは一覧から職員が消す）
        return Minutes.objects.create(facility=facility, created_by=request.user, **fields), None

    def _save_as_therapy(self, request, facility, fields, keep=False):
        """
        選んだ利用者（複数可）の療育記録の「記録」の欄に入れる（2026-11-07 オウル：小集団の会・面談を何人かの記録に）。
        その日の記録がすでにあれば、その後ろに足す（同じ日の記録を 2 つにしない）。無ければ新しく作る。
        keep=True（両方）なら議事録の履歴にも残す
        """
        from beneficiaries.models import Beneficiary
        from therapy.models import TherapyRecord
        p = request.POST
        if not getattr(facility, 'use_therapy_record', False):
            messages.error(request, 'この事業所では療育記録を使っていません。')
            return redirect(request.get_full_path())
        ids = {to_int(v, -1) for v in p.getlist('beneficiaries')} | {to_int(p.get('beneficiary'), -1)}
        kids = list(Beneficiary.objects.filter(facility=facility, pk__in=[i for i in ids if i > 0],
                                               status=Beneficiary.STATUS_ACTIVE).order_by('last_name_kana', 'pk'))
        if not kids:
            messages.error(request, '療育記録に入れるには、どの利用者の記録かを選んでください。')
            return redirect(request.get_full_path())
        try:
            time = datetime.time.fromisoformat(p.get('time') or '')
        except ValueError:
            time = None
        body = fields['summary'] or fields['transcript']
        if fields['title'] and not fields['title'].endswith('の記録'):
            body = f"【{fields['title']}】\n{body}"
        m = None
        if keep:
            m, back = self._save_minutes(request, facility, fields)
            if back is not None:
                return back
        made, added, cut, last = [], [], False, None
        for kid in kids:
            rec = TherapyRecord.objects.filter(beneficiary=kid, date=fields['held_on']).order_by('pk').first()
            if rec is not None and (rec.body or rec.situation):
                text = f'{rec.body}\n\n{body}' if rec.body else body
                cut = cut or len(text) > THERAPY_BODY_MAX
                rec.body = text[:THERAPY_BODY_MAX]
                rec.save(update_fields=['body', 'updated_at'])
                added.append(kid)
            else:
                cut = cut or len(body) > THERAPY_BODY_MAX
                if rec is None:
                    rec = TherapyRecord.objects.create(
                        facility=facility, beneficiary=kid, date=fields['held_on'], time=time,
                        staff=request.user, body=body[:THERAPY_BODY_MAX], created_by=request.user)
                else:
                    rec.body = body[:THERAPY_BODY_MAX]
                    if time and not rec.time:
                        rec.time = time
                    rec.save(update_fields=['body', 'time', 'updated_at'])
                made.append(kid)
            last = rec
        day = f'{fields["held_on"]:%-m/%-d}'
        names = '・'.join(k.full_name for k in kids)
        if len(kids) == 1:
            msg = f'{names} さんの {day} の療育記録に' + ('足しました' if added else 'なりました') + '。「やったこと」は療育記録の画面で足せます。'
        else:
            msg = f'{len(kids)} 人（{names}）の {day} の療育記録に入れました'
            if added:
                msg += f'（{len(added)} 人はその日の記録があったので後ろに足しました）'
            msg += '。'
        if cut:
            msg += f'（記録の欄は {THERAPY_BODY_MAX} 字までのため、後ろを切りました）'
        if m is not None:
            msg += f' 議事録「{m.title}」としても残しました。'
        messages.success(request, msg)
        if len(kids) == 1 and m is None:
            return redirect(f"{reverse('therapy:child', args=[kids[0].pk])}?ym={last.date:%Y-%m}#rec{last.pk}")
        return redirect(f"{reverse('minutes:index')}?id={m.pk}" if m is not None else reverse('minutes:index'))


class ListView(LoginRequiredMixin, View):
    """議事録の一覧（全件）。件名・内容で探す、ページ送り、1 件ずつ／選んでまとめて削除"""
    template_name = 'minutes/list.html'

    def get(self, request):
        facility = request.user.facility
        q = (request.GET.get('q') or '').strip()[:100]
        qs = Minutes.objects.filter(facility=facility).select_related('created_by')
        if q:
            qs = qs.filter(Q(title__icontains=q) | Q(summary__icontains=q) | Q(transcript__icontains=q))
        page = Paginator(qs, LIST_PER_PAGE).get_page(request.GET.get('page'))
        return render(request, self.template_name, {
            'page': page, 'q': q, 'total': Minutes.objects.filter(facility=facility).count(),
        })

    def post(self, request):
        facility = request.user.facility
        p = request.POST
        ids = [to_int(v, -1) for v in p.getlist('ids')] if p.get('action') == 'delete_many' else [to_int(p.get('id'), -1)]
        qs = Minutes.objects.filter(facility=facility, pk__in=[i for i in ids if i > 0])
        titles = list(qs.values_list('title', flat=True))
        if not titles:
            messages.error(request, '消す議事録を選んでください。')
        else:
            qs.delete()
            if len(titles) == 1:
                messages.success(request, f'「{titles[0]}」を削除しました。')
            else:
                messages.success(request, f'議事録を {len(titles)} 件削除しました。')
        back = reverse('minutes:list')
        extra = {k: v for k, v in (('q', p.get('q')), ('page', p.get('page'))) if v}
        if extra:
            from urllib.parse import urlencode
            back += '?' + urlencode(extra)
        return redirect(back)


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
        return JsonResponse({'result': result, **asis.check(f'{title}\n{text}', result)})


class PrintView(LoginRequiredMixin, View):
    def get(self, request, pk):
        m = get_object_or_404(Minutes, pk=pk, facility=request.user.facility)
        return render(request, 'minutes/print.html', {'m': m, 'facility': request.user.facility})


class ConsentView(LoginRequiredMixin, View):
    """面談の録音・文字起こし・AI 要約についての同意書（A4 1枚。契約のときに署名をもらう）。?fmt=html で画面表示"""

    def get(self, request):
        from config.pdf import pdf_or_html
        facility = request.user.facility
        ctx = {'facility': facility, 'notice': CONSENT_NOTICE, 'items': CONSENT_ITEMS}
        return pdf_or_html(request, 'minutes/consent.html', ctx, '録音の同意書')

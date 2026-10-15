"""
利用者ごとの個別の質問（レベル別）を入れる・AI で案を出す（records/questions.py。QuestionSetting を使う事業所だけ）
"""
import datetime
import json
import logging

import anthropic
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View

from ai_assist import trial
from ai_assist.text import JAPANESE_RULES, clean_ai_text
from beneficiaries.models import Beneficiary

from . import questions

logger = logging.getLogger(__name__)


def _beneficiary(request, pk):
    facility = request.user.facility
    if not questions.enabled(facility):
        raise Http404
    return get_object_or_404(Beneficiary, pk=pk, facility=facility)


def section_context(beneficiary):
    """利用者情報の画面の「個別の質問」の欄"""
    cq = questions.child_set(beneficiary)
    levels = [list(x) for x in (cq.levels if cq else [])]
    shown = max(3, len(levels) + 1)
    return {
        'questions_on': True,
        'child_q': cq,
        'child_q_levels': [(i + 1, '\n'.join(levels[i]) if i < len(levels) else '') for i in range(min(shown, questions.MAX_LEVELS))],
        'child_q_stats': questions.level_stats(beneficiary, beneficiary.facility),
        'common_questions': questions.common_questions(beneficiary.facility),
    }


JOURNAL_MAX = 30         # AI に渡す日誌（新しい順にこの件数まで）


def _cut(text, n):
    text = ' '.join((text or '').split())
    return text if len(text) <= n else text[:n] + '…'


def _mask(text, b):
    """名前を {名前} に伏せる（1 字の名前は「さん・くん・ちゃん」が付いたときだけ）"""
    import re
    from .models import RecordTemplate
    text = RecordTemplate.anonymize(text, b)
    for n in {b.first_name, b.last_name, b.first_name_kana, b.last_name_kana}:
        if n and len(n) == 1:
            text = re.sub(re.escape(n) + r'(?=(さん|くん|ちゃん|君))', RecordTemplate.NAME_PLACEHOLDER, text)
    return text


def journal_material(b):
    """
    個別の質問の案に使う日誌：直近の日誌（活動・観察・支援・反応・メモ・質問の答え）と、質問ごとの答えの数。
    名前は {名前} に伏せる
    """
    from .models import DailyRecord
    records = list(DailyRecord.objects.filter(beneficiary=b).prefetch_related('activity_tags')
                   .order_by('-date', '-pk')[:JOURNAL_MAX])
    if not records:
        return ''
    lines, tally = [], {}
    for r in reversed(records):          # 古い順に
        parts = [f'・{r.date.month}/{r.date.day}', r.activity_name or '・'.join(t.name for t in r.activity_tags.all())]
        for label, text, n in (('観察', r.observation_text or r.observation_memo, 160), ('支援', r.support_text or r.support_memo, 100),
                               ('反応', r.reaction_text or r.reaction_memo, 120), ('考察', r.activity_reflection, 100)):
            if (text or '').strip():
                parts.append(f'{label}：{_cut(text, n)}')
        answers = questions.clean(r.question_answers)
        if any(a['answer'] for a in answers):
            parts.append('質問：' + questions.text_for_ai(answers))
        for a in answers:
            if a['answer']:
                t = tally.setdefault((a['kind'], a['text']), {'yes': 0, 'neutral': 0, 'no': 0})
                t[a['answer']] += 1
        lines.append(_mask('　'.join(p for p in parts if p), b))
    out = [f'【日誌（直近 {len(records)} 回・古い順）】', *lines]
    if tally:
        out.append('\n【質問ごとの答えの数（はい／どちらでもない／いいえ）】')
        out += [f'・{"共通" if kind == "common" else "個別"}「{text}」：{t["yes"]}／{t["neutral"]}／{t["no"]}'
                for (kind, text), t in tally.items()]
    return '\n'.join(out)


class ChildQuestionsSaveView(LoginRequiredMixin, View):
    """個別の質問を保存する（レベルごとに 1 行 1 問。空のレベルは詰める）。いまのレベルも変えられる"""

    def post(self, request, pk):
        b = _beneficiary(request, pk)
        levels = []
        for i in range(1, questions.MAX_LEVELS + 1):
            qs = questions.lines(request.POST.get(f'level_{i}', ''))
            if qs:
                levels.append(qs)
        cq = questions.child_set(b, create=True)
        cq.levels = levels
        try:
            level = int(request.POST.get('level') or cq.level)
        except ValueError:
            level = cq.level
        level = max(1, min(level, max(1, len(levels))))
        if level != cq.level:
            cq.level = level
            cq.leveled_at = None
        cq.save()
        messages.success(request, f'個別の質問を保存しました（{len(levels)} レベル・いまはレベル {cq.level}）。')
        return redirect(reverse('beneficiaries:detail', args=[b.pk]) + '#questions')


class ChildQuestionsSuggestView(LoginRequiredMixin, View):
    """日誌（直近の記録と質問の答え）・個別支援計画の目標・アセスメントから、個別の質問の案（3 レベル × 4〜5 問）を AI で作る。保存はしない（職員が直して保存）"""

    SYSTEM_PROMPT = """あなたは放課後等デイサービスの児童発達支援管理責任者を補助するAIです。
利用者の日誌・個別支援計画の目標・アセスメントから、職員が毎回の日誌で「はい／どちらでもない／いいえ」で答える「個別の質問」を作ります。

- 【日誌】をいちばん大事にする。日誌から、もうできていること（「はい」が続く）、できかけていること、
  つまずく場面（「いいえ」「どちらでもない」・メモや反応に出てくる場面）を読み取り、日誌に出てくる活動・場面の言葉で書く
- レベル 1〜3 の 3 段階。レベル 1 は日誌でよく出る場面の「できかけていること」、レベル 2・3 はその次の段階（個別支援計画の目標に近づく順）
- もうほとんど「はい」の質問は入れない。{名前} は本人のこと（質問の文には名前を書かない）
- 1 レベル 4〜5 問。1 問 25 字以内の「〜できた」の形で、その日の様子を見ればすぐ答えられる、目に見える行動で書く
- 全員に聞く共通の質問（活動に参加できた／静かに座れた／指示を聞けていた／指示どおりに動けた）と同じことは書かない
- 診断名・評価の言葉・あいまいな言葉（頑張れた、落ち着いていた など）は使わない

""" + JAPANESE_RULES + """

必ず次の JSON だけを返してください。
{"levels": [["レベル1の質問", "..."], ["レベル2の質問", "..."], ["レベル3の質問", "..."]]}"""

    def post(self, request, pk):
        b = _beneficiary(request, pk)
        if not settings.ANTHROPIC_API_KEY:
            return JsonResponse({'error': 'AI の設定（ANTHROPIC_API_KEY）がありません。'}, status=500)
        over = trial.check(request.user.facility)
        if over:
            return JsonResponse({'error': over}, status=403)
        material = self.material(b)
        if not material:
            return JsonResponse({'error': '日誌・個別支援計画・アセスメントがまだありません。日誌を何回か書いてから使うか、質問を直接入れてください。'}, status=400)
        try:
            client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
            response = client.messages.create(
                model=settings.AI_PLAN_MODEL, max_tokens=1500, system=self.SYSTEM_PROMPT,
                messages=[{'role': 'user', 'content': material}],
            )
            trial.use(request.user.facility)
            raw = ''.join(c.text for c in response.content if c.type == 'text').strip()
            data = json.loads(raw[raw.find('{'):raw.rfind('}') + 1], strict=False)
            levels = []
            for lv in (data.get('levels') or [])[:questions.MAX_LEVELS]:
                qs = questions.lines('\n'.join(clean_ai_text(str(q), keep_newlines=False) for q in (lv or [])))
                if qs:
                    levels.append(qs)
            if not levels:
                raise ValueError('empty')
            return JsonResponse({'levels': ['\n'.join(lv) for lv in levels]})
        except (ValueError, json.JSONDecodeError):
            return JsonResponse({'error': 'AI の返答を読み取れませんでした。もう一度お試しください。'}, status=500)
        except anthropic.APIError as e:
            logger.warning('個別の質問の案でエラー: %s', type(e).__name__)
            return JsonResponse({'error': 'AI サービスでエラーが起きました。少し待ってからお試しください。'}, status=502)

    @staticmethod
    def material(b):
        parts = []
        plan = b.support_plans.order_by('-created_at').first()
        if plan is not None:
            goals = [f'・{g.get_goal_type_display()}：{g.content}' + (f'（支援：{g.support_content[:120]}）' if g.support_content else '')
                     for g in plan.goals.all()]
            if goals:
                parts.append('【個別支援計画の目標】\n' + '\n'.join(goals))
        from beneficiaries.models import DevelopmentAssessment
        sheet = DevelopmentAssessment.objects.filter(beneficiary=b).order_by('-date', '-pk').first()
        if sheet is not None:
            from support_plans.sources import dev_text
            text = dev_text(sheet)
            if text:
                parts.append('【5領域アセスメント】\n' + text[:1500])
        asm = b.assessments.exclude(content='').order_by('-date', '-pk').first()
        if asm is not None:
            parts.append('【アセスメント】\n' + asm.content[:1500])
        journal = journal_material(b)
        if journal:
            parts.insert(0, journal)
        if not parts:
            return ''
        cq = questions.child_set(b)
        if cq and cq.levels:
            parts.append('【いまの個別の質問（参考。よければ活かす）】\n' + '\n'.join(
                f'レベル{i + 1}：' + '／'.join(lv) for i, lv in enumerate(cq.levels)))
        if b.date_of_birth:
            today = datetime.date.today()
            age = today.year - b.date_of_birth.year - ((today.month, today.day) < (b.date_of_birth.month, b.date_of_birth.day))
            parts.insert(0, f'【年齢】{age} 歳')
        return '\n\n'.join(parts)

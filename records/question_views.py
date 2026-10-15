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
    """個別支援計画の目標・アセスメントから、個別の質問の案（3 レベル × 4〜5 問）を AI で作る。保存はしない（職員が直して保存）"""

    SYSTEM_PROMPT = """あなたは放課後等デイサービスの児童発達支援管理責任者を補助するAIです。
利用者の個別支援計画の目標とアセスメントから、職員が毎回の日誌で「はい／どちらでもない／いいえ」で答える「個別の質問」を作ります。

- レベル 1〜3 の 3 段階。レベル 1 は今できかけていること、レベル 2・3 は少しずつ難しくする（同じ目標の次の段階）
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
            return JsonResponse({'error': '個別支援計画の目標やアセスメントがまだありません。先に計画を作るか、質問を直接入れてください。'}, status=400)
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

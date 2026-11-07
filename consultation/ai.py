"""
相談支援の計画（申請者の現状・サービス等利用計画）の AI 下書き。
材料は support_plans/material.py と同じ（療育記録・アセスメント・5 領域・書類から分かっていること）に、
児童発達支援の個別支援計画（実施中）の方針・目標を足す。決めるのは相談支援専門員なので下書きとして返す。
"""
import json
from datetime import date, timedelta

import anthropic
from django.conf import settings

from ai_assist.text import JAPANESE_RULES, clean_ai_text, effort_kwargs
from support_plans import material
from support_plans.models import SupportPlan

from .models import CHILD_STATE_KEYS

PROMPT = """あなたは障害児相談支援・計画相談支援の相談支援専門員を補助するAIです。
利用者（お子さま）についての材料（台帳のアセスメント、取り込んだ書類、療育記録、児童発達支援事業所の個別支援計画）をもとに、
相談支援の書類「申請者の現状（基本情報）」と「障害児支援利用計画（サービス等利用計画）」の下書きを作ります。
- 材料に書かれている事実だけを使い、推測で診断名や家庭の事情を書かない。無い項目は空にする
- 概要は支援経過・現状と課題を時系列で 200〜400 字。本児の様子は食事／排泄／着脱・整容／言語／学習／運動／社会性／健康面の 8 項目をそれぞれ 1〜3 文
- 意向（希望する生活）は本人と家族を分けて書く。方針は意向と目標につながるものを 100〜200 字
- 長期目標は 1 年程度、短期目標は半年程度で目指す姿。課題の表（rows）は優先順位の順に 2〜4 行。各行に 解決すべき課題（本人のニーズ）・支援目標・達成時期・
  福祉サービス等（種類・内容・量）・提供事業者名・本人の役割・評価時期・その他留意事項
- 児童発達支援事業所の個別支援計画があれば、その方針や目標と矛盾しないようにする（相談支援の計画が上位の計画）
- です/ます調

""" + JAPANESE_RULES + """

必ず次のJSONだけを返してください（文字列内で改行しない）。
{"overview": "概要", "child_state": {"meal": "", "toilet": "", "dress": "", "language": "", "study": "", "motor": "", "social": "", "health": ""},
 "complaint_child": "本人の主訴", "complaint_family": "家族の主訴", "wishes": "意向（本人／家族）", "policy": "総合的な支援の方針",
 "long_goal": "長期目標", "short_goal": "短期目標",
 "rows": [{"priority": "1", "issue": "", "goal": "", "timing": "", "service": "", "provider": "", "role": "", "eval_timing": "", "notes": ""}],
 "life_summary": "サービス提供によって実現する生活の全体像"}"""


def _client():
    return anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)


def _parse_json(res):
    raw = ''.join(b.text for b in res.content if b.type == 'text').strip()
    start, end = raw.find('{'), raw.rfind('}') + 1
    if start < 0 or end <= start:
        raise json.JSONDecodeError('JSONが含まれていない', raw, 0)
    data = json.loads(raw[start:end], strict=False)
    if not isinstance(data, dict):
        raise json.JSONDecodeError('object expected', raw, 0)
    return data


def support_plan_text(beneficiary):
    """児童発達支援の個別支援計画（実施中か作成中の最新）の方針・目標"""
    plan = (SupportPlan.objects.filter(beneficiary=beneficiary).exclude(status=SupportPlan.STATUS_CLOSED)
            .order_by('-created_at').first())
    if not plan:
        return ''
    d = plan.get_step(SupportPlan.STEP_DRAFT)
    lines = [f'■ 児童発達支援の個別支援計画「{plan.title}」（{plan.facility.name}）']
    if d.policy:
        lines.append('方針：' + d.policy)
    for g in plan.goals.all():
        lines.append(f'・{g.get_goal_type_display()}：{g.content}')
    return '\n'.join(lines)


def draft(cp, months=6):
    """戻り値 (dict, 使った記録数)。dict は PROMPT の JSON の形（整えたもの）＋ sources"""
    b = cp.beneficiary
    end = date.today()
    start = end - timedelta(days=30 * months)
    m = material.collect(b, start, end, limit=60, exclude_consultation=cp.pk)
    sp = support_plan_text(b)
    if not (m['lines'] or m['ledger'] or m['documents'] or m['service_plan'] or sp):
        return None, 0
    user = material.prompt_blocks(m, start, end)
    if sp:
        user += '\n\n【児童発達支援の個別支援計画】\n' + sp
    res = _client().messages.create(model=settings.AI_TEXT_MODEL, max_tokens=5000, **effort_kwargs(settings.AI_TEXT_MODEL, 'medium'),
                                    system=PROMPT, messages=[{'role': 'user', 'content': user}])
    data = _parse_json(res)
    out = {k: clean_ai_text(data.get(k, '')) for k in ('overview', 'complaint_child', 'complaint_family', 'wishes', 'policy',
                                                          'long_goal', 'short_goal', 'life_summary')}
    cs = data.get('child_state') if isinstance(data.get('child_state'), dict) else {}
    out['child_state'] = {k: clean_ai_text(cs.get(k, '')) for k, _ in CHILD_STATE_KEYS}
    rows = []
    for i, r in enumerate(data.get('rows', [])[:6], 1):
        if not isinstance(r, dict):
            continue
        row = {k: clean_ai_text(str(r.get(k, '')), keep_newlines=False)[:400] for k in
               ('priority', 'issue', 'goal', 'timing', 'service', 'provider', 'role', 'eval_timing', 'notes')}
        row['priority'] = row['priority'] or str(i)
        rows.append(row)
    out['rows'] = rows
    out['sources'] = m['sources'] + (['児童発達支援の個別支援計画'] if sp else [])
    return out, len(m['lines'])

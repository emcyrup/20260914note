"""
個別支援計画の AI 支援：
- 過去の日誌からアセスメントの下書き
- 期間の日誌から原案（方針・目標）の下書きと根拠記録のひもづけ
- 目標に関連する記録の検索（根拠の候補）
"""
import json
import logging
import math
from collections import Counter
from datetime import date, timedelta

import anthropic
from django.conf import settings

from ai_assist.retrieval import tokenize
from ai_assist.text import JAPANESE_RULES, clean_ai_text, effort_kwargs
from records.models import DailyRecord

from .models import PlanGoal

logger = logging.getLogger(__name__)
ANSWER_LABEL = {'yes': 'はい', 'no': 'いいえ'}


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


def record_text(r):
    """日誌1件を検索・要約用の1つの文字列に"""
    parts = [r.activity_name, r.observation_text, r.support_text, r.reaction_text, r.activity_reflection, r.observation_memo]
    parts += [f'{v.get("text")}：{ANSWER_LABEL.get(v.get("answer"), "未確認")}' for v in (r.activity_viewpoints or [])]
    return '\n'.join(p for p in parts if p)


def record_line(r, limit=160):
    """プロンプト用の1行要約"""
    tags = '・'.join(t.name for t in r.activity_tags.all()[:3])
    body = ' / '.join(p for p in (r.observation_text, r.reaction_text) if p)[:limit]
    vps = ' '.join(f'{v.get("text")}={ANSWER_LABEL.get(v.get("answer"), "?")}' for v in (r.activity_viewpoints or []) if v.get('answer'))
    return f'- {r.date} {r.activity_name or tags}: {body}' + (f'（観点: {vps}）' if vps else '')


def records_for(beneficiary, start, end, limit=60):
    return list(DailyRecord.objects.filter(beneficiary=beneficiary, date__range=(start, end))
                .prefetch_related('activity_tags').order_by('-date')[:limit])


# =============================================
# 根拠の候補：目標に関連する記録を検索
# =============================================
def find_evidence(beneficiary, query, start, end, top_k=20):
    """期間内の日誌を、目標文との関連度（BM25）順に返す"""
    records = records_for(beneficiary, start, end, limit=400)
    q_terms = set(tokenize(query))
    docs = [(r, Counter(tokenize(record_text(r)))) for r in records]
    n = len(docs) or 1
    avg = (sum(sum(c.values()) for _, c in docs) / n) or 1
    df = Counter(t for _, c in docs for t in q_terms if t in c)
    scored = []
    for r, c in docs:
        dl = sum(c.values()) or 1
        score = 0.0
        for t in q_terms:
            tf = c.get(t)
            if not tf:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            score += idf * tf * 2.5 / (tf + 1.5 * (0.25 + 0.75 * dl / avg))
        scored.append((score, r))
    scored.sort(key=lambda x: (-x[0], x[1].date))
    return [{'record': r, 'score': round(s, 2), 'snippet': record_text(r)[:120]} for s, r in scored[:top_k]]


# =============================================
# アセスメントの下書き
# =============================================
ASSESSMENT_PROMPT = """あなたは放課後等デイサービスの児童発達支援管理責任者を補助するAIです。
過去の日誌（支援記録）をもとに、アセスメント（情報収集）の下書きを作ります。
- 日誌に書かれている事実だけを使い、推測で診断名や家庭の事情を書かない
- 「〜が多い」「〜の場面で〜」のように、複数の記録から読み取れる傾向として書く
- 分からない項目は「（日誌からは不明。面談で確認）」と書く
- です/ます調。各項目 150〜250字

""" + JAPANESE_RULES + """

必ず次のJSONだけを返してください（文字列内で改行しない）。
{"condition": "心身の状況（発達・健康・得意/苦手・コミュニケーションの特徴）", "environment": "置かれている環境（利用頻度・学校・生活リズムなど、日誌から分かる範囲）", "wishes": "本人の希望として読み取れること（本人の言動から）。家族の希望は面談で確認と書く"}"""


def assessment_draft(plan, months=6):
    """過去の日誌からアセスメントの下書きを作る。戻り値 dict と使った記録数"""
    b = plan.beneficiary
    end = date.today()
    start = end - timedelta(days=30 * months)
    records = records_for(b, start, end, limit=60)
    if not records:
        return None, 0
    lines = '\n'.join(record_line(r) for r in records)
    user = (f'【利用者】{b.full_name}（{b.date_of_birth} 生、{b.disability_type or "障害種別未記入"}、利用予定曜日 {b.scheduled_weekdays_display}）\n'
            f'【期間】{start} 〜 {end}（日誌 {len(records)} 件）\n\n【日誌】\n{lines}')
    res = _client().messages.create(model=settings.AI_TEXT_MODEL, max_tokens=2500, **effort_kwargs(settings.AI_TEXT_MODEL, 'medium'),
                                    system=ASSESSMENT_PROMPT, messages=[{'role': 'user', 'content': user}])
    data = _parse_json(res)
    return {k: clean_ai_text(data.get(k, '')) for k in ('condition', 'environment', 'wishes')}, len(records)


# =============================================
# 原案の下書き（方針・目標・根拠）
# =============================================
DRAFT_PROMPT = """あなたは放課後等デイサービスの児童発達支援管理責任者を補助するAIです。
アセスメントと期間内の日誌をもとに、個別支援計画（原案）の下書きを作ります。決めるのは人なので、下書きとして提案してください。
- 目標は日誌の事実に根拠があるものだけ。各目標に、根拠にした日誌の日付（YYYY-MM-DD）を1〜4件挙げる
- 長期目標 1〜2件、短期目標 2〜3件。短期目標には具体的な支援内容（誰が・いつ・どのように）を書く
- 文は「〜できる」「〜が増える」のように達成が確かめられる形に
- です/ます調。方針は 150〜250字

""" + JAPANESE_RULES + """

必ず次のJSONだけを返してください（文字列内で改行しない）。
{"policy": "総合的な支援の方針", "family_wishes": "本人・家族の意向（アセスメントから）",
 "goals": [{"type": "long|short", "content": "目標", "support_content": "具体的な支援内容（短期は必須）", "frequency": "頻度・時間", "evidence_dates": ["YYYY-MM-DD"]}]}"""


def plan_draft(plan, start, end):
    """期間の日誌から原案を作り、目標を根拠つきで登録する。戻り値 (作成した目標数, 使った記録数)"""
    b = plan.beneficiary
    a = plan.get_step(1)
    d = plan.get_step(2)
    records = records_for(b, start, end, limit=80)
    if not records:
        return 0, 0
    by_date = {}
    for r in records:
        by_date.setdefault(r.date.isoformat(), r)
    lines = '\n'.join(record_line(r) for r in records)
    user = (f'【利用者】{b.full_name}（{b.date_of_birth} 生、{b.disability_type or "障害種別未記入"}）\n'
            f'【アセスメント】\n心身の状況: {a.condition}\n置かれている環境: {a.environment}\n希望する生活: {a.wishes}\n\n'
            f'【期間】{start} 〜 {end}（日誌 {len(records)} 件）\n\n【日誌】\n{lines}')
    res = _client().messages.create(model=settings.AI_TEXT_MODEL, max_tokens=3000, **effort_kwargs(settings.AI_TEXT_MODEL, 'medium'),
                                    system=DRAFT_PROMPT, messages=[{'role': 'user', 'content': user}])
    data = _parse_json(res)

    if not d.policy.strip() and data.get('policy'):
        d.policy = clean_ai_text(data['policy'])
    if not d.family_wishes.strip() and data.get('family_wishes'):
        d.family_wishes = clean_ai_text(data['family_wishes'])
    d.save()

    created = 0
    for item in data.get('goals', [])[:6]:
        gtype = PlanGoal.TYPE_LONG if str(item.get('type', '')).startswith('long') else PlanGoal.TYPE_SHORT
        content = clean_ai_text(item.get('content', ''), keep_newlines=False)[:200]
        if not content:
            continue
        months = 6 if gtype == PlanGoal.TYPE_LONG else 3
        g = PlanGoal.objects.create(
            plan=plan, goal_type=gtype, content=content,
            support_content=clean_ai_text(item.get('support_content', '')),
            frequency=clean_ai_text(item.get('frequency', ''), keep_newlines=False)[:100],
            target_date=(d.period_start or start) + timedelta(days=30 * months),
            order=plan.goals.filter(goal_type=gtype).count(),
        )
        evidence = [by_date[x] for x in item.get('evidence_dates', []) if x in by_date]
        if evidence:
            g.evidence_records.set(evidence)
        created += 1
    return created, len(records)

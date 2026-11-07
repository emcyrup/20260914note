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
    if r.question_answers:                 # 共通の質問・個別の質問（records/questions.py）
        from records.questions import text_for_ai
        parts.append(text_for_ai(r.question_answers))
    return '\n'.join(p for p in parts if p)


def record_line(r, limit=160):
    """プロンプト用の1行要約"""
    tags = '・'.join(t.name for t in r.activity_tags.all()[:3])
    body = ' / '.join(p for p in (r.observation_text, r.reaction_text) if p)[:limit]
    vps = ' '.join(f'{v.get("text")}={ANSWER_LABEL.get(v.get("answer"), "?")}' for v in (r.activity_viewpoints or []) if v.get('answer'))
    qa = ''
    if r.question_answers:                 # 共通の質問・個別の質問（records/questions.py）
        from records.questions import text_for_ai
        qa = text_for_ai(r.question_answers)
    return f'- {r.date} {r.activity_name or tags}: {body}' + (f'（観点: {vps}）' if vps else '') + (f'（質問: {qa}）' if qa else '')


def records_for(beneficiary, start, end, limit=60):
    return list(DailyRecord.objects.filter(beneficiary=beneficiary, facility_id=beneficiary.facility_id, date__range=(start, end))
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
ASSESSMENT_PROMPT = """あなたは放課後等デイサービス・児童発達支援の児童発達支援管理責任者を補助するAIです。
台帳のアセスメント（面談・聞き取り・5領域・留意点）、取り込んだ書類（フェイスシート・アセスメントシート・相談支援事業所の基本情報や
サービス等利用計画・診断書など）、期間の記録（療育記録または日誌）をもとに、個別支援計画のアセスメント（情報収集）の下書きを作ります。
- 材料に書かれている事実だけを使い、推測で診断名や家庭の事情を書かない。どの材料から分かったことかが読み取れるように書く
  （例：「相談支援の基本情報では〜」「10月の療育記録では〜」）
- 「〜が多い」「〜の場面で〜」のように、複数の記録から読み取れる傾向として書く
- 分からない項目は「（材料からは不明。面談で確認）」と書く
- 希望する生活は、本人・家族の意向・願い・主訴として書類にあるものを優先し、書類に無ければ本人の言動から読み取れることを書く
- です/ます調。各項目 150〜300字

""" + JAPANESE_RULES + """

必ず次のJSONだけを返してください（文字列内で改行しない）。
{"condition": "心身の状況（発達・健康・得意/苦手・コミュニケーションの特徴）", "environment": "置かれている環境（家庭・園や学校・利用頻度・生活リズム・関係機関など、材料から分かる範囲）", "wishes": "希望する生活（本人の希望、家族の希望。書類にある意向・願いを生かす）"}"""


def assessment_draft(plan, months=6):
    """台帳・書類・期間の記録からアセスメントの下書きを作る。戻り値 dict と使った記録数（dict には sources も入る）"""
    from . import material
    b = plan.beneficiary
    end = date.today()
    start = end - timedelta(days=30 * months)
    m = material.collect(b, start, end, limit=60)
    if not (m['lines'] or m['ledger'] or m['documents'] or m['service_plan']):
        return None, 0
    user = material.prompt_blocks(m, start, end)
    res = _client().messages.create(model=settings.AI_TEXT_MODEL, max_tokens=3000, **effort_kwargs(settings.AI_TEXT_MODEL, 'medium'),
                                    system=ASSESSMENT_PROMPT, messages=[{'role': 'user', 'content': user}])
    data = _parse_json(res)
    out = {k: clean_ai_text(data.get(k, '')) for k in ('condition', 'environment', 'wishes')}
    out['sources'] = m['sources']
    return out, len(m['lines'])


# =============================================
# 原案の下書き（方針・目標・根拠）
# =============================================
DRAFT_PROMPT = """あなたは放課後等デイサービス・児童発達支援の児童発達支援管理責任者を補助するAIです。
アセスメント・取り込んだ書類・期間の記録（療育記録または日誌）をもとに、個別支援計画（原案）の下書きを作ります。決めるのは人なので、下書きとして提案してください。
- 相談支援事業所のサービス等利用計画があれば、総合的な支援の方針と目標はその方針・目標・本人と家族の意向に沿わせる（文言をそのまま写すのではなく、この事業所での支援に落とし込む）
- 目標は記録や書類の事実に根拠があるものだけ。各目標に、根拠にした記録の日付（YYYY-MM-DD）を 1〜4 件挙げる（記録が無い目標は書類を根拠にし、evidence_dates は空）
- 長期目標 1〜2 件、短期目標 2〜4 件。短期目標には具体的な支援内容（誰が・いつ・どのように）、項目（例：健康・生活／言語・コミュニケーション／人間関係・社会性／運動・感覚／認知・行動／家族支援）、担当者（例：児発管・担当職員・保護者）を書く
- 文は「〜できる」「〜が増える」のように達成が確かめられる形に
- です/ます調。方針は 150〜250字

""" + JAPANESE_RULES + """

必ず次のJSONだけを返してください（文字列内で改行しない）。
{"policy": "総合的な支援の方針", "family_wishes": "本人・家族の意向（書類・アセスメントから）",
 "goals": [{"type": "long|short", "item": "項目", "content": "目標", "support_content": "具体的な支援内容（短期は必須）", "frequency": "頻度・時間", "staff": "担当者・提供機関", "notes": "留意事項（本人の役割を含む）", "evidence_dates": ["YYYY-MM-DD"]}]}"""


def plan_draft(plan, start, end):
    """期間の記録と書類から原案を作り、目標を根拠つきで登録する。戻り値 (作成した目標数, 使った記録数)"""
    from . import material
    b = plan.beneficiary
    a = plan.get_step(1)
    d = plan.get_step(2)
    m = material.collect(b, start, end, limit=80)
    if not (m['lines'] or m['documents'] or m['service_plan']):
        return 0, 0
    by_date = {}
    for x in m['lines']:
        if x.record is not None:
            by_date.setdefault(x.date.isoformat(), x.record)
    user = (f'【アセスメント（個別支援計画のステップ1）】\n心身の状況: {a.condition}\n置かれている環境: {a.environment}\n希望する生活: {a.wishes}\n\n'
            + material.prompt_blocks(m, start, end))
    res = _client().messages.create(model=settings.AI_TEXT_MODEL, max_tokens=4000, **effort_kwargs(settings.AI_TEXT_MODEL, 'medium'),
                                    system=DRAFT_PROMPT, messages=[{'role': 'user', 'content': user}])
    data = _parse_json(res)

    if not d.policy.strip() and data.get('policy'):
        d.policy = clean_ai_text(data['policy'])
    if not d.family_wishes.strip() and data.get('family_wishes'):
        d.family_wishes = clean_ai_text(data['family_wishes'])
    d.save()

    created = 0
    for item in data.get('goals', [])[:8]:
        gtype = PlanGoal.TYPE_LONG if str(item.get('type', '')).startswith('long') else PlanGoal.TYPE_SHORT
        content = clean_ai_text(item.get('content', ''), keep_newlines=False)[:200]
        if not content:
            continue
        months = 6 if gtype == PlanGoal.TYPE_LONG else 3
        extra = {k: clean_ai_text(item.get(k, ''), keep_newlines=False)[:200] for k in ('item', 'staff', 'notes') if item.get(k)}
        g = PlanGoal.objects.create(
            plan=plan, goal_type=gtype, content=content,
            support_content=clean_ai_text(item.get('support_content', '')),
            frequency=clean_ai_text(item.get('frequency', ''), keep_newlines=False)[:100],
            target_date=(d.period_start or start) + timedelta(days=30 * months),
            order=plan.goals.filter(goal_type=gtype).count(), form_extra=extra,
        )
        evidence = [by_date[x] for x in item.get('evidence_dates', []) if x in by_date]
        if evidence:
            g.evidence_records.set(evidence)
        created += 1
    return created, len(m['lines'])


# =============================================
# モニタリングの下書き（目標ごとの達成状況・見直しの要否）
# =============================================
MONITORING_PROMPT = """あなたは放課後等デイサービス・児童発達支援の児童発達支援管理責任者を補助するAIです。
個別支援計画の目標と支援内容、計画を作ってからの期間の記録（療育記録または日誌）、前回のモニタリングをもとに、
モニタリング記録の下書きを作ります。決めるのは人なので、下書きとして提案してください。
- 記録に書かれている事実だけを使い、推測で書かない。目標ごとに、記録から読み取れる変化・できるようになったこと・まだ難しいことを書く
- 支援内容が計画どおり行われているか（実施状況）は、記録にある活動と支援内容を突き合わせて書く
- 達成状況は achieved（達成）／progressing（順調）／partial（一部達成）／not_yet（未達成）から、記録の事実に合うものを 1 つ
- 見直しが必要なのは、目標を達成した／目標が合わなくなった／支援内容が実際と違う／本人や家庭の状況が大きく変わった とき
- です/ます調。実施状況 150〜250字、達成状況の詳細は目標ごとに 1〜3 文

""" + JAPANESE_RULES + """

必ず次のJSONだけを返してください（文字列内で改行しない）。
{"implementation": "計画通りに支援できているか（支援内容の実施状況）", "achievement": "achieved|progressing|partial|not_yet",
 "achievement_detail": "達成状況の詳細・本人の変化（目標ごとに「目標：…」で始める）", "review_needed": true,
 "review_reason": "見直しが必要な理由・次の計画への課題（不要なら空）",
 "per_goal": [{"goal": "目標の文（そのまま）", "status": "achieved|progressing|partial|not_yet", "note": "根拠になった記録の要点と日付"}]}"""


def monitoring_draft(plan, start, end):
    """
    モニタリング記録の下書き。戻り値 (dict, 使った記録数)。dict は implementation / achievement / achievement_detail /
    review_needed / review_reason / per_goal / sources
    """
    from . import material
    b = plan.beneficiary
    m = material.collect(b, start, end, limit=80)
    if not m['lines']:
        return None, 0
    d = plan.get_step(2)
    goals = []
    for g in plan.goals.order_by('goal_type', 'order', 'pk'):
        e = g.form_extra or {}
        line = f'- {g.get_goal_type_display()}：{g.content}'
        if g.target_date:
            line += f'（達成時期 {g.target_date}）'
        if g.support_content.strip():
            line += f'\n  支援内容：{g.support_content.strip()}'
        if e.get('item') or e.get('staff'):
            line += f'\n  項目：{e.get("item", "")}　担当：{e.get("staff", "")}'
        goals.append(line)
    prev = plan.monitoring_records.order_by('-date').first()
    prev_text = ''
    if prev:
        prev_text = (f'\n\n【前回のモニタリング {prev.date}】達成状況：{prev.get_achievement_display()}\n{prev.implementation}\n{prev.achievement_detail}'
                     + (f'\n見直し：{prev.review_reason}' if prev.review_reason else ''))
    user = (f'【個別支援計画「{plan.title}」】期間 {d.period_start or "未設定"} 〜 {d.period_end or "未設定"}\n'
            f'総合的な支援の方針：{d.policy}\n【目標】\n' + '\n'.join(goals) + prev_text + '\n\n'
            + material.prompt_blocks({**m, 'ledger': '', 'documents': '', 'service_plan': m['service_plan']}, start, end))
    res = _client().messages.create(model=settings.AI_TEXT_MODEL, max_tokens=3500, **effort_kwargs(settings.AI_TEXT_MODEL, 'medium'),
                                    system=MONITORING_PROMPT, messages=[{'role': 'user', 'content': user}])
    data = _parse_json(res)
    achievement = data.get('achievement') if data.get('achievement') in ('achieved', 'progressing', 'partial', 'not_yet') else 'progressing'
    per_goal = []
    for x in data.get('per_goal', [])[:10]:
        if isinstance(x, dict) and x.get('goal'):
            per_goal.append({'goal': clean_ai_text(x.get('goal', ''), keep_newlines=False)[:200],
                             'status': x.get('status') if x.get('status') in ('achieved', 'progressing', 'partial', 'not_yet') else '',
                             'note': clean_ai_text(x.get('note', ''), keep_newlines=False)[:400]})
    return {
        'implementation': clean_ai_text(data.get('implementation', '')),
        'achievement': achievement,
        'achievement_detail': clean_ai_text(data.get('achievement_detail', '')),
        'review_needed': bool(data.get('review_needed')),
        'review_reason': clean_ai_text(data.get('review_reason', '')) if data.get('review_needed') else '',
        'per_goal': per_goal, 'sources': m['sources'],
    }, len(m['lines'])


COMPOSE_PROMPT = """あなたは放課後等デイサービス・児童発達支援の児童発達支援管理責任者を補助するAIです。
個別支援計画書の「欄」ごとに、職員が入れた短いメモを、計画書に載せる文章に整えます。
- メモに書かれていることだけを使う。メモに無い事実・診断・家庭の事情を足さない
- 前提（アセスメント・意向・方針・いまの目標）と食い違わないようにする。前提の内容を繰り返して埋めない
- 欄ごとの書き方のヒントに合わせる。目標は「〜できる」「〜が増える」のように達成が確かめられる形
- いまの欄の文があり、メモが「直し方」の指示（短く・やさしく・具体的に など）なら、いまの文をその指示で直す
- 返すのはその欄に入れる文だけ。見出し・前置き・説明・「」や記号での囲みは付けない

""" + JAPANESE_RULES


def compose_cells(plan, items, context):
    """
    欄ごとの整文。items は [{'field', 'label', 'hint', 'memo', 'current'}]。戻り値 [{'field', 'result'}]（失敗した欄は result が ''）。
    AI は欄ごとに 1 回ずつたずねる（欄が独立していて、1 欄の失敗がほかに響かないように）
    """
    from ai_assist.quick import ask_ai
    out = []
    for item in items:
        content = (f'{context}\n\n【欄】{item["label"]}\n【書き方のヒント】{item.get("hint") or "計画書の文として"}\n'
                   + (f'【いまの欄の文】\n{item["current"]}\n' if item.get('current') else '')
                   + f'【メモ】\n{item["memo"]}')
        raw, error = ask_ai(COMPOSE_PROMPT, content, 1200)
        out.append({'field': item['field'], 'result': clean_ai_text(raw) if raw else '',
                    'error': '' if raw else 'AI が使えませんでした'})
    return out

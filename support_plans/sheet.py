"""
個別支援計画を「様式のまま」直接編集する画面の、欄の定義と保存（support_plans/views.py の PlanSheetView）

- 画面は使っている様式（標準の計画書か、はぴりす様式の別紙1）と同じ並びで、欄がそのまま入力欄になる
- 上の「文を作る」でメモから欄の文を作り、入れる欄を選ぶ。欄ごとの「メモ → 整文」もできる（ai.compose_cells）
- 保存は draft（意向・方針・留意事項）、plan.form_extra（提供時間など）、目標（内容・支援内容・頻度・達成時期・様式の追加項目）
"""
import datetime

from custom_forms.views import GOAL_EXTRA_FIELDS, PLAN_EXTRA_FIELDS

from .models import PlanGoal, SupportPlan

# 計画の欄：(キー, 見出し, 置き場所, AI への書き方のヒント)。置き場所は draft（原案）か extra（様式の追加項目）
PLAN_CELLS = [
    ('family_wishes', '本人・家族の意向', 'draft', '本人と家族が望んでいることを、面談で聞いた言葉を生かして 2〜4 文で'),
    ('policy', '総合的な支援の方針', 'draft', '意向と目標につながる方針を 150〜250 字で'),
    ('service_hours', '支援の標準的な提供時間等（曜日・頻度、時間）', 'extra', '曜日・頻度・時間を 1 行で（例：月・水・金 15:00〜17:30）'),
    ('notes', '留意事項', 'draft', '支援にあたって気をつけることを箇条書きではなく 1〜3 文で'),
]
# 様式の追加項目のうち、はぴりす様式の画面に出すもの（PLAN_EXTRA_FIELDS から）
EXTRA_IN_SHEET = ['usage_form', 'specialists', 'medical_care', 'review_cycle']
# 目標の欄：(キー, 見出し, 置き場所, ヒント)。置き場所は goal（目標そのもの）か extra（goal.form_extra）
GOAL_CELLS = [
    ('item', '項目', 'extra', '支援の項目名を短く（例：言語・コミュニケーション）'),
    ('content', '支援目標（具体的な到達目標）', 'goal', '「〜できる」「〜が増える」のように達成が確かめられる形で 1 文、60 字以内'),
    ('support_content', '支援内容（内容・支援の提供上のポイント）', 'goal', '誰が・いつ・どのように支援するかを 2〜4 文で'),
    ('frequency', '頻度・時間', 'goal', '例：週3回・活動の前半30分'),
    ('timing', '達成時期（文章）', 'extra', '例：2026年9月末'),
    ('staff', '担当者・提供機関', 'extra', '担当者や職種を短く'),
    ('notes', '留意事項（本人の役割を含む）', 'extra', '本人の役割や配慮を 1〜2 文で'),
]
PLAN_LABELS = {k: label for k, label, _, _ in PLAN_CELLS} | {k: label for k, label, _ in PLAN_EXTRA_FIELDS}
GOAL_LABELS = {k: label for k, label, _, _ in GOAL_CELLS}
HINTS = {k: hint for k, _, _, hint in PLAN_CELLS} | {f'goal.{k}': hint for k, _, _, hint in GOAL_CELLS}
NEW_ROWS = 3        # 空の目標の行をいくつ出すか


def _split(field):
    """欄のキー（'f_policy'・'g12_content'・'n1_content'）→ ('plan' か 'goal', 欄の名前)"""
    if field.startswith('f_'):
        return 'plan', field[2:]
    if '_' in field and field[0] in 'gn' and field[1:field.index('_')].isdigit():
        return 'goal', field.split('_', 1)[1]
    return 'plan', field


def label_of(field):
    kind, key = _split(field)
    return GOAL_LABELS.get(key, key) if kind == 'goal' else PLAN_LABELS.get(key, key)


def hint_of(field):
    kind, key = _split(field)
    return HINTS.get('goal.' + key if kind == 'goal' else key, '')


def plan_values(plan, draft):
    """計画の欄の今の値 {キー: 文}"""
    x = plan.form_extra or {}
    out = {}
    for key, _label, where, _hint in PLAN_CELLS:
        out[key] = getattr(draft, key, '') if where == 'draft' else x.get(key, '')
    for key in EXTRA_IN_SHEET:
        out[key] = x.get(key, '')
    return out


def goal_rows(plan):
    """目標の行：[{'goal', 'prefix', 'values': {キー: 文}, 'type', 'target_date'}]"""
    rows = []
    for g in plan.goals.order_by('goal_type', 'order', 'pk'):
        e = g.form_extra or {}
        values = {key: (getattr(g, key, '') if where == 'goal' else e.get(key, '')) for key, _l, where, _h in GOAL_CELLS}
        rows.append({'goal': g, 'prefix': f'g{g.pk}', 'values': values, 'type': g.goal_type,
                     'target_date': g.target_date.isoformat() if g.target_date else ''})
    for i in range(1, NEW_ROWS + 1):
        rows.append({'goal': None, 'prefix': f'n{i}', 'values': {k: '' for k, _l, _w, _h in GOAL_CELLS},
                     'type': PlanGoal.TYPE_SHORT, 'target_date': ''})
    return rows


def _date(value):
    try:
        return datetime.date.fromisoformat(value) if value else None
    except ValueError:
        return None


def save(plan, draft, post):
    """画面の内容を保存する。戻り値は (直した目標の数, 足した目標の数, 消した目標の数)"""
    x = dict(plan.form_extra or {})
    for key, _label, where, _hint in PLAN_CELLS:
        value = post.get(f'f_{key}', '').strip()
        if where == 'draft':
            setattr(draft, key, value)
        else:
            x[key] = value
    for key in EXTRA_IN_SHEET:
        if f'f_{key}' in post:
            x[key] = post.get(f'f_{key}', '').strip()
    draft.save()
    plan.form_extra = x
    plan.save(update_fields=['form_extra', 'updated_at'])

    goal_keys = [k for k, _l, w, _h in GOAL_CELLS if w == 'goal']
    extra_keys = [k for k, _l, w, _h in GOAL_CELLS if w == 'extra']
    edited = added = removed = 0
    for g in list(plan.goals.all()):
        p = f'g{g.pk}_'
        if f'{p}content' not in post:
            continue
        if post.get(f'{p}delete'):
            g.delete()
            removed += 1
            continue
        for key in goal_keys:
            setattr(g, key, post.get(p + key, '').strip()[:200 if key == 'content' else 4000])
        g.goal_type = post.get(f'{p}type') if post.get(f'{p}type') in dict(PlanGoal.TYPE_CHOICES) else g.goal_type
        g.target_date = _date(post.get(f'{p}target_date', ''))
        e = dict(g.form_extra or {})
        for key in extra_keys:
            e[key] = post.get(p + key, '').strip()
        g.form_extra = e
        if not g.content:
            g.delete()
            removed += 1
            continue
        g.save()
        edited += 1
    for i in range(1, NEW_ROWS + 1):
        p = f'n{i}_'
        content = post.get(f'{p}content', '').strip()[:200]
        if not content:
            continue
        gtype = post.get(f'{p}type') if post.get(f'{p}type') in dict(PlanGoal.TYPE_CHOICES) else PlanGoal.TYPE_SHORT
        PlanGoal.objects.create(
            plan=plan, goal_type=gtype, content=content,
            support_content=post.get(f'{p}support_content', '').strip()[:4000],
            frequency=post.get(f'{p}frequency', '').strip()[:100],
            target_date=_date(post.get(f'{p}target_date', '')),
            order=plan.goals.filter(goal_type=gtype).count(),
            form_extra={key: post.get(p + key, '').strip() for key in extra_keys},
        )
        added += 1
    return edited, added, removed


def context_text(plan, draft):
    """AI に渡す、計画の前提（アセスメントといまの欄の内容）"""
    a = plan.get_step(SupportPlan.STEP_ASSESSMENT)
    b = plan.beneficiary
    lines = [f'【利用者】{b.full_name}（{b.date_of_birth} 生、{b.disability_type or "障害種別未記入"}）']
    if a:
        lines.append(f'【アセスメント】心身の状況: {a.condition}\n置かれている環境: {a.environment}\n希望する生活: {a.wishes}')
    if draft.family_wishes:
        lines.append(f'【本人・家族の意向】{draft.family_wishes}')
    if draft.policy:
        lines.append(f'【総合的な支援の方針】{draft.policy}')
    goals = [f'・{g.get_goal_type_display()}：{g.content}' for g in plan.goals.all()]
    if goals:
        lines.append('【いまの目標】\n' + '\n'.join(goals))
    return '\n'.join(lines)

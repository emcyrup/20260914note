"""
個別支援計画を「様式のまま」直接編集する画面の、欄の定義と保存（support_plans/views.py の PlanSheetView）

- 画面は使っている様式（標準の計画書か、はびりす様式の別紙1）と同じ並びで、欄がそのまま入力欄になる
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
# 様式の追加項目のうち、はびりす様式の画面に出すもの（PLAN_EXTRA_FIELDS から）
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
# オウル様式（児童発達支援センター オウル）の計画書：上の欄と、目標の表（項目＝本人のニーズ等／具体的な達成目標／支援内容／達成時期／担当者／留意事項／優先順位）
OWL_PLAN_CELLS = [
    ('family_wishes', '利用児及び家族の生活に対する意向', 'draft', '本人と家族が望んでいることを、面談や相談支援の計画の言葉を生かして 2〜4 文で（本人・家族の別を書く）'),
    ('policy', '総合的な支援の方針', 'draft', '意向と目標につながる方針を 150〜250 字で'),
    ('long_goal_text', '長期目標（内容・期間等）', 'extra', '1 年程度で目指す姿を 1〜2 文で'),
    ('short_goal_text', '短期目標（内容・期間等）', 'extra', '半年程度で目指す姿を 1〜2 文で'),
    ('service_hours', '支援の標準的な提供時間等（曜日・頻度、時間）', 'extra', '曜日・頻度・時間を 1 行で（例：週3回・個別月、水、金 10時〜）'),
    ('transport', '送迎', 'extra', '迎え・送りの有無と場所を 1 行で（例：迎え：あり 園　送り：あり 自宅）'),
    ('notes', '備考', 'draft', '支援にあたって気をつけることや家庭からの連絡事項を 1〜3 文で'),
]
OWL_GOAL_CELLS = [
    ('item', '項目（本人のニーズ等）', 'extra', '支援の区分を短く（例：発達支援／家族支援・移行支援／地域連携）'),
    ('content', '具体的な達成目標', 'goal', '「〜できる」「〜が増える」のように達成が確かめられる形で 1〜2 文'),
    ('support_content', '支援内容（内容・支援の提供上のポイント・5領域との関連性等）', 'goal', '誰が・いつ・どのように支援するかを 2〜4 文で。関係する 5 領域を添える'),
    ('frequency', '頻度・時間', 'goal', '例：週3回・個別'),
    ('timing', '達成時期', 'extra', '例：6か月'),
    ('staff', '担当者・提供機関', 'extra', '例：オウル職員・保護者・保育園'),
    ('notes', '留意事項（本人の役割を含む）', 'extra', '本人の役割や配慮、関係する加算を 1〜2 文で'),
    ('priority', '優先順位', 'extra', '1 から'),
]
OWL_EXTRA_IN_SHEET = []
# 別表（オウル様式）の加算の候補
OWL_ADDONS = ['専門的支援実施加算', '家族支援加算', '子育てサポート加算', '送迎加算', '保育・教育等移行支援加算', '個別サポート加算(I)',
              '個別サポート加算(II)', '個別サポート加算(III)', '関係機関連携加算', '通所自立支援加算', '延長支援加算', '強度行動障害児支援加算',
              '集中的支援加算', '人工内耳装用児支援加算', '医療連携体制加算']
OWL_WEEKDAYS = [('mon', '月'), ('tue', '火'), ('wed', '水'), ('thu', '木'), ('fri', '金'), ('sat', '土'), ('sun', '日・祝日')]
OWL_SLOTS_NOTE = ('※利用曜日や利用時間に変更がある場合は、下記時間枠にてご利用いただきます。\n'
                  '(1)9:00〜9:40、(2)10:00〜10:40、(3)13:00〜13:40、(4)14:00〜14:40、(5)15:00〜15:40\n'
                  '※以下の加算項目を根拠として支援してまいります。')


def is_owl(facility):
    return getattr(facility, 'form_set', '') == 'owl'


def plan_cells(facility):
    return OWL_PLAN_CELLS if is_owl(facility) else PLAN_CELLS


def goal_cells(facility):
    return OWL_GOAL_CELLS if is_owl(facility) else GOAL_CELLS


def extra_in_sheet(facility):
    return OWL_EXTRA_IN_SHEET if is_owl(facility) else EXTRA_IN_SHEET


_ALL_PLAN = PLAN_CELLS + OWL_PLAN_CELLS
_ALL_GOAL = GOAL_CELLS + OWL_GOAL_CELLS
PLAN_LABELS = {k: label for k, label, _, _ in reversed(_ALL_PLAN)} | {k: label for k, label, _ in PLAN_EXTRA_FIELDS}   # 同じキーは標準の見出し
GOAL_LABELS = {k: label for k, label, _, _ in reversed(_ALL_GOAL)}
HINTS = {k: hint for k, _, _, hint in reversed(_ALL_PLAN)} | {f'goal.{k}': hint for k, _, _, hint in reversed(_ALL_GOAL)}
NEW_ROWS = 3        # 空の目標の行をいくつ出すか


def _split(field):
    """欄のキー（'f_policy'・'g12_content'・'n1_content'）→ ('plan' か 'goal', 欄の名前)"""
    if field.startswith('f_'):
        return 'plan', field[2:]
    if '_' in field and field[0] in 'gn' and field[1:field.index('_')].isdigit():
        return 'goal', field.split('_', 1)[1]
    return 'plan', field


def label_of(field, facility=None):
    kind, key = _split(field)
    if facility is not None:
        cells = goal_cells(facility) if kind == 'goal' else plan_cells(facility)
        for k, label, _w, _h in cells:
            if k == key:
                return label
    return GOAL_LABELS.get(key, key) if kind == 'goal' else PLAN_LABELS.get(key, key)


def hint_of(field, facility=None):
    kind, key = _split(field)
    if facility is not None:
        cells = goal_cells(facility) if kind == 'goal' else plan_cells(facility)
        for k, _label, _w, hint in cells:
            if k == key:
                return hint
    return HINTS.get('goal.' + key if kind == 'goal' else key, '')


def plan_values(plan, draft):
    """計画の欄の今の値 {キー: 文}（使っている様式の欄）"""
    x = plan.form_extra or {}
    out = {}
    for key, _label, where, _hint in plan_cells(plan.facility):
        out[key] = getattr(draft, key, '') if where == 'draft' else x.get(key, '')
    for key in extra_in_sheet(plan.facility):
        out[key] = x.get(key, '')
    return out


def goal_rows(plan):
    """目標の行：[{'goal', 'prefix', 'values': {キー: 文}, 'type', 'target_date'}]"""
    cells = goal_cells(plan.facility)
    rows = []
    for g in plan.goals.order_by('goal_type', 'order', 'pk'):
        e = g.form_extra or {}
        values = {key: (getattr(g, key, '') if where == 'goal' else e.get(key, '')) for key, _l, where, _h in cells}
        rows.append({'goal': g, 'prefix': f'g{g.pk}', 'values': values, 'type': g.goal_type,
                     'target_date': g.target_date.isoformat() if g.target_date else ''})
    for i in range(1, NEW_ROWS + 1):
        rows.append({'goal': None, 'prefix': f'n{i}', 'values': {k: '' for k, _l, _w, _h in cells},
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
    for key, _label, where, _hint in plan_cells(plan.facility):
        value = post.get(f'f_{key}', '').strip()
        if where == 'draft':
            setattr(draft, key, value)
        else:
            x[key] = value
    for key in extra_in_sheet(plan.facility):
        if f'f_{key}' in post:
            x[key] = post.get(f'f_{key}', '').strip()
    draft.save()
    plan.form_extra = x
    plan.save(update_fields=['form_extra', 'updated_at'])

    cells = goal_cells(plan.facility)
    goal_keys = [k for k, _l, w, _h in cells if w == 'goal']
    extra_keys = [k for k, _l, w, _h in cells if w == 'extra']
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
            limit = {'content': 200, 'frequency': 100}.get(key, 4000)     # モデルの長さに合わせる
            setattr(g, key, post.get(p + key, '').strip()[:limit])
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

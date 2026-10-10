"""
日誌の「確定の前のチェック」（新人向けの観点チェック）と、良い記録の例

- 施設設定で、日誌を確定するのに要る項目（Facility.journal_required）と、文の最低字数（Facility.journal_min_chars）を選ぶ
- 確定で保存したとき足りない項目があれば、下書きのまま保存して知らせる（records/views.py の _enforce_required）
- 入力画面には「確定の前のチェック」の欄（static/js/journal-check.js）。画面とサーバーは同じ規則（RULES）で見る
- 良い記録の例：標準の例（EXAMPLES）と、事業所が「良い例」に選んだ日誌（DailyRecord.is_good_example。名前を {名前} に伏せて出す）
"""
from .models import DailyRecord

DOMAIN_KEYS = ['domain_health_life', 'domain_motor_sensory', 'domain_cognition_behavior', 'domain_language_comm', 'domain_social']

# 必須にできる項目：(キー, 見出し, 日誌の項目（施設設定の journal_sections。None は常に出る）, 種類, 画面の欄の name)
RULES = [
    ('activity_name', '活動名', 'activity', 'text', 'activity_name'),
    ('activity_aim', 'めあて', 'activity', 'text', 'activity_aim'),
    ('viewpoints', '観点（1つ以上、すべてに「はい／いいえ」）', 'activity', 'viewpoints', 'activity_viewpoints'),
    ('domains', '関連する5領域（1つ以上）', None, 'domains', ''),
    ('observation', '観察・活動内容', 'observation', 'long', 'observation_text'),
    ('support', '支援内容', 'support', 'long', 'support_text'),
    ('reaction', '本人の反応', 'reaction', 'long', 'reaction_text'),
    ('parent_message', '保護者向けメッセージ', 'parent_message', 'text', 'parent_message_draft'),
]
RULE_LABELS = {k: label for k, label, _s, _t, _n in RULES}
MIN_CHARS_MAX = 400


def active_rules(facility):
    """この事業所で見る規則（施設設定で選ばれ、日誌の項目として使っているもの）"""
    chosen = set(getattr(facility, 'journal_required', None) or [])
    sections = set(facility.journal_section_keys()) if facility is not None else set()
    return [r for r in RULES if r[0] in chosen and (r[2] is None or r[2] in sections)]


def min_chars(facility):
    try:
        return max(0, min(MIN_CHARS_MAX, int(getattr(facility, 'journal_min_chars', 0) or 0)))
    except (TypeError, ValueError):
        return 0


def values_of(record):
    """日誌から、チェックに使う値"""
    return {
        'activity_name': record.activity_name or '', 'activity_aim': record.activity_aim or '',
        'activity_viewpoints': record.activity_viewpoints or [],
        'domains': [k for k in DOMAIN_KEYS if getattr(record, k, False)],
        'observation_text': record.observation_text or '', 'support_text': record.support_text or '',
        'reaction_text': record.reaction_text or '', 'parent_message_draft': record.parent_message_draft or '',
    }


def _len(text):
    return len(''.join((text or '').split()))


def missing(values, facility):
    """足りない項目の説明のリスト（空なら確定してよい）"""
    out = []
    n = min_chars(facility)
    for key, label, _section, kind, name in active_rules(facility):
        if kind == 'viewpoints':
            vps = [v for v in (values.get(name) or []) if (v.get('text') or '').strip()]
            if not vps:
                out.append(f'{label}：観点がありません')
            elif any(v.get('answer') not in ('yes', 'no') for v in vps):
                left = sum(1 for v in vps if v.get('answer') not in ('yes', 'no'))
                out.append(f'{label}：{left} つが未確認')
        elif kind == 'domains':
            if not values.get('domains'):
                out.append(label)
        else:
            length = _len(values.get(name))
            if not length:
                out.append(label)
            elif kind == 'long' and n and length < n:
                out.append(f'{label}：{length} 字（{n} 字以上）')
    return out


def check_config(facility):
    """画面（journal-check.js）に渡す規則"""
    return {'min': min_chars(facility),
            'rules': [{'key': k, 'label': label, 'kind': kind, 'name': name} for k, label, _s, kind, name in active_rules(facility)]}


# 標準の良い記録の例（日誌の項目ごと）。事実（何を・どのくらい・どう）を書き、評価や決めつけは書かない
EXAMPLES = [
    {
        'key': 'observation', 'label': '観察・活動内容',
        'points': ['何をしたか（活動の名前と内容）', 'どのくらい（時間・回数・量）', '本人の言葉は「」でそのまま'],
        'good': '15:30 から 20 分、折り紙でかぼちゃを作った。手順カードを見ながら、角を合わせて折る工程を 3 回くり返した。途中で「ここわからん」と言い、職員の手元を見てから続けた。',
        'avoid': '折り紙をした。楽しそうに頑張っていた。',
        'why': '「楽しそう」「頑張った」は見た人の感想で、何をどれだけしたかが分からない。',
    },
    {
        'key': 'support', 'label': '支援内容',
        'points': ['誰が・どのように支援したか', '声かけはその言葉で', '道具や環境の工夫'],
        'good': '手順カードを机の左に置き、1 枚ずつめくって見せた。折り目が合わないときは「角と角をくっつけよう」と声をかけ、最初の 1 回だけ手を添えた。',
        'avoid': '適宜声かけを行い、促した。',
        'why': '「適宜」「促した」では、次の職員が同じ支援をできない。',
    },
    {
        'key': 'reaction', 'label': '本人の反応',
        'points': ['支援のあと本人がどうしたか', '表情・言葉・行動', '前回との違いがあれば事実で'],
        'good': '2 回目からは手を添えなくても角を合わせた。完成すると職員に見せに来て「できた」と言った。前回は途中で席を立ったが、今日は最後まで座って作った。',
        'avoid': '集中力が続かず、わがままを言う場面があった。',
        'why': '「わがまま」は決めつけ。何を言ったか・何をしたかを書く。',
    },
    {
        'key': 'parent_message', 'label': '保護者向けメッセージ',
        'points': ['今日できたことを 1 つ具体的に', '家でも話題にできること', '連絡が要ることは分けて'],
        'good': '今日は折り紙でかぼちゃを作りました。2 回目からは自分で角を合わせて折り、完成すると「できた」と見せてくれました。おうちでも、かぼちゃの話をしてみてください。',
        'avoid': '今日もとても頑張っていました！',
        'why': '何をしたかが伝わらず、毎日同じ文になりやすい。',
    },
    {
        'key': 'activity', 'label': '活動・めあて・観点',
        'points': ['めあては「〜できる」の形', '観点は活動中に見て「はい／いいえ」で答えられる文', '考察は観点の結果から'],
        'good': 'めあて：手順カードを見て、最後まで自分で折ることができる。観点：①カードを見てから折り始めた ②角を合わせて折った ③完成まで席にいた',
        'avoid': 'めあて：楽しく活動する。観点：意欲的に取り組めたか',
        'why': '「楽しく」「意欲的に」は、はい／いいえで答えにくい。',
    },
]


def facility_examples(facility, limit=20):
    """事業所が「良い例」に選んだ日誌（名前は {名前} に伏せる）"""
    from .models import RecordTemplate
    rows = []
    qs = (DailyRecord.objects.filter(facility=facility, is_good_example=True, status=DailyRecord.STATUS_CONFIRMED)
          .select_related('beneficiary').order_by('-date')[:limit])
    for r in qs:
        b = r.beneficiary

        def hide(text):
            return RecordTemplate.anonymize(text or '', b)
        rows.append({'record': r, 'activity_name': hide(r.activity_name), 'activity_aim': hide(r.activity_aim),
                     'viewpoints': [hide(v.get('text', '')) for v in (r.activity_viewpoints or []) if v.get('text')],
                     'observation': hide(r.observation_text), 'support': hide(r.support_text),
                     'reaction': hide(r.reaction_text), 'parent_message': hide(r.parent_message_draft),
                     'note': hide(r.good_example_note)})
    return rows

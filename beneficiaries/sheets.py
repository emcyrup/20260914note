"""
オウルの用紙（2026-11-08 の依頼：相談支援・児童発達支援の様式を利用者ごとに出力できるように）。

- フェイスシート（児童発達支援・保育所等訪問支援）：基本情報・家族・緊急連絡先・診断と手帳・医療・服薬・てんかん・アレルギーと、
  睡眠〜家族の願いまでの聞き取りの項目。紙の用紙と同じ並び
- アセスメントシート：対人関係・生活面・学習面の項目を 3 段階（出来る／あまり出来ない／ほとんど出来ない）で評価し、本人の状態を書く

どちらも値は JSON に入れる（用紙の項目の増減で移行が要らないように）。項目の定義はここにまとめ、画面・PDF・AI の材料で共有する。
"""

# ---------------------------------------------------------------- フェイスシート
# 聞き取りの項目（key, 見出し, 用紙のかっこ書き）
FACE_ITEMS = [
    ('sleep', '睡眠', '就寝時間／起床時間／寝つきの良し悪し／添い寝の有無／寝るとき必要なもの等'),
    ('meal', '食事', '好きな食べ物／嫌いな食べ物／好きなお菓子／箸・スプーン・フォーク使用／介助の有無等'),
    ('toilet', '排泄', '介助の有無／排泄サイン／おしめ／様式・和式・男性用便器／トイレ内環境（暗さ・換気扇の音・自動便座等）への反応等'),
    ('dress', '着脱衣', '介助の有無／前後裏表の認識／感覚的な部分（濡れたら着替える・タグが嫌い・特定の感覚の服が好き等）'),
    ('grooming', '整容', '歯磨き／爪きり／手洗い／耳そうじ／散髪／生理時の対応等'),
    ('communication', 'コミュニケーション', '意思伝達手段・意思伝達理解（下の印）、手段の特徴等伝えておきたいこと'),
    ('behavior', '行動の特徴', '感覚の過敏さ／こだわり／興味／不注意等'),
    ('problem', '問題行動', '自傷・他害／破壊行為／他動・多弁／異食／無断外出／自宅で注意している行動やしてほしくない行動'),
    ('motor', '身体運動', '麻痺／好きな運動／苦手な運動等'),
    ('social', '対人', '集団での活動／好きなタイプ・苦手なタイプ／他者との距離感等'),
    ('outing', '外出', '自宅周辺で迷子になる確率／歩行の様子／車道への飛び出し／外でのこだわり／乗車時のシートベルト着用等'),
    ('holiday', '休日の過ごし方', '好きな遊びやもの／楽しみ等'),
    ('environment', '環境', '初めての場所や人への反応／好む環境・苦手な環境等'),
    ('strengths', '本人の長所・得意なこと', ''),
    ('wish_child', '本人の願い・希望', ''),
    ('grow', '伸ばしてあげたい個性や能力・培ってもらいたい能力', ''),
    ('wish_family', '家族の願い・希望', ''),
    ('other_services', '利用している他事業所のサービス', '児童発達支援（センター含む）／放課後等デイサービス（事業所名と契約日数）、日中一時支援、短期入所、ヘルパー、その他'),
]
FACE_ITEM_LABELS = dict((k, l) for k, l, _ in FACE_ITEMS)

# コミュニケーションの印（意思伝達手段・意思伝達理解。用紙の①〜⑤）
COMM_MEANS = [('1', '①手を引っ張る等の直接的行動'), ('2', '②ジェスチャー・サインの使用'), ('3', '③写真・絵カード'), ('4', '④文字'), ('5', '⑤言葉（日常会話 単語）')]
COMM_UNDERSTAND = [('1', '①実物提示'), ('2', '②ジェスチャー・サイン'), ('3', '③写真・絵カード'), ('4', '④文字'), ('5', '⑤言葉（日常会話 単語）')]

# 頭の欄（key, 見出し）。文字の欄。家族・緊急連絡先は表（下）
FACE_HEAD = [
    ('school', '所属（幼稚園／学校）'), ('grade', '学年／年生'), ('class_type', '学級（情緒・知的・言語・通級）'),
    ('nickname', '愛称'), ('guardian', '保護者氏名'), ('postal', '〒'), ('address', '住所'), ('tel', 'TEL'),
    ('gender', '性別'), ('blood', '血液型'), ('height', '身長（cm）'), ('weight', '体重（kg）'),
    ('diagnosis', '障がい診断（診断なし／診断あり：診断名・何歳／疑いがあると言われた）'), ('diagnosis_org', '診断医療機関・診断者'),
    ('notebook', '所有手帳（療育手帳 A1・A2・B1・B2／精神保健福祉手帳 級／身体障害者手帳 級／手帳なし）'),
    ('hospital', '医療機関（かかりつけ病院・主治医・TEL）'), ('temp_normal', '平熱'), ('temp_fever', '発熱の目安'), ('aids', '補助具の使用'),
    ('medicine', '服薬（定期薬名・服薬方法）'), ('epilepsy', 'てんかん（発作／発作の前兆・発作時の様子・発作後の様子）'),
    ('allergy', 'アレルギー（有・無／食べ物・薬・その他／具体的な原因物質／花粉症・アトピー性皮膚炎・その他）'),
    ('emergency_work_ok', '緊急時、自宅・携帯につながらない場合に勤務先へ TEL してもよい（はい／いいえ）'),
    ('emergency_name_ok', '勤務先へ TEL をした際、事業所名を名乗ってもよい（はい／いいえ）'),
]
FACE_HEAD_LABELS = dict(FACE_HEAD)
FAMILY_COLS = [('name', '名前'), ('relation', '続柄'), ('birth', '生年月日（年齢）'), ('job', '職場・学年'), ('note', '備考')]
FAMILY_ROWS = 5
CONTACT_COLS = [('who', '父・母・その他'), ('where', '自宅・携帯・勤務先・その他'), ('tel', 'TEL')]
CONTACT_ROWS = 3
TEXT_MAX = 2000


def face_from_post(post):
    """画面の送信 → data（JSON）。知らない key は入れない"""
    data = {'head': {}, 'items': {}, 'family': [], 'contacts': [], 'comm_means': [], 'comm_understand': []}
    for k, _ in FACE_HEAD:
        data['head'][k] = (post.get(f'head_{k}') or '').strip()[:TEXT_MAX]
    for k, _, _ in FACE_ITEMS:
        data['items'][k] = (post.get(f'item_{k}') or '').strip()[:TEXT_MAX]
    for i in range(FAMILY_ROWS):
        row = {c: (post.get(f'family_{i}_{c}') or '').strip()[:200] for c, _ in FAMILY_COLS}
        if any(row.values()):
            data['family'].append(row)
    for i in range(CONTACT_ROWS):
        row = {c: (post.get(f'contact_{i}_{c}') or '').strip()[:200] for c, _ in CONTACT_COLS}
        if any(row.values()):
            data['contacts'].append(row)
    data['comm_means'] = [v for v, _ in COMM_MEANS if post.get(f'comm_means_{v}')]
    data['comm_understand'] = [v for v, _ in COMM_UNDERSTAND if post.get(f'comm_understand_{v}')]
    return data


def face_prefill(beneficiary):
    """新しいフェイスシートの頭の欄を台帳から写す（住所・電話・所属・保護者）"""
    g = next((x for x in beneficiary.guardians.all() if x.is_primary), None) or next(iter(beneficiary.guardians.all()), None)
    head = {
        'school': beneficiary.school_name or '', 'grade': beneficiary.get_grade_display() if beneficiary.grade else '',
        'guardian': f'{g.last_name} {g.first_name}' if g else '', 'postal': beneficiary.postal_code or '',
        'address': beneficiary.address or '', 'tel': beneficiary.mobile_phone or beneficiary.home_phone or (g.phone if g else ''),
        'gender': beneficiary.get_gender_display() if getattr(beneficiary, 'gender', '') else '',
    }
    if beneficiary.disability_type:
        head['diagnosis'] = f'診断あり（{beneficiary.disability_type}）'
    return {'head': head, 'items': {}, 'family': [], 'contacts': [], 'comm_means': [], 'comm_understand': []}


def face_text(sheet):
    """AI の材料・検索に使う「項目：値」の文（空の項目は出さない）"""
    d = sheet.data or {}
    out = []
    for k, label in FACE_HEAD:
        v = (d.get('head') or {}).get(k)
        if v:
            out.append(f'{label}：{v}')
    means = [l for v, l in COMM_MEANS if v in (d.get('comm_means') or [])]
    under = [l for v, l in COMM_UNDERSTAND if v in (d.get('comm_understand') or [])]
    if means:
        out.append('意思伝達手段：' + '・'.join(means))
    if under:
        out.append('意思伝達理解：' + '・'.join(under))
    for k, label, _ in FACE_ITEMS:
        v = (d.get('items') or {}).get(k)
        if v:
            out.append(f'{label}：{v}')
    return '\n'.join(out)


# ---------------------------------------------------------------- アセスメントシート
# (key, 項目, 3 段階の言い方)。段階は 1＝できる・良い・ある・安定、2＝あまり、3＝ほとんど
L_DEKIRU = ('出来る', 'あまり出来ない', 'ほとんど出来ない')
L_JOKYO = ('状況に応じて出来る', 'あまり出来ない', 'ほとんど出来ない')
L_ARU = ('ある', 'あまりない', 'ほとんどない')
L_YOI = ('良い', 'あまりない', 'ほとんどない')
L_ANTEI = ('安定している', 'あまり安定していない', 'ほとんど安定していない')
L_JIBUN = ('自分で出来る', 'あまり出来ない', 'ほとんど出来ない')
ASSESS_GROUPS = [
    ('social', '対人関係', [
        ('greeting', '挨拶', L_DEKIRU), ('conversation', '会話', L_JOKYO), ('wording', '言葉遣い', L_JOKYO),
        ('cooperation', '協調性', L_ARU), ('emotion', '感情のコントロール', L_DEKIRU), ('assert', '意思表示', L_DEKIRU),
        ('teamwork', '共同作業', L_DEKIRU), ('peers', '子ども同士の関係', L_YOI), ('adults', '大人との関係', L_YOI),
        ('guardians', '保護者との関係', L_YOI),
    ]),
    ('life', '生活面', [
        ('rhythm', '生活リズム', L_ANTEI), ('mood', '情緒', L_ANTEI), ('meal', '食事', L_JIBUN), ('toilet', '排泄', L_JIBUN),
        ('grooming', '身だしなみ', L_DEKIRU), ('initiative', '行動の自発性', L_ARU), ('join_task', '用意された作業への参加', L_DEKIRU),
        ('prepare', '作業の準備と片付け', L_DEKIRU), ('dexterity', '巧緻性', L_ARU), ('instruction', '指示系統の理解', L_DEKIRU),
        ('focus', '集中力', L_ARU), ('switch', '気持ちと行動の切り替え', ('（気・行）出来る', '（気・行）あまり出来ない', '（気・行）ほとんど出来ない')),
    ]),
    ('study', '学習面', [
        ('literacy', '読み／書き', L_DEKIRU), ('math', '計算／時計', L_DEKIRU),
    ]),
]
ASSESS_ITEMS = [(k, label, levels, gkey, glabel) for gkey, glabel, items in ASSESS_GROUPS for k, label, levels in items]
ASSESS_LABELS = dict((k, label) for k, label, _, _, _ in ASSESS_ITEMS)
LEVELS = (1, 2, 3)


def assess_from_post(post):
    items = {}
    for k, _, _, _, _ in ASSESS_ITEMS:
        level = post.get(f'level_{k}')
        items[k] = {'level': int(level) if level in ('1', '2', '3') else None,
                    'note': (post.get(f'note_{k}') or '').strip()[:TEXT_MAX]}
    return items


def assess_rows(sheet, previous=None):
    """画面・PDF 用の行：グループ・項目・段階の言い方・いまの値・本人の状態・前回の値"""
    items = sheet.items or {}
    prev = (previous.items or {}) if previous is not None else {}
    rows = []
    for k, label, levels, gkey, glabel in ASSESS_ITEMS:
        cur = items.get(k) or {}
        rows.append({'key': k, 'label': label, 'levels': list(zip(LEVELS, levels)), 'group': gkey, 'group_label': glabel,
                     'level': cur.get('level'), 'note': cur.get('note', ''),
                     'prev_level': (prev.get(k) or {}).get('level')})
    return rows


def assess_text(sheet):
    """AI の材料に使う文（項目：段階の言い方／本人の状態）"""
    out = []
    for r in assess_rows(sheet):
        if r['level'] is None and not r['note']:
            continue
        word = dict(r['levels']).get(r['level'], '')
        out.append(f'{r["group_label"]}・{r["label"]}：{word}' + (f'／{r["note"]}' if r['note'] else ''))
    return '\n'.join(out)

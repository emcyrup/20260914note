"""
重身用（重症心身障害児）の日誌に付ける記録項目。

厚生労働省「児童発達支援ガイドライン」「放課後等デイサービスガイドライン」で、重症心身障害児の支援に
あたって把握・記録が求められる、日々の健康状態（バイタル）・食事や水分・排泄・睡眠・発作・医療的ケア・
姿勢や皮膚の状態を項目にしている。事業所で使わない項目は空欄のままでよい。
"""

# (key, 項目名, 入力の種類, 単位や選択肢, 入力例)
# 入力の種類: 'number' 数値, 'text' 1行, 'select' 選択, 'textarea' 複数行
SEVERE_CARE_FIELDS = [
    # --- バイタル ---
    ('temperature',   '体温',       'number', '℃',     '36.8'),
    ('pulse',         '脈拍',       'number', '回/分',  '90'),
    ('spo2',          'SpO2',       'number', '%',     '97'),
    ('respiration',   '呼吸',       'text',   '',      '回/分・喘鳴の有無など'),
    ('blood_pressure','血圧',       'text',   'mmHg',  '100/60'),
    # --- 食事・水分 ---
    ('meal',          '食事',       'select', ['完食', '半量', '少量', '摂取なし', '経管栄養'], ''),
    ('water_ml',      '水分量',     'number', 'ml',    '300'),
    ('tube_feeding',  '経管栄養',   'text',   '',      '注入量・時刻'),
    # --- 排泄 ---
    ('urination',     '排尿',       'number', '回',    '3'),
    ('defecation',    '排便',       'number', '回',    '1'),
    ('stool',         '便の性状',   'select', ['普通', '軟便', '下痢', '硬便', 'なし'], ''),
    # --- 睡眠・発作 ---
    ('sleep',         '睡眠',       'text',   '',      '入眠時刻・眠りの様子'),
    ('seizure',       '発作',       'select', ['なし', 'あり'], ''),
    ('seizure_note',  '発作の様子', 'text',   '',      '時刻・持続時間・対応'),
    # --- 医療的ケア ---
    ('suction',       '吸引',       'number', '回',    '2'),
    ('catheter',      '導尿',       'number', '回',    '0'),
    ('oxygen',        '酸素',       'text',   '',      '流量・時間'),
    ('medical_other', 'その他の医療的ケア', 'text', '', '服薬・吸入など'),
    # --- 姿勢・皮膚・様子 ---
    ('positioning',   '体位変換・姿勢', 'text', '',    '回数・使った姿勢保持具'),
    ('skin',          '皮膚の状態', 'text',   '',      '発赤・褥瘡・かぶれの有無'),
    ('mood',          '機嫌',       'select', ['良い', '普通', '不機嫌', '泣いていた'], ''),
    ('care_memo',     '特記事項',   'textarea', '',    '医療的ケアや体調で気づいたこと'),
]

SEVERE_CARE_KEYS = [f[0] for f in SEVERE_CARE_FIELDS]
SEVERE_CARE_LABELS = {f[0]: f[1] for f in SEVERE_CARE_FIELDS}
SEVERE_CARE_UNITS = {f[0]: (f[3] if isinstance(f[3], str) else '') for f in SEVERE_CARE_FIELDS}


def severe_care_fields(data=None):
    """テンプレート用：入力欄の定義（dict のリスト）。data を渡すと保存済みの値を value に入れる"""
    data = data or {}
    return [
        {
            'key': key, 'label': label, 'kind': kind,
            'unit': unit if isinstance(unit, str) else '',
            'choices': unit if isinstance(unit, list) else [],
            'placeholder': placeholder,
            'value': data.get(key, ''),
        }
        for key, label, kind, unit, placeholder in SEVERE_CARE_FIELDS
    ]


def clean_severe_care(post):
    """画面から届いた重身の記録（severe_<key>）を、入力があった項目だけの dict にする"""
    out = {}
    for key in SEVERE_CARE_KEYS:
        value = (post.get(f'severe_{key}') or '').strip()
        if value:
            out[key] = value[:500]
    return out


def severe_care_rows(data):
    """保存済みの dict を表示用の行（項目名・値・単位）にする"""
    data = data or {}
    return [
        {'key': k, 'label': SEVERE_CARE_LABELS[k], 'value': data[k], 'unit': SEVERE_CARE_UNITS.get(k, '')}
        for k in SEVERE_CARE_KEYS if data.get(k)
    ]

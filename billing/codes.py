"""
放課後等デイサービスの基本報酬と、定員区分で単位数が変わる体制加算（令和6年4月施行の報酬改定）。

出典：依頼者の Google ドライブ「加算」フォルダの放課後等デイサービスコード表（令和6年4月版。houdei_basic1.pdf・houdei_basic2.pdf）と
こども家庭庁「令和６年度障害福祉サービス等報酬改定（障害児支援関係）改定事項の概要」。
サービスコードは「種類コード 63 ＋ 項目コード（4 桁）」の 6 桁。

- 時間区分：区分1＝30分以上1時間30分以下、区分2＝1時間30分超3時間以下、区分3＝3時間超5時間以下（学校休業日だけ）
- 定員区分：10人以下／11〜20人／21人以上
- 医療的ケア区分：利用者ごと（医療的ケア判定スコア 32 点以上・16 点以上・3 点以上・それ以外）
- 主として重症心身障害児を通わせる事業所の基本報酬はここに無い（施設設定の「1日あたり基本報酬単位数」に手で入れる）
- 減算（自己評価結果等未公表・開所時間・個別支援計画未作成・支援プログラム未公表）はコード表の「・評減」「・開減」「・未計画」「・支減」の行。
  単位数は下の REDUCTIONS と reduced_units で基本報酬に割合を掛けて出す（減算つきのサービスコードは出さない）
- 令和8年6月以降の新規指定事業所は 982/1000（Facility.is_new_facility_r8）
"""
import datetime
from decimal import ROUND_HALF_UP, Decimal

from config.jp_holidays import is_weekend_or_holiday

KUBUN_CHOICES = [(1, '区分1（30分以上1時間30分以下）'), (2, '区分2（1時間30分超3時間以下）'), (3, '区分3（3時間超5時間以下・学校休業日のみ）')]
KUBUN_LABELS = dict(KUBUN_CHOICES)
KUBUN_SHORT = {1: '区分1', 2: '区分2', 3: '区分3'}

CAPACITY_CHOICES = [('', '（単位数を手で入れる）'), ('10', '定員10人以下'), ('20', '定員11〜20人'), ('21', '定員21人以上')]
CAPACITY_LABELS = dict(CAPACITY_CHOICES)

# 医療的ケア区分（利用者ごと。医療的ケア判定スコア）
CARE_LEVEL_CHOICES = [(0, 'なし'), (3, '医療的ケア児（スコア 3 点以上）'), (16, '医療的ケア児（スコア 16 点以上）'), (32, '医療的ケア児（スコア 32 点以上）')]
CARE_LEVEL_LABELS = dict(CARE_LEVEL_CHOICES)

# {医療的ケア区分: {定員区分: {時間区分: (サービスコード, 単位数)}}}
BASE_TABLE = {
    0: {   # 放デイ４ｘｘ（医療的ケア区分なし）
        '10': {1: ('63H574', 574), 2: ('63H592', 609), 3: ('63H610', 666)},
        '20': {1: ('63H682', 382), 2: ('63H700', 406), 3: ('63H718', 443)},
        '21': {1: ('63H790', 287), 2: ('63H808', 305), 3: ('63H826', 343)},
    },
    3: {   # 放デイ３ｘｘ（スコア 3 点以上）
        '10': {1: ('63G949', 1247), 2: ('63G967', 1282), 3: ('63G985', 1339)},
        '20': {1: ('63H358', 1055), 2: ('63H376', 1078), 3: ('63H394', 1116)},
        '21': {1: ('63H466', 960), 2: ('63H484', 977), 3: ('63H502', 1016)},
    },
    16: {  # 放デイ２ｘｘ（スコア 16 点以上）
        '10': {1: ('63G625', 1583), 2: ('63G643', 1618), 3: ('63G661', 1674)},
        '20': {1: ('63G733', 1391), 2: ('63G751', 1414), 3: ('63G769', 1452)},
        '21': {1: ('63G841', 1296), 2: ('63G859', 1313), 3: ('63G877', 1352)},
    },
    32: {  # 放デイ１ｘｘ（スコア 32 点以上）
        '10': {1: ('63G301', 2591), 2: ('63G319', 2627), 3: ('63G337', 2683)},
        '20': {1: ('63G409', 2399), 2: ('63G427', 2423), 3: ('63G445', 2461)},
        '21': {1: ('63G517', 2304), 2: ('63G535', 2322), 3: ('63G553', 2361)},
    },
}
# 互換（単位数だけの表）
BASE_UNITS = {cap: {k: v[1] for k, v in rows.items()} for cap, rows in BASE_TABLE[0].items()}

# 定員区分で単位数が変わる体制加算：{加算名: {定員区分: (サービスコード, 単位数)}}（1 日につき）
FACILITY_ADDON_BY_CAPACITY = {
    '児童指導員等加配加算（常勤専従・経験5年以上）': {'10': ('634400', 187), '20': ('634401', 125), '21': ('634402', 75)},
    '児童指導員等加配加算（常勤専従・経験5年未満）': {'10': ('634417', 152), '20': ('634418', 101), '21': ('634419', 59)},
    '児童指導員等加配加算（常勤換算・経験5年以上）': {'10': ('634434', 123), '20': ('634435', 82), '21': ('634436', 49)},
    '児童指導員等加配加算（常勤換算・経験5年未満）': {'10': ('634451', 107), '20': ('634452', 71), '21': ('634453', 43)},
    '児童指導員等加配加算（その他の従業者）':       {'10': ('634468', 90), '20': ('634469', 60), '21': ('634470', 36)},
    '専門的支援体制加算':                           {'10': ('634551', 123), '20': ('634552', 82), '21': ('634553', 49)},
    '中核機能強化事業所加算':                       {'10': ('635801', 187), '20': ('635802', 125), '21': ('635803', 75)},
}


# 主として重症心身障害児を通わせる事業所（重心型）の基本報酬：時間区分は無く「授業終了後」「学校休業日」の 2 つ。
# 令和 6 年 4 月から定員区分は 3 人刻み（5〜7 人・8〜10 人・11 人以上）。看護職員加配加算は 1 人刻み（5〜11 人以上）のまま。
# 単位数は令和 6 年度改定の解説（複数の民間サイトで一致）による。5〜7 人は確かめずみ、8〜10 人・11 人以上は告示の原本で要確認。
# 施設設定で単位数を入れればそちらが優先（事業所ごとの上書き）
SEVERE_SCHOOL, SEVERE_HOLIDAY = 2, 3
SEVERE_LABELS = {SEVERE_SCHOOL: '授業終了後', SEVERE_HOLIDAY: '学校休業日'}
SEVERE_CAPACITY_CHOICES = [('', '（単位数を手で入れる）'), ('5', '定員 5 人'), ('6', '定員 6 人'), ('7', '定員 7 人'), ('8', '定員 8 人'),
                           ('9', '定員 9 人'), ('10', '定員 10 人'), ('11', '定員 11 人以上')]
SEVERE_BASE = {        # 定員の帯 → {区分: 単位数}
    '5-7':  {SEVERE_SCHOOL: 1771, SEVERE_HOLIDAY: 2056},
    '8-10': {SEVERE_SCHOOL: 1118, SEVERE_HOLIDAY: 1299},
    '11':   {SEVERE_SCHOOL: 692,  SEVERE_HOLIDAY: 817},
}
SEVERE_REFERENCE = [('定員 5〜7 人', 1771, 2056), ('定員 8〜10 人', 1118, 1299), ('定員 11 人以上', 692, 817)]
# 重心型だけの体制加算：定員 1 人刻みで単位数が変わる（1 日につき）。Ⅰ＝看護職員を常勤換算 1.0 以上・医療的ケアスコア合計 40 点以上、
# Ⅱ＝2.0 以上・72 点以上。令和 3 年度にⅢは廃止。サービスコードは事業所のコード表で確かめて加算設定に入れる
SEVERE_ADDON_BY_CAPACITY = {
    '看護職員加配加算Ⅰ': {'5': 400, '6': 333, '7': 286, '8': 250, '9': 222, '10': 200, '11': 133},
    '看護職員加配加算Ⅱ': {'5': 800, '6': 666, '7': 572, '8': 500, '9': 444, '10': 400, '11': 266},
}
SEVERE_ONLY_ADDONS = tuple(SEVERE_ADDON_BY_CAPACITY)      # 重心型の事業所でだけ意味のある体制加算


def severe_band(severe_capacity):
    """重心型の定員（1 人刻み）→ 基本報酬の帯"""
    try:
        n = int(severe_capacity)
    except (TypeError, ValueError):
        return ''
    return '5-7' if n <= 7 else '8-10' if n <= 10 else '11'


def severe_base_units(severe_capacity, kind):
    """重心型の基本報酬の単位数（表）。定員が無ければ None"""
    band = severe_band(severe_capacity)
    return SEVERE_BASE[band][kind] if band else None


def severe_addon_units(name, severe_capacity):
    """重心型の定員で変わる体制加算の単位数。該当しなければ None"""
    for prefix, table in SEVERE_ADDON_BY_CAPACITY.items():
        if (name or '').startswith(prefix) and str(severe_capacity) in table:
            return table[str(severe_capacity)]
    return None


# ---- 利用者の区分（重心・医療的ケア）で、請求セルに出す個別加算を絞る ----
def addon_applies(name, beneficiary):
    """
    この利用者にその個別加算が当てはまるか（当てはまらない加算は請求セルの一覧に出さない。付いているものは出す）。
    - 重症児・医療的ケア児の延長支援は、重心か医療的ケア区分のある利用者だけ。一般の延長支援はそれ以外
    - 送迎の上乗せ：重症心身障害児は重心の利用者、医療的ケア児は医療的ケア区分（16 点以上／その他）で分ける
    - 入浴支援加算は重心か医療的ケア児。個別サポート加算Ⅰ（著しく重度）は重心の利用者
    """
    name = name or ''
    severe = bool(getattr(beneficiary, 'is_severe', False))
    care = int(getattr(beneficiary, 'medical_care_level', 0) or 0)
    if name.startswith('延長支援加算'):
        return (severe or care > 0) if '重症児・医療的ケア児' in name else not (severe or care > 0)
    if '上乗せ' in name:
        if '重症心身障害児' in name:
            return severe
        if 'スコア16点以上' in name:
            return care >= 16
        if '医療的ケア児' in name:
            return 0 < care < 16
    if name.startswith('入浴支援加算'):
        return severe or care > 0
    if name.startswith('個別サポート加算Ⅰ（著しく重度）'):
        return severe
    return True


def is_leg_addon(name):
    """送迎の片道（往・復・同一敷地内）か"""
    return (name or '').startswith('送迎加算（') and '上乗せ' not in name


def is_per_leg_addon(name):
    """送迎の片道ごとに付く上乗せか（その日の片道の数だけ算定する）"""
    return (name or '').startswith('送迎加算（') and '上乗せ' in name


def companion_addons(beneficiary, selected_rows, all_rows):
    """
    送迎を付けたときに一緒に付ける上乗せ（重心・医療的ケア児）。戻り値は足す加算 ID の集合。
    selected_rows・all_rows は AddonRow（name・pk）
    """
    if not any(is_leg_addon(r.name) for r in selected_rows):
        return set()
    return {r.pk for r in all_rows if is_per_leg_addon(r.name) and addon_applies(r.name, beneficiary)}


def severe_kind(kubun):
    """時間区分 → 重心型の区分（区分 3 ＝ 学校休業日、それ以外は授業終了後）"""
    return SEVERE_HOLIDAY if int(kubun or 0) == 3 else SEVERE_SCHOOL


def base_row(care_level, capacity_class, kubun):
    """(サービスコード, 単位数)。表に無ければ None"""
    try:
        return BASE_TABLE[int(care_level or 0)][capacity_class][int(kubun)]
    except (KeyError, TypeError, ValueError):
        return None


def table_units(capacity_class, kubun, care_level=0):
    row = base_row(care_level, capacity_class, kubun)
    return row[1] if row else None


def table_code(capacity_class, kubun, care_level=0):
    row = base_row(care_level, capacity_class, kubun)
    return row[0] if row else ''


# 福祉・介護職員等処遇改善加算：単位数ではなく、ほかの単位数の合計に加算率を掛ける（1 単位未満は四捨五入）。
# 放課後等デイサービスの率（%）。{適用開始日: {区分: 率}}。令和8年6月から率が上がり、Ⅰ・Ⅱは「イ」「ロ」に分かれた。
# ここでは Ⅰ・Ⅱ は「イ」の率。「ロ」（イ＋0.6）などで違うときは、施設設定の加算設定で事業所の率を入れる
TREATMENT_PREFIX = '福祉・介護職員等処遇改善加算'
TREATMENT_RATES = [
    (datetime.date(2024, 6, 1), {'Ⅰ': '13.4', 'Ⅱ': '13.1', 'Ⅲ': '12.1', 'Ⅳ': '9.8'}),
    (datetime.date(2026, 6, 1), {'Ⅰ': '15.5', 'Ⅱ': '15.2', 'Ⅲ': '14.2', 'Ⅳ': '11.9'}),
]
# 利用者負担上限額管理加算：上限額管理をした月に 1 回（日ごとではない）
MONTHLY_ADDONS = ('利用者負担上限額管理加算',)


# 減算：基本報酬（所定単位数。加算は含まない）に掛ける割合（%）。重なるときは掛け合わせて、最後に 1 単位未満を四捨五入。
# 開所時間減算は学校休業日（時間区分 3／重心型の学校休業日）の日だけ。出どころは令和6年度報酬改定の解説（告示・通知の原本で要確認）
REDUCTIONS = {
    'self_eval': '自己評価結果等未公表減算',
    'program':   '支援プログラム未公表減算',
    'hours':     '開所時間減算',
    'no_plan':   '個別支援計画未作成減算',
}


def reduced_units(units, rates):
    """1 日の基本報酬の単位数に減算の割合（% の一覧）を掛ける。1 単位未満は四捨五入"""
    value = Decimal(units)
    for rate in rates:
        value = value * Decimal(rate) / Decimal(100)
    return int(value.quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def is_treatment(name):
    return (name or '').startswith(TREATMENT_PREFIX)


def treatment_rate(name, day):
    """処遇改善加算の標準の率（%・Decimal）。その日に当てはまる表が無ければ None"""
    grade = (name or '')[len(TREATMENT_PREFIX):][:1]
    rate = None
    for start, table in TREATMENT_RATES:
        if day >= start and grade in table:
            rate = Decimal(table[grade])
    return rate


def treatment_units(units, rate):
    """処遇改善加算の単位数：単位数の合計 × 率（%）、1 単位未満は四捨五入"""
    return int((Decimal(units) * Decimal(rate) / Decimal(100)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def capacity_addon(name, capacity_class, severe_capacity=''):
    """定員区分で変わる体制加算の (サービスコード, 単位数)。重心型の定員（1 人刻み）の表も見る。該当しなければ None"""
    row = FACILITY_ADDON_BY_CAPACITY.get(name, {}).get(capacity_class) if capacity_class else None
    if row:
        return row
    units = severe_addon_units(name, severe_capacity) if severe_capacity else None
    return ('', units) if units is not None else None


def facility_capacity_addon(facility, name):
    """その事業所の定員区分（一般・重心型）で決まる体制加算の (コード, 単位数)"""
    return capacity_addon(name, facility.capacity_class, facility.severe_capacity if facility.is_severe_facility else '')


def default_kubun(facility, day):
    """その日の時間区分の初期値：土日祝は施設設定の「学校休業日の区分」、平日は「平日の区分」"""
    if is_weekend_or_holiday(day):
        return facility.time_kubun_holiday or 3
    return facility.time_kubun_weekday or 2


def units_table(facility):
    """施設設定の画面用：定員区分ごとの単位数とコード（医療的ケア区分なし。いまの事業所の区分に印）"""
    return [{'key': key, 'label': label, 'current': facility.capacity_class == key,
             'cells': [BASE_TABLE[0][key][k] for k in (1, 2, 3)]}
            for key, label in CAPACITY_CHOICES if key]

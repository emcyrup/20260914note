"""
放課後等デイサービスの基本報酬（令和6年4月施行の報酬改定）。

- 時間区分：区分1＝30分以上1時間30分以下、区分2＝1時間30分超3時間以下、区分3＝3時間超5時間以下（学校休業日だけ）
- 定員区分：10人以下／11〜20人／21人以上
- 単位数は「医療的ケア区分なし（重症心身障害児以外）」の値。主として重症心身障害児を通わせる事業所と、
  医療的ケア児（判定スコアによる区分）の単位数はここに無いので、施設設定の「1日あたり基本報酬単位数」に手で入れる
- 国保連のサービスコード（6 桁）は、事業所が使っているコード表の値を施設設定で入れる（ここでは持たない）
- 令和8年6月以降の新規指定事業所は 982/1000（Facility.is_new_facility_r8）
"""
from config.jp_holidays import is_weekend_or_holiday

KUBUN_CHOICES = [(1, '区分1（30分以上1時間30分以下）'), (2, '区分2（1時間30分超3時間以下）'), (3, '区分3（3時間超5時間以下・学校休業日のみ）')]
KUBUN_LABELS = dict(KUBUN_CHOICES)
KUBUN_SHORT = {1: '区分1', 2: '区分2', 3: '区分3'}

CAPACITY_CHOICES = [('', '（単位数を手で入れる）'), ('10', '定員10人以下'), ('20', '定員11〜20人'), ('21', '定員21人以上')]

# {定員区分: {時間区分: 単位数}}（医療的ケア区分なし）
BASE_UNITS = {
    '10': {1: 574, 2: 609, 3: 666},
    '20': {1: 382, 2: 406, 3: 443},
    '21': {1: 287, 2: 305, 3: 343},
}


def table_units(capacity_class, kubun):
    """定員区分と時間区分から基本報酬の単位数（表に無ければ None）"""
    try:
        return BASE_UNITS[capacity_class][int(kubun)]
    except (KeyError, TypeError, ValueError):
        return None


def default_kubun(facility, day):
    """その日の時間区分の初期値：土日祝は施設設定の「学校休業日の区分」、平日は「平日の区分」"""
    if is_weekend_or_holiday(day):
        return facility.time_kubun_holiday or 3
    return facility.time_kubun_weekday or 2


def units_table(facility):
    """施設設定の画面用：定員区分ごとの単位数（いまの事業所の区分に印）"""
    return [{'key': key, 'label': label, 'units': [BASE_UNITS[key][k] for k in (1, 2, 3)],
             'current': facility.capacity_class == key}
            for key, label in CAPACITY_CHOICES if key]

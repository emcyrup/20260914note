"""
日本の祝日（土・日・祝の枠を決めるための簡易判定）。

外部ライブラリなしで、国民の祝日に関する法律の規則から計算する。
- 固定日：元日・建国記念の日・昭和の日・憲法記念日・みどりの日・こどもの日・山の日・文化の日・勤労感謝の日・天皇誕生日
- ハッピーマンデー：成人の日（1月第2月曜）・海の日（7月第3月曜）・敬老の日（9月第3月曜）・スポーツの日（10月第2月曜）
- 春分の日・秋分の日：天文計算の近似式（2000〜2099年で一致）
- 振替休日（祝日が日曜なら次の平日）・国民の休日（祝日に挟まれた平日）
2020・2021年のオリンピック特例のような年ごとの特例は入れていない（当時の年を扱うことは想定しない）。
"""
import datetime
from functools import lru_cache


def _nth_monday(year, month, n):
    first = datetime.date(year, month, 1)
    offset = (7 - first.weekday()) % 7          # 最初の月曜まで
    return first + datetime.timedelta(days=offset + 7 * (n - 1))


def _equinox(year, march=True):
    """春分・秋分の日（2000〜2099年の近似式）"""
    if march:
        day = int(20.8431 + 0.242194 * (year - 1980) - int((year - 1980) / 4))
        return datetime.date(year, 3, day)
    day = int(23.2488 + 0.242194 * (year - 1980) - int((year - 1980) / 4))
    return datetime.date(year, 9, day)


@lru_cache(maxsize=32)
def holidays(year):
    """その年の祝日（振替休日・国民の休日を含む）を {日付: 名前} で返す"""
    base = {
        datetime.date(year, 1, 1): '元日',
        _nth_monday(year, 1, 2): '成人の日',
        datetime.date(year, 2, 11): '建国記念の日',
        datetime.date(year, 2, 23): '天皇誕生日',
        _equinox(year, True): '春分の日',
        datetime.date(year, 4, 29): '昭和の日',
        datetime.date(year, 5, 3): '憲法記念日',
        datetime.date(year, 5, 4): 'みどりの日',
        datetime.date(year, 5, 5): 'こどもの日',
        _nth_monday(year, 7, 3): '海の日',
        datetime.date(year, 8, 11): '山の日',
        _nth_monday(year, 9, 3): '敬老の日',
        _equinox(year, False): '秋分の日',
        _nth_monday(year, 10, 2): 'スポーツの日',
        datetime.date(year, 11, 3): '文化の日',
        datetime.date(year, 11, 23): '勤労感謝の日',
    }
    result = dict(base)
    # 振替休日：祝日が日曜なら、その後の最初の「祝日でない日」
    for day in sorted(base):
        if day.weekday() == 6:
            nxt = day + datetime.timedelta(days=1)
            while nxt in result:
                nxt += datetime.timedelta(days=1)
            result[nxt] = '振替休日'
    # 国民の休日：前日と翌日が祝日の平日（敬老の日と秋分の日の間など）
    for day in sorted(base):
        mid = day + datetime.timedelta(days=1)
        after = day + datetime.timedelta(days=2)
        if after in base and mid not in result and mid.weekday() != 6:
            result[mid] = '国民の休日'
    return result


def is_holiday(day):
    return day in holidays(day.year)


def holiday_name(day):
    return holidays(day.year).get(day, '')


def is_weekend_or_holiday(day):
    """土・日・祝か（この日は「土日祝」の枠の時間帯を使う）"""
    return day.weekday() >= 5 or is_holiday(day)

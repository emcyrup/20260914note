"""
人員配置基準の判定（通所介護の基準をもとにした目安。2026-10-20 の打ち合わせ：利用者数と職員数で「安全／要調整」を出す）。
細かい決まり（常勤換算・配置時間・事業所の指定の種類）は 11 月のパイロット導入で事業所と決めるので、ここは目安の表にしてある。

通所介護（定員 11 人以上）の人員基準（厚労省令・平成 11 年厚生省令第 37 号 第 93 条の要点）
- 生活相談員：1 人以上（提供日ごとに提供時間帯を通じて）
- 看護職員：1 人以上（提供日ごと。単位ごと）
- 介護職員：利用者 15 人まで 1 人以上、15 人を超えると 5 人ごとに 1 人を加える（提供時間数の合計を提供時間で割った数）
- 機能訓練指導員：1 人以上
- 介護職員と看護職員は、利用者 15 人までは合わせて 1 人以上でよい（どちらかが要る）
地域密着型（定員 18 人以下）：看護職員は置けないことがあるが、介護職員の決まりは同じ。
"""
import math

USERS_BASE = 15      # ここまでは介護職員 1 人
USERS_STEP = 5       # 超えた分はこの人数ごとに 1 人


def required_care_staff(users):
    """利用者数に対して要る介護職員（看護職員を含めてよい）の人数"""
    if users <= 0:
        return 0
    if users <= USERS_BASE:
        return 1
    return 1 + math.ceil((users - USERS_BASE) / USERS_STEP)


def judge(day):
    """
    その日の人員配置の判定。戻り値 dict：ok（True＝安全）、required（介護職員の必要数）、problems（足りないものの一覧）、
    level（'ok' / 'warn' / 'ng'）
    """
    users = day.users or 0
    required = required_care_staff(users)
    problems = []
    level = 'ok'
    if users == 0:
        return {'ok': True, 'required': 0, 'problems': [], 'level': 'ok', 'label': '利用者なし'}
    # 介護職員＋看護職員で数える（15 人までは合わせて 1 人でよいため）
    hands = (day.care_staff or 0) + (day.nurses or 0)
    if hands < required:
        problems.append(f'介護・看護職員が {required} 人必要（いま {hands} 人）')
        level = 'ng'
    if users > USERS_BASE and (day.nurses or 0) < 1:
        problems.append('看護職員がいない（利用者 16 人以上の日は 1 人必要）')
        level = 'ng'
    if (day.counselors or 0) < 1:
        problems.append('生活相談員がいない')
        level = 'warn' if level == 'ok' else level
    if (day.trainers or 0) < 1:
        problems.append('機能訓練指導員がいない（看護職員などが兼ねていれば可）')
        level = 'warn' if level == 'ok' else level
    label = {'ok': '安全', 'warn': '確認', 'ng': '要調整'}[level]
    return {'ok': level == 'ok', 'required': required, 'problems': problems, 'level': level, 'label': label}

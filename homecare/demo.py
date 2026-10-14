"""
居宅介護の架空のサンプルデータ（create_facility --preset kyotaku --demo）。名前・内容はすべて架空。
今月の訪問（毎週の決まった曜日）と、今日より前の訪問の記録、居宅介護計画を入れる。
"""
import datetime

from beneficiaries.models import Beneficiary

from .models import CarePlan, HomeCareProfile, Visit

PEOPLE = [
    # 姓, 名, かな姓, かな名, 生年月日, 区分, 支給量, 留意点, 曜日（0=月）, 開始, 終了, 種類, 計画の内容
    ('山本', '和子', 'やまもと', 'かずこ', datetime.date(1958, 5, 12), '3', {'body': 20, 'house': 10},
     '右半身にまひがある。立ち上がりは左側から支える。入浴は湯温 40 度まで。', [0, 3], '10:00', '11:00', 'body',
     '入浴介助・更衣・整容'),
    ('佐々木', '誠', 'ささき', 'まこと', datetime.date(1975, 11, 3), '2', {'house': 16},
     '糖尿病の食事制限あり（調理は塩分・糖分を控える）。買い物は本人と一緒にメモを確認する。', [1, 4], '14:00', '15:30', 'house',
     '調理・掃除・買い物'),
    ('中島', '美保', 'なかじま', 'みほ', datetime.date(1990, 2, 20), '4', {'body': 30, 'escort_body': 6},
     '車いすを使用。移乗は二人で行う手順を家族と確認ずみ。発作時は横向きにして家族へ連絡。', [2], '09:30', '11:00', 'body',
     '移乗・排せつ介助・清拭'),
]
DONE_TASKS = {'body': ['bath', 'dress', 'groom', 'check'], 'house': ['cook', 'clean', 'shopping', 'check']}
BODIES = {
    'body': '入浴の前に体温と血圧を測った。浴槽への出入りは手すりを使って一緒に行った。「さっぱりした」と話していた。',
    'house': '昼食と夕食の分を調理した。冷蔵庫の中を一緒に確認し、買い物のメモを作った。',
}


def seed(facility):
    today = datetime.date.today()
    first = today.replace(day=1)
    counts = {'people': 0, 'visits': 0, 'plans': 0}
    for (last, first_name, kana_l, kana_f, dob, level, grants, cautions, weekdays, start, end, kind, content) in PEOPLE:
        b = Beneficiary.objects.create(facility=facility, last_name=last, first_name=first_name, last_name_kana=kana_l,
                                       first_name_kana=kana_f, date_of_birth=dob, status=Beneficiary.STATUS_ACTIVE)
        counts['people'] += 1
        HomeCareProfile.objects.create(beneficiary=b, support_level=level, grants=grants, cautions=cautions)
        s, e = datetime.time.fromisoformat(start), datetime.time.fromisoformat(end)
        d = first
        while d.month == first.month:
            if d.weekday() in weekdays:
                v = Visit.objects.create(facility=facility, beneficiary=b, date=d, start_time=s, end_time=e, service_type=kind)
                if d < today:
                    v.status, v.actual_start, v.actual_end = Visit.STATUS_DONE, s, e
                    v.tasks = DONE_TASKS.get(kind, ['check'])
                    v.condition, v.temperature = 'usual', '36.5'
                    v.body = BODIES.get(kind, '')
                    v.save()
                counts['visits'] += 1
            d += datetime.timedelta(days=1)
        CarePlan.objects.create(
            facility=facility, beneficiary=b, created_on=first, period_start=first,
            period_end=first.replace(year=first.year + (1 if first.month > 6 else 0),
                                     month=(first.month + 5) % 12 + 1) - datetime.timedelta(days=1),
            wishes='自宅での生活を続けたい（本人）。', situation=cautions, issues='一人では難しい家事・身の回りのことがある。',
            long_goal='自宅で安心して暮らし続けられる。', short_goal='決まった曜日の支援で、生活のリズムを保てる。',
            services=[{'days': '・'.join('月火水木金土日'[w] for w in weekdays), 'start': start, 'end': end, 'type': kind,
                       'content': content}],
            notes=cautions)
        counts['plans'] += 1
    return counts

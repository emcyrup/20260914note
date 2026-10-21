"""
居宅介護支援（ケアマネ）の架空のサンプルデータ。名前・住所・内容はすべて架空。
- python manage.py kaigo_demo --facility 1          … 入れる（もう入っていれば何もしない）
- python manage.py kaigo_demo --facility 1 --remove … 消す（サンプルの印のある利用者・取引先だけ。ほかのデータは触らない）
create_facility --preset kyotaku --demo からも呼ぶ。
サンプルの利用者は備考の先頭に MARK、取引先はメモに MARK を入れて見分ける。
"""
import calendar
import datetime

from django.db import transaction

from beneficiaries.models import Beneficiary, Guardian

from .models import (ASSESSMENT_KEYS, Assessment, CareInsurance, KaigoPlan, Meeting, Provider, ServiceUse, SupportLog)

MARK = '【サンプル】'
NOTE = MARK + '架空の利用者です（操作を試すためのもの）。不要になったら一覧で印を付けて削除してください。'

PROVIDERS = [
    ('架空ヘルパーステーション', '訪問介護', '000-0000-0001'),
    ('架空デイサービスセンター', '通所介護', '000-0000-0002'),
    ('架空福祉用具', '福祉用具貸与', '000-0000-0003'),
    ('架空クリニック', '医療機関', '000-0000-0004'),
    ('架空地域包括支援センター', '地域包括支援センター', '000-0000-0005'),
]

# 姓, 名, かな姓, かな名, 性別, 生年月日, 要介護度, 負担割合, 家族（姓, 名, 続柄の言い方）, 主な課題, サービス
PEOPLE = [
    ('見本', '花子', 'みほん', 'はなこ', 'female', datetime.date(1938, 4, 2), '要介護2', 1, ('見本', '一郎', '長男'),
     '膝の痛みがあり、一人での入浴が不安', [('架空ヘルパーステーション', '訪問介護 身体1', '10:00', '10:30', [0, 3]),
                                       ('架空デイサービスセンター', '通所介護', '09:30', '16:00', [2])]),
    ('試験', '太郎', 'しけん', 'たろう', 'male', datetime.date(1941, 9, 15), '要介護4', 1, ('試験', '良子', '妻'),
     '車いすの生活で、移乗に介助が要る', [('架空ヘルパーステーション', '訪問介護 身体2', '09:00', '10:00', [0, 2, 4]),
                                     ('架空福祉用具', '福祉用具貸与（車いす・特殊寝台）', '', '', [])]),
    ('架空', '和子', 'かくう', 'かずこ', 'female', datetime.date(1945, 1, 20), '要支援2', 1, ('架空', '真一', '長男'),
     '一人暮らしで、買い物と掃除が負担', [('架空デイサービスセンター', '通所型サービス', '09:30', '15:30', [1])]),
    ('仮名', '次郎', 'かめい', 'じろう', 'male', datetime.date(1936, 11, 8), '要介護3', 3, ('仮名', '恵子', '長女'),
     '物忘れが増え、服薬の管理が難しい', [('架空デイサービスセンター', '通所介護', '09:30', '16:00', [1, 4]),
                                     ('架空ヘルパーステーション', '訪問介護 生活2', '15:00', '15:45', [2])]),
]

LOGS = [
    # (何日前, 種別, 方法, 相手, 内容)
    (2, 'monitoring', 'visit', '', '自宅にて本人と面談してモニタリング実施。{issue}。サービスは予定どおり利用できている。本人は「今のままで続けたい」とのこと。体調に大きな変わりはない。'),
    (9, 'provider', 'phone', '架空デイサービスセンター', '架空デイサービスセンターより連絡。先週の利用時に少し食欲が落ちていたとのこと。家族にも伝えて様子を見てもらう。'),
    (16, 'family', 'phone', '', '{family}さんより電話。来月の通院日が決まったとのこと。通院日の予定を調整すると伝えた。'),
    (30, 'monitoring', 'visit', '', '自宅にて本人と面談してモニタリング実施。{issue}が続いているが、サービスの利用で生活は保てている。家族の協力もある。'),
    (38, 'medical', 'fax', '架空クリニック', '架空クリニックの主治医へ、最近の様子をまとめて FAX で送った。次の受診時に確認してもらう。'),
    (45, 'meeting', 'visit', '', 'サービス担当者会議（自宅）を開催。本人・家族・各事業所が参加。今の計画を続けることを確認した。'),
]


def is_demo(b):
    return (b.notes or '').startswith(MARK)


def _month_days(d, weekdays):
    n = calendar.monthrange(d.year, d.month)[1]
    return [day for day in range(1, n + 1) if datetime.date(d.year, d.month, day).weekday() in weekdays]


@transaction.atomic
def seed(facility, user=None):
    """サンプルを入れる。もう入っていれば何もしない。入れた数を返す"""
    if any(is_demo(b) for b in Beneficiary.objects.filter(facility=facility, notes__startswith=MARK)):
        return {'people': 0, 'skipped': True}
    today = datetime.date.today()
    providers = {}
    for name, kind, tel in PROVIDERS:
        pv = Provider.objects.filter(facility=facility, name=name).first() or \
            Provider.objects.create(facility=facility, name=name, service_kind=kind, phone=tel, notes=MARK + '架空の取引先です。')
        providers[name] = pv
    counts = {'people': 0, 'logs': 0, 'plans': 0, 'meetings': 0, 'assessments': 0, 'uses': 0, 'providers': len(providers)}
    for (last, first, kl, kf, gender, dob, level, ratio, fam, issue, services) in PEOPLE:
        b = Beneficiary.objects.create(facility=facility, last_name=last, first_name=first, last_name_kana=kl, first_name_kana=kf,
                                       gender=gender, date_of_birth=dob, status=Beneficiary.STATUS_ACTIVE,
                                       postal_code='0000000', address='架空県架空市見本町1-2-3', notes=NOTE)
        Guardian.objects.create(beneficiary=b, last_name=fam[0], first_name=fam[1], relation=Guardian.RELATION_OTHER,
                                phone='000-0000-0000', is_primary=True)
        start = today.replace(day=1) - datetime.timedelta(days=150)
        CareInsurance.objects.create(beneficiary=b, insurer_number='000000', insured_number=f'00000000{counts["people"] + 1:02d}',
                                     care_level=level, certified_on=start, issued_on=start, applied_from=start,
                                     valid_from=start.replace(day=1), valid_until=(start.replace(day=1) + datetime.timedelta(days=365)),
                                     copay_ratio=ratio, copay_issued_on=start, copay_from=start)
        counts['people'] += 1
        family = f'{fam[2]}の{fam[0]}{fam[1]}'
        for days, cat, method, pv_name, text in LOGS:
            when = today - datetime.timedelta(days=days)
            SupportLog.objects.create(facility=facility, beneficiary=b, date=when, time=datetime.time(10, 0), category=cat,
                                      method=method, provider=providers.get(pv_name), created_by=user,
                                      content=text.format(issue=issue, family=family))
            counts['logs'] += 1
        plan_day = today - datetime.timedelta(days=45)
        weekly = [{'days': w, 'start': s, 'end': e, 'content': svc} for _, svc, s, e, w in services if w]
        KaigoPlan.objects.create(
            facility=facility, beneficiary=b, created_on=plan_day, first_created_on=plan_day, plan_type='continue',
            cert_state='certified', care_level=level, certified_on=start, cert_from=start.replace(day=1),
            cert_until=start.replace(day=1) + datetime.timedelta(days=365), planner='（サンプル）',
            wishes=f'本人：住み慣れた家で暮らし続けたい。\n家族：無理のない範囲で手伝いたい。\n課題：{issue}。',
            policy='できることは続けてもらいながら、難しいところをサービスで支える。体調の変化は事業所と早めに共有する。',
            goals=[{'need': issue, 'long_goal': '自宅で安心して暮らし続けられる', 'long_period': '1年',
                    'short_goal': '決まった曜日のサービスで、生活のリズムを保つ', 'short_period': '6か月',
                    'service': '・'.join(svc for _, svc, *_ in services), 'insured': '○',
                    'kind': services[0][1].split()[0], 'provider': services[0][0], 'frequency': '週' + str(max(1, len(services[0][4]))) + '回',
                    'period': '6か月'}],
            weekly=weekly, daily_life='7:00 起床・朝食　12:00 昼食　18:00 夕食　21:00 就寝', explained_on=plan_day, agreed_by='本人')
        counts['plans'] += 1
        Meeting.objects.create(
            facility=facility, beneficiary=b, held_on=plan_day, start=datetime.time(13, 0), end=datetime.time(13, 30), place='自宅',
            count=1, planner='（サンプル）',
            attendees=[{'org': '本人', 'name': f'{last} {first}'}, {'org': f'家族（{fam[2]}）', 'name': f'{fam[0]} {fam[1]}'},
                       {'org': f'{services[0][0]}（担当）', 'name': '架空 担当'}],
            topics=f'1. {issue}\n2. 今後のサービスの利用', discussion='本人・家族の意向を確認し、各事業所から最近の様子を聞いた。',
            conclusion='今の計画を続ける。体調の変化があれば早めに連絡し合う。', remaining='次回は認定の更新の前に開く。')
        counts['meetings'] += 1
        items = {k: '' for k in ASSESSMENT_KEYS}
        items.update({'basic': f'{level}。家族（{fam[2]}）の協力あり。', 'wishes': '住み慣れた家で暮らし続けたい。',
                      'health': '持病は落ち着いている。定期的に通院。', 'adl': issue + '。',
                      'family': f'{fam[2]}が週に数回様子を見に来る。', 'housing': '持ち家。段差あり。'})
        Assessment.objects.create(facility=facility, beneficiary=b, assessed_on=plan_day - datetime.timedelta(days=7), kind='regular',
                                  place='自宅', assessor='（サンプル）', items={k: v for k, v in items.items() if v},
                                  summary=issue + '。')
        counts['assessments'] += 1
        for i, (pv_name, svc, s, e, w) in enumerate(services):
            for month_start in (today.replace(day=1) - datetime.timedelta(days=1), today):
                d = month_start.replace(day=1)
                plan = _month_days(d, w)
                done = [x for x in plan if datetime.date(d.year, d.month, x) < today]
                ServiceUse.objects.create(facility=facility, beneficiary=b, year=d.year, month=d.month, order=i,
                                          provider=providers[pv_name], service=svc, start=s, end=e, plan_days=plan, done_days=done)
                counts['uses'] += 1
    return counts


@transaction.atomic
def remove(facility):
    """サンプルの利用者（関連する記録ごと）と、サンプルの取引先だけを消す。消した数を返す"""
    people = [b for b in Beneficiary.objects.filter(facility=facility, notes__startswith=MARK) if is_demo(b)]
    n = len(people)
    for b in people:
        b.delete()
    m, _ = Provider.objects.filter(facility=facility, notes__startswith=MARK).delete()
    return {'people': n, 'providers': m}

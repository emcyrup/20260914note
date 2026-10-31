"""
介護施設（やすらぎの森）の架空のサンプルデータ。名前・住所・内容はすべて架空。
- python manage.py nursing_demo --facility 1          … 入れる（もう入っていれば何もしない）
- python manage.py nursing_demo --facility 1 --remove … 消す（サンプルの印のある利用者と、その記録だけ）
create_facility --preset care --demo からも呼ぶ。サンプルの利用者は備考の先頭に MARK を入れて見分ける。
"""
import datetime
import random
from decimal import Decimal

from django.db import transaction

from beneficiaries.models import Beneficiary, Guardian
from kaigo.models import CareInsurance

from .models import CarePlan, CareRecord, IncidentReport, Medication, StaffingDay, VitalsLog

MARK = '【サンプル】'
NOTE = MARK + '架空の利用者です（操作を試すためのもの）。不要になったら一覧で印を付けて削除してください。'

# 姓, 名, かな姓, かな名, 性別, 生年月日, 要介護度, 利用の曜日, 家族（姓, 名, 続柄）, 様子, 薬
PEOPLE = [
    ('見本', '花子', 'みほん', 'はなこ', 'female', datetime.date(1938, 4, 2), '要介護2', (0, 2, 4), ('見本', '一郎', '長男'),
     '膝の痛みがあり、歩くときは手すりを使う。おしゃべりが好き', [('アムロジピン', '1 錠　朝食後', ['morning'], '血圧', '架空クリニック 山田医師')]),
    ('試験', '太郎', 'しけん', 'たろう', 'male', datetime.date(1941, 9, 15), '要介護4', (1, 3, 5), ('試験', '良子', '妻'),
     '車いす。移乗に介助が要る。食事はセッティングで自力', [('マグミット', '1 錠　朝夕食後', ['morning', 'evening'], '便秘', '架空クリニック 山田医師'),
                                                          ('ロキソニンテープ', '1 枚　腰', ['other'], '腰痛', '架空整形外科')]),
    ('架空', '和子', 'かくう', 'かずこ', 'female', datetime.date(1945, 1, 20), '要支援2', (0, 3), ('架空', '真一', '長男'),
     '一人暮らし。物忘れが少しある。体操が好き', []),
    ('仮名', '次郎', 'かめい', 'じろう', 'male', datetime.date(1936, 11, 8), '要介護3', (1, 2, 4), ('仮名', '恵子', '長女'),
     '認知症あり。声かけで落ち着く。歌が好き', [('ドネペジル', '1 錠　朝食後', ['morning'], '認知症', '架空メンタルクリニック')]),
]

BODIES = [
    '午前は体操に参加し、腕を上げる動きも最後まで続けた。昼食は主食・副食とも 8 割ほど。午後はぬり絵をして、隣の人と話しながら過ごした。排泄は自分でトイレに行き、問題なし。帰りの際に「また来る」と笑顔。',
    '来所時の体温は平熱。午前は足浴と機能訓練（立ち上がりの練習 10 回）。昼食は副食を少し残した。午後はカラオケに参加し、好きな歌を 2 曲歌った。排便あり。特に変わりなし。',
    '少し表情が固く、午前は休みがち。声をかけると「眠い」とのこと。昼食は 5 割。午後は窓際でゆっくり過ごし、水分をこまめにすすめた。帰りには表情がやわらぎ、家族には様子を伝えた。',
]
FAMILY = [
    '本日は体操とぬり絵に参加され、昼食もしっかり召し上がりました。お帰りの際には「また来る」と笑顔でした。',
    '本日は足浴と立ち上がりの練習をされました。カラオケでは好きな歌を 2 曲歌われ、楽しそうなご様子でした。',
    '本日は少しお疲れのご様子で、午前は休みながら過ごされました。昼食は半分ほどでしたが、水分はこまめに取られています。ご自宅でもゆっくりお休みください。',
]


def is_demo(b):
    return (b.notes or '').startswith(MARK)


def add(facility, user=None, days=14, stdout=None):
    """入れる。もう入っていれば何もしない。戻り値は入れた利用者の数"""
    if Beneficiary.objects.filter(facility=facility, notes__startswith=MARK).exists():
        return 0
    rnd = random.Random(20261031)
    today = datetime.date.today()
    with transaction.atomic():
        people = []
        for (ln, fn, lk, fk, gender, dob, level, wdays, fam, note, meds) in PEOPLE:
            b = Beneficiary(facility=facility, last_name=ln, first_name=fn, last_name_kana=lk, first_name_kana=fk, gender=gender,
                            date_of_birth=dob, notes=NOTE + '\n' + note, status=Beneficiary.STATUS_ACTIVE,
                            admission_date=today - datetime.timedelta(days=400))
            for i, f in enumerate(('weekday_mon', 'weekday_tue', 'weekday_wed', 'weekday_thu', 'weekday_fri', 'weekday_sat')):
                setattr(b, f, i in wdays)
            b.save()
            Guardian.objects.create(beneficiary=b, last_name=fam[0], first_name=fam[1], relation='other', memo=fam[2], is_primary=True,
                                    phone='000-0000-0000')
            CareInsurance.objects.create(beneficiary=b, insurer_number='999999', insured_number=f'00000000{len(people) + 1:02d}', care_level=level,
                                         valid_from=today.replace(day=1) - datetime.timedelta(days=200), valid_until=today + datetime.timedelta(days=300),
                                         copay_ratio=1)
            for name, dose, timings, purpose, doc in meds:
                Medication.objects.create(facility=facility, beneficiary=b, name=name, dose=dose, timings=timings, purpose=purpose, prescriber=doc,
                                          start_on=today - datetime.timedelta(days=90), review_due=today + datetime.timedelta(days=rnd.choice([5, 20, 45])))
            CarePlan.objects.create(facility=facility, beneficiary=b, created_on=today - datetime.timedelta(days=60), period_from=today - datetime.timedelta(days=60),
                                    period_to=today + datetime.timedelta(days=120), care_level=level, planner='相談員（サンプル）',
                                    wishes_person='できることは自分で続けたい。みんなと話したい。', wishes_family='安全に通ってほしい。体調の変化を教えてほしい。',
                                    policy='本人のペースを大切にし、できる動作は見守りながら自分で行ってもらう。体調の変化は家族と共有する。',
                                    long_goal='歩行の力を保ち、通所を続ける。', short_goal='週 2 回の体操に参加し、立ち上がりの練習を続ける。',
                                    rows=[{'goal': '転ばずに移動する', 'service': '機能訓練', 'detail': '立ち上がりの練習・歩行の見守り', 'frequency': '来所ごと 15 分', 'staff': '機能訓練指導員'},
                                          {'goal': '楽しく過ごす', 'service': 'レクリエーション', 'detail': '体操・カラオケ・ぬり絵', 'frequency': '毎回', 'staff': '介護職員'}],
                                    review_due=today + datetime.timedelta(days=rnd.choice([10, 60, 120])), created_by=user)
            people.append((b, wdays))
        # 検温板・介護記録（直近 days 日の利用日）
        for b, wdays in people:
            base_temp = Decimal(rnd.choice(['36.2', '36.4', '36.6']))
            for i in range(days):
                d = today - datetime.timedelta(days=i)
                if d.weekday() not in wdays:
                    continue
                temp = base_temp + Decimal(rnd.choice(['0.0', '0.1', '0.2', '-0.1']))
                if i == 3 and b.first_name == '花子':
                    temp = Decimal('37.6')
                VitalsLog.objects.create(facility=facility, beneficiary=b, date=d, slot=VitalsLog.SLOT_AM, temp=temp, bp_high=rnd.randint(118, 150),
                                         bp_low=rnd.randint(68, 88), pulse=rnd.randint(62, 84), spo2=rnd.randint(95, 99), meal_main=rnd.choice([10, 8, 8, 5]),
                                         meal_side=rnd.choice([10, 8, 7, 5]), water_ml=rnd.choice([400, 500, 600]), urine=rnd.randint(2, 5), stool=rnd.choice([0, 0, 1]),
                                         bath=d.weekday() in (wdays[0],), recorded_by=user)
                k = rnd.randrange(3)
                status = 'approved' if i >= 2 else ('review' if i == 1 else 'draft')
                r = CareRecord(facility=facility, beneficiary=b, date=d, author=user, body=BODIES[k], family_message=FAMILY[k], status=status,
                               keywords='体操・昼食・ぬり絵' if k == 0 else '足浴・カラオケ' if k == 1 else '疲れ・休息・水分',
                               checks={'meal': 'yes' if k != 2 else 'no', 'water': 'yes', 'toilet': 'yes', 'bath': 'yes' if d.weekday() == wdays[0] else 'no',
                                       'mood': 'yes' if k != 2 else 'no', 'activity': 'yes', 'pain': 'no', 'fall': 'no', 'family': 'yes' if k == 2 else 'no'},
                               ai_drafted=True)
                if status == 'approved' and user is not None:
                    r.approve(user)
                r.save()
        # ヒヤリハット 1 件
        b0 = people[1][0]
        from django.utils import timezone
        IncidentReport.objects.create(facility=facility, beneficiary=b0, kind='near', category='fall', occurred_at=timezone.now() - datetime.timedelta(days=5, hours=3),
                                      place='食堂', injury='none', situation='昼食後、車いすから立ち上がろうとしてバランスを崩しかけた。近くの職員がすぐ支え、転倒はなかった。',
                                      cause='ブレーキがかかっていなかった。食後で眠気があった。', response='本人にけがなし。看護師が確認。家族に電話で伝えた。',
                                      prevention='食後は職員が車いすのブレーキを声に出して確認する。立ち上がりの合図を決める。', reported_to=['family', 'doctor', 'manager'],
                                      reporter=user, status='review')
        for i in range(7):
            d = today - datetime.timedelta(days=i)
            n = sum(1 for _b, w in people if d.weekday() in w)
            if n:
                StaffingDay.objects.create(facility=facility, date=d, users=n, care_staff=2, nurses=1, counselors=1, trainers=1 if i % 2 == 0 else 0)
    return len(people)


def remove(facility):
    qs = Beneficiary.objects.filter(facility=facility, notes__startswith=MARK)
    n = qs.count()
    StaffingDay.objects.filter(facility=facility, note='').delete() if n else None
    qs.delete()
    return n

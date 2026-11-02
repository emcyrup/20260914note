"""
介護施設（やすらぎの森）の架空のサンプルデータ。名前・住所・内容はすべて架空。
- python manage.py nursing_demo --facility 1          … 入れる（足りない分だけ足す。もう全部入っていれば何もしない）
- python manage.py nursing_demo --facility 1 --remove … 消す（サンプルの印のある利用者とその記録・報告、サンプルの車両・運転手・人員配置だけ）
create_facility --preset care --demo からも呼ぶ。サンプルの利用者は備考の先頭に MARK を入れて見分ける。
"""
import datetime
import random
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from beneficiaries.models import Beneficiary, Guardian
from kaigo.models import CareInsurance

from .models import CarePlan, CareRecord, IncidentReport, Medication, StaffingDay, VitalsLog

MARK = '【サンプル】'
NOTE = MARK + '架空の利用者です（操作を試すためのもの）。不要になったら Actions の Manage (yasuragi) の demo_remove でまとめて消せます。'

# 姓, 名, かな姓, かな名, 性別, 生年月日, 要介護度, 利用の曜日（0=月）, 家族（姓, 名, 続柄）, 様子, 薬, 送迎（迎えの時刻, 送りの時刻, 車両の番号。None は家族の送迎）
PEOPLE = [
    ('見本', '花子', 'みほん', 'はなこ', 'female', datetime.date(1938, 4, 2), '要介護2', (0, 2, 4), ('見本', '一郎', '長男'),
     '膝の痛みがあり、歩くときは手すりを使う。おしゃべりが好き', [('アムロジピン', '1 錠　朝食後', ['morning'], '血圧', '架空クリニック 山田医師')], ('08:40', '16:10', 0)),
    ('試験', '太郎', 'しけん', 'たろう', 'male', datetime.date(1941, 9, 15), '要介護4', (1, 3, 5), ('試験', '良子', '妻'),
     '車いす。移乗に介助が要る。食事はセッティングで自力', [('マグミット', '1 錠　朝夕食後', ['morning', 'evening'], '便秘', '架空クリニック 山田医師'),
                                                          ('ロキソニンテープ', '1 枚　腰', ['other'], '腰痛', '架空整形外科')], ('08:50', '16:20', 1)),
    ('架空', '和子', 'かくう', 'かずこ', 'female', datetime.date(1945, 1, 20), '要支援2', (0, 3), ('架空', '真一', '長男'),
     '一人暮らし。物忘れが少しある。体操が好き', [], ('09:00', '16:00', 0)),
    ('仮名', '次郎', 'かめい', 'じろう', 'male', datetime.date(1936, 11, 8), '要介護3', (1, 2, 4), ('仮名', '恵子', '長女'),
     '認知症あり。声かけで落ち着く。歌が好き', [('ドネペジル', '1 錠　朝食後', ['morning'], '認知症', '架空メンタルクリニック')], ('08:45', '16:15', 0)),
    ('見本', '正子', 'みほん', 'まさこ', 'female', datetime.date(1940, 6, 30), '要介護1', (0, 2, 4), ('見本', '浩', '長男'),
     '杖で歩く。手芸が得意で、ほかの人に教えるのが好き', [('アトルバスタチン', '1 錠　夕食後', ['evening'], 'コレステロール', '架空内科')], ('09:05', '16:05', 0)),
    ('試験', '清', 'しけん', 'きよし', 'male', datetime.date(1939, 2, 11), '要介護2', (0, 1, 3, 5), ('試験', '美香', '長女'),
     '脳梗塞のあと右手に軽いまひ。リハビリに熱心。将棋が好き', [('バイアスピリン', '1 錠　朝食後', ['morning'], '脳梗塞の再発予防', '架空脳神経外科'),
                                                               ('ランソプラゾール', '1 錠　朝食後', ['morning'], '胃の保護', '架空脳神経外科')], ('08:35', '16:25', 0)),
    ('架空', 'トメ', 'かくう', 'とめ', 'female', datetime.date(1932, 8, 3), '要介護5', (1, 4), ('架空', '健', '長男'),
     'ほぼ寝たきり。リクライニングの車いす。食事はきざみ食で介助。入浴は機械浴', [('ムコスタ', '1 錠　朝昼夕食後', ['morning', 'noon', 'evening'], '胃の保護', '架空在宅クリニック'),
                                                                          ('アズノール軟膏', '仙骨部に塗る', ['other'], '皮膚の保護', '架空在宅クリニック')], ('09:10', '15:50', 1)),
    ('仮名', '勇', 'かめい', 'いさむ', 'male', datetime.date(1944, 12, 24), '要介護1', (2, 5), ('仮名', '幸江', '妻'),
     '糖尿病。昼食前に血糖を測る。散歩が好き', [('メトホルミン', '1 錠　朝夕食後', ['morning', 'evening'], '糖尿病', '架空内科')], None),
    ('例示', '幸子', 'れいじ', 'さちこ', 'female', datetime.date(1943, 3, 5), '要支援1', (0, 3), ('例示', '修', '長男'),
     'ほぼ自立。人と話すのが目的で通っている。料理の話が好き', [], ('09:15', '15:55', 0)),
    ('例示', '茂', 'れいじ', 'しげる', 'male', datetime.date(1937, 7, 19), '要介護3', (1, 3, 5), ('例示', '和美', '長女'),
     'パーキンソン病。すくみ足があり、歩くときは見守り。薬の時間を守る', [('レボドパ', '1 錠　朝昼夕食後', ['morning', 'noon', 'evening'], 'パーキンソン病', '架空神経内科')], ('08:55', '16:30', 1)),
    ('模擬', '春子', 'もぎ', 'はるこ', 'female', datetime.date(1935, 4, 10), '要介護2', (0, 2, 4), ('模擬', '進', '長男'),
     '耳が遠い。大きめの声でゆっくり話す。歌と体操が好き', [('ラシックス', '1 錠　朝食後', ['morning'], 'むくみ', '架空循環器クリニック')], ('08:30', '16:20', 0)),
    ('模擬', '健一', 'もぎ', 'けんいち', 'male', datetime.date(1948, 10, 1), '要介護1', (1, 4, 5), ('模擬', '洋子', '妻'),
     '若年で通い始めた。軽い認知症。囲碁と散歩が好き', [], None),
]

VEHICLES = [('1 号車（サンプル）', 7, '白のワゴン'), ('2 号車（車いす・サンプル）', 4, 'リフト付き')]
DRIVERS = ['運転手 A（サンプル）', '運転手 B（サンプル）']

BODIES = [
    '午前は体操に参加し、腕を上げる動きも最後まで続けた。昼食は主食・副食とも 8 割ほど。午後はぬり絵をして、隣の人と話しながら過ごした。排泄は自分でトイレに行き、問題なし。帰りの際に「また来る」と笑顔。',
    '来所時の体温は平熱。午前は足浴と機能訓練（立ち上がりの練習 10 回）。昼食は副食を少し残した。午後はカラオケに参加し、好きな歌を 2 曲歌った。排便あり。特に変わりなし。',
    '少し表情が固く、午前は休みがち。声をかけると「眠い」とのこと。昼食は 5 割。午後は窓際でゆっくり過ごし、水分をこまめにすすめた。帰りには表情がやわらぎ、家族には様子を伝えた。',
    '入浴（一般浴）を行い、背中は職員が介助した。皮膚に赤みなし。昼食は全量。午後は手芸の時間にほかの利用者に編み方を教え、終始楽しそうだった。',
]
FAMILY = [
    '本日は体操とぬり絵に参加され、昼食もしっかり召し上がりました。お帰りの際には「また来る」と笑顔でした。',
    '本日は足浴と立ち上がりの練習をされました。カラオケでは好きな歌を 2 曲歌われ、楽しそうなご様子でした。',
    '本日は少しお疲れのご様子で、午前は休みながら過ごされました。昼食は半分ほどでしたが、水分はこまめに取られています。ご自宅でもゆっくりお休みください。',
    '本日は入浴をされ、さっぱりされました。昼食は全部召し上がり、午後は手芸でほかの方に編み方を教えてくださいました。',
]
KEYWORDS = ['体操・昼食・ぬり絵', '足浴・カラオケ', '疲れ・休息・水分', '入浴・手芸']
# 外国人スタッフが母国語（ベトナム語）で入れた例。本文は日本語に整えたもの
VI_MEMO = 'Buổi sáng bà tham gia tập thể dục. Ăn trưa được khoảng 8 phần. Buổi chiều bà tô màu và nói chuyện vui vẻ với mọi người.'

INCIDENTS = [
    dict(kind='near', category='fall', days=5, place='食堂', injury='none', reported_to=['family', 'doctor', 'manager'], status='review',
         situation='昼食後、車いすから立ち上がろうとしてバランスを崩しかけた。近くの職員がすぐ支え、転倒はなかった。',
         cause='ブレーキがかかっていなかった。食後で眠気があった。', response='本人にけがなし。看護師が確認。家族に電話で伝えた。',
         prevention='食後は職員が車いすのブレーキを声に出して確認する。立ち上がりの合図を決める。', who=1),
    dict(kind='accident', category='skin', days=12, place='浴室', injury='minor', reported_to=['family', 'doctor', 'manager', 'caremanager'], status='approved',
         situation='入浴の介助中、浴槽のふちに右のすねが当たり、2 cm ほどの皮膚のはがれができた。',
         cause='機械浴のストレッチャーから移るとき、足の位置の確認が足りなかった。皮膚が薄くなっている。',
         response='看護師が洗って保護材を貼った。家族とケアマネジャーに電話で伝えた。翌日、治りを確認した。',
         prevention='移るときは 2 人で、足の位置を声に出して確かめる。浴槽のふちにクッション材を付ける。', who=6),
    dict(kind='near', category='medication', days=2, place='食堂', injury='none', reported_to=['manager'], status='draft',
         situation='昼食後の薬を配るとき、別の利用者の薬袋を手に取りかけた。名前を読み上げたときに気づき、渡す前に止めた。',
         cause='同じ名字の利用者が隣の席だった。薬袋の名前を声に出す前に手に取った。',
         response='正しい薬を渡した。誤って飲んだ薬はない。', prevention='薬袋は名前と顔を確かめ、名前を声に出してから渡す。同じ名字の人は席を離す。', who=4),
]


def is_demo(b):
    return (b.notes or '').startswith(MARK)


def _vehicles(facility):
    """サンプルの車両と運転手（無ければ作る）。車両の番号順のリスト"""
    from transport.models import Driver, Vehicle
    drivers = []
    for i, name in enumerate(DRIVERS):
        d = Driver.objects.filter(facility=facility, name=name).first() or Driver.objects.create(facility=facility, name=name, note=MARK, order=90 + i)
        drivers.append(d)
    out = []
    for i, (name, cap, plate) in enumerate(VEHICLES):
        v = Vehicle.objects.filter(facility=facility, name=name).first()
        if v is None:
            v = Vehicle.objects.create(facility=facility, name=name, capacity=cap, plate=plate, default_driver=drivers[i], note=MARK, order=90 + i)
        out.append(v)
    return out


def _person(facility, row, idx, today, rnd, user):
    (ln, fn, lk, fk, gender, dob, level, wdays, fam, note, meds, _ride) = row
    b = Beneficiary(facility=facility, last_name=ln, first_name=fn, last_name_kana=lk, first_name_kana=fk, gender=gender,
                    date_of_birth=dob, notes=NOTE + '\n' + note, status=Beneficiary.STATUS_ACTIVE, address='架空市 見本町 1-1（サンプル）',
                    admission_date=today - datetime.timedelta(days=rnd.randint(120, 900)))
    for i, f in enumerate(('weekday_mon', 'weekday_tue', 'weekday_wed', 'weekday_thu', 'weekday_fri', 'weekday_sat')):
        setattr(b, f, i in wdays)
    b.save()
    Guardian.objects.create(beneficiary=b, last_name=fam[0], first_name=fam[1], relation='other', memo=fam[2], is_primary=True, phone='000-0000-0000')
    CareInsurance.objects.create(beneficiary=b, insurer_number='999999', insured_number=f'00000000{idx + 1:02d}', care_level=level,
                                 valid_from=today.replace(day=1) - datetime.timedelta(days=200), valid_until=today + datetime.timedelta(days=300), copay_ratio=1)
    for name, dose, timings, purpose, doc in meds:
        Medication.objects.create(facility=facility, beneficiary=b, name=name, dose=dose, timings=timings, purpose=purpose, prescriber=doc,
                                  start_on=today - datetime.timedelta(days=90), review_due=today + datetime.timedelta(days=rnd.choice([-3, 5, 20, 45, 60])))
    CarePlan.objects.create(facility=facility, beneficiary=b, created_on=today - datetime.timedelta(days=60), period_from=today - datetime.timedelta(days=60),
                            period_to=today + datetime.timedelta(days=120), care_level=level, planner='生活相談員（サンプル）',
                            wishes_person='できることは自分で続けたい。みんなと話したい。', wishes_family='安全に通ってほしい。体調の変化を教えてほしい。',
                            policy='本人のペースを大切にし、できる動作は見守りながら自分で行ってもらう。体調の変化は家族と共有する。',
                            long_goal='歩行の力を保ち、通所を続ける。', short_goal='週 2 回の体操に参加し、立ち上がりの練習を続ける。',
                            rows=[{'goal': '転ばずに移動する', 'service': '機能訓練', 'detail': '立ち上がりの練習・歩行の見守り', 'frequency': '来所ごと 15 分', 'staff': '機能訓練指導員'},
                                  {'goal': '楽しく過ごす', 'service': 'レクリエーション', 'detail': '体操・カラオケ・ぬり絵', 'frequency': '毎回', 'staff': '介護職員'}],
                            review_due=today + datetime.timedelta(days=rnd.choice([10, 60, 120])), created_by=user)
    return b


def _ride(b, row, vehicles):
    from transport.models import TransportProfile
    ride = row[11]
    if ride is None or TransportProfile.objects.filter(beneficiary=b).exists():
        return
    hm = lambda s: datetime.time(*map(int, s.split(':')))  # noqa: E731
    TransportProfile.objects.create(beneficiary=b, pickup=True, pickup_place='自宅', pickup_time=hm(ride[0]), dropoff=True, dropoff_place='自宅',
                                    dropoff_time=hm(ride[1]), default_vehicle=vehicles[ride[2]],
                                    note='車いすのまま乗る' if ride[2] == 1 else '')


def _days(facility, b, row, today, rnd, user, days):
    """検温板（朝。重い人は夕も）と介護記録。今日は記入の途中（未記入・下書き・確認待ちが混ざる）"""
    level, wdays = row[6], row[7]
    base_temp = Decimal(rnd.choice(['36.2', '36.4', '36.6']))
    heavy = level in ('要介護4', '要介護5')
    for i in range(days):
        d = today - datetime.timedelta(days=i)
        if d.weekday() not in wdays:
            continue
        if i == 0 and rnd.random() < 0.3:
            continue  # 今日の分はまだ（ホームの「未記入」の例）
        temp = base_temp + Decimal(rnd.choice(['0.0', '0.1', '0.2', '-0.1']))
        if i == 3 and b.first_name == '花子':
            temp = Decimal('37.6')
        vit = dict(bp_high=rnd.randint(118, 150), bp_low=rnd.randint(68, 88), pulse=rnd.randint(62, 84), spo2=rnd.randint(95, 99), recorded_by=user)
        if b.first_name == 'トメ' and i == 1:
            vit['spo2'] = 92
        VitalsLog.objects.create(facility=facility, beneficiary=b, date=d, slot=VitalsLog.SLOT_AM, temp=temp, meal_main=rnd.choice([10, 8, 8, 5]),
                                 meal_side=rnd.choice([10, 8, 7, 5]), water_ml=rnd.choice([400, 500, 600]), urine=rnd.randint(2, 5), stool=rnd.choice([0, 0, 1]),
                                 bath=d.weekday() == wdays[0], **vit)
        if heavy or i == 0:
            VitalsLog.objects.create(facility=facility, beneficiary=b, date=d, slot=VitalsLog.SLOT_PM, temp=temp + Decimal('0.1'), bp_high=vit['bp_high'] - 4,
                                     bp_low=vit['bp_low'] - 2, pulse=vit['pulse'] + 2, spo2=max(vit['spo2'], 95), water_ml=rnd.choice([200, 300]),
                                     urine=rnd.randint(1, 3), recorded_by=user)
        k = 3 if d.weekday() == wdays[0] and not heavy else rnd.randrange(3)
        status = 'approved' if i >= 2 else ('review' if i == 1 else rnd.choice(['draft', 'review']))
        r = CareRecord(facility=facility, beneficiary=b, date=d, author=user, body=BODIES[k], family_message=FAMILY[k], status=status,
                       keywords=KEYWORDS[k], ai_drafted=True,
                       checks={'meal': 'yes' if k != 2 else 'no', 'water': 'yes', 'toilet': 'yes', 'bath': 'yes' if d.weekday() == wdays[0] else 'no',
                               'mood': 'yes' if k != 2 else 'no', 'activity': 'yes', 'pain': 'no', 'fall': 'no', 'family': 'yes' if k == 2 else 'no'})
        if b.first_name == '正子' and i <= 1:
            r.lang, r.memo, r.body, r.family_message, r.keywords = 'vi', VI_MEMO, BODIES[0], FAMILY[0], KEYWORDS[0]
        if b.first_name == 'トメ':
            r.internal_note = '仙骨部の皮膚に赤みなし。体位変換 2 時間ごと。'
        if status == 'approved' and user is not None:
            r.approve(user)
        r.save()


def add(facility, user=None, days=14, stdout=None):
    """入れる。足りないサンプルの利用者だけを足す（もう全部入っていれば何もしない）。戻り値は足した利用者の数"""
    rnd = random.Random(20261031)
    today = datetime.date.today()
    have = {(b.last_name, b.first_name): b for b in Beneficiary.objects.filter(facility=facility, notes__startswith=MARK)}
    if all((row[0], row[1]) in have for row in PEOPLE) and IncidentReport.objects.filter(facility=facility, situation=INCIDENTS[-1]['situation']).exists():
        return 0
    with transaction.atomic():
        vehicles = _vehicles(facility) if facility.use_transport else []
        people, added = [], 0
        for idx, row in enumerate(PEOPLE):
            b = have.get((row[0], row[1]))
            if b is None:
                b = _person(facility, row, idx, today, rnd, user)
                _days(facility, b, row, today, rnd, user, days)
                added += 1
            if vehicles:
                _ride(b, row, vehicles)
            people.append((b, row[7]))
        # ヒヤリハット・事故報告（同じ内容が無いものだけ）
        for inc in INCIDENTS:
            if IncidentReport.objects.filter(facility=facility, situation=inc['situation']).exists():
                continue
            r = IncidentReport(facility=facility, beneficiary=people[inc['who']][0], kind=inc['kind'], category=inc['category'],
                               occurred_at=timezone.now() - datetime.timedelta(days=inc['days'], hours=3), place=inc['place'], injury=inc['injury'],
                               situation=inc['situation'], cause=inc['cause'], response=inc['response'], prevention=inc['prevention'],
                               reported_to=inc['reported_to'], reporter=user, status=inc['status'])
            if inc['status'] == 'approved' and user is not None:
                r.approve(user)
            r.save()
        # 人員配置（直近 7 日。サンプルの日だけ作る・直す。手で入れた日は触らない）
        for i in range(7):
            d = today - datetime.timedelta(days=i)
            n = sum(1 for _b, w in people if d.weekday() in w)
            if not n:
                continue
            vals = dict(users=n, care_staff=2 if n <= 7 else 3, nurses=1, counselors=1, trainers=1 if i % 2 == 0 else 0, note=MARK)
            row = StaffingDay.objects.filter(facility=facility, date=d).first()
            if row is None:
                StaffingDay.objects.create(facility=facility, date=d, **vals)
            elif row.note == MARK or (row.note == '' and (row.care_staff, row.nurses, row.counselors) == (2, 1, 1)):  # 前の版のサンプル
                for k, v in vals.items():
                    setattr(row, k, v)
                row.save()
    return added


def remove(facility):
    """サンプルの利用者（とその記録・報告・送迎の設定）、サンプルの車両・運転手・人員配置だけを消す"""
    from transport.models import Driver, Vehicle
    qs = Beneficiary.objects.filter(facility=facility, notes__startswith=MARK)
    n = qs.count()
    with transaction.atomic():
        IncidentReport.objects.filter(facility=facility, beneficiary__in=qs).delete()
        qs.delete()
        StaffingDay.objects.filter(facility=facility, note=MARK).delete()
        Vehicle.objects.filter(facility=facility, note=MARK).delete()
        Driver.objects.filter(facility=facility, note=MARK).delete()
    return n

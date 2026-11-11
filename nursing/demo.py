"""
介護施設の架空のサンプルデータ。名前・住所・内容はすべて架空。
- python manage.py nursing_demo --facility 1                    … 入れる（足りない分だけ足す。もう全部入っていれば何もしない）
- python manage.py nursing_demo --facility 1 --scenario roken   … 老健・通所リハのサンプル（通所リハ 36 人・入所 8 人・車両 4 台・運転手 4 人）
- python manage.py nursing_demo --facility 1 --remove           … 消す（サンプルの印のある利用者とその記録・報告、サンプルの車両・運転手・人員配置だけ）
create_facility --preset care|roken --demo からも呼ぶ。サンプルの利用者は備考の先頭に MARK を入れて見分ける。

シナリオ
- care  ：やすらぎの森（通所介護）。利用者 12 人
- roken ：介護老人保健施設・通所リハ（2026-11-05 の相談）。通所リハ 36 人（1 日 9〜15 人が送迎）・入所 8 人（朝夕の検温）。
          送迎の条件（車いす・車両の定員と車いす枠・運転できる車両・同乗NG）がわざと数か所引っかかるようにしてある
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
NOTE = MARK + '架空の利用者です（操作を試すためのもの）。不要になったら Actions の Manage の demo_remove でまとめて消せます。'

# やすらぎの森（通所介護）：姓, 名, かな姓, かな名, 性別, 生年月日, 要介護度, 利用の曜日（0=月）, 家族（姓, 名, 続柄）, 様子, 薬, 送迎（迎えの時刻, 送りの時刻, 車両の番号。None は家族の送迎）
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

# 車両：名前, 定員, ナンバー・色, 車いすのまま乗れる台数　／　運転手：名前, 運転できる車両の番号（None はどれでも）
VEHICLES = [('1 号車（サンプル）', 7, '白のワゴン', 0), ('2 号車（車いす・サンプル）', 4, 'リフト付き', 2)]
DRIVERS = [('運転手 A（サンプル）', None), ('運転手 B（サンプル）', None)]

# 老健・通所リハ（2026-11-05 の相談）：通所リハ 36 人は名前を組み合わせて作る。入所 8 人は曜日なし・朝夕の検温
ROKEN_VEHICLES = [('1 号車 ハイエース（サンプル）', 10, '白', 0), ('2 号車 リフト車（サンプル）', 6, '車いす 2 台', 2),
                  ('3 号車 セレナ（サンプル）', 7, '銀', 1), ('4 号車 軽ワゴン（サンプル）', 3, '黄色ナンバー', 0)]
ROKEN_DRIVERS = [('運転手 A（サンプル）', (0, 2)), ('運転手 B（サンプル）', (1,)), ('運転手 C（サンプル）', None), ('運転手 D（サンプル）', (3,))]
ROKEN_SURNAMES = [('見本', 'みほん'), ('試験', 'しけん'), ('架空', 'かくう'), ('仮名', 'かめい'), ('例示', 'れいじ'), ('模擬', 'もぎ'),
                  ('仮称', 'かしょう'), ('標本', 'ひょうほん'), ('試作', 'しさく'), ('例題', 'れいだい'), ('想定', 'そうてい'), ('仮想', 'かそう')]
ROKEN_GIVEN = [('花子', 'はなこ', 'female'), ('太郎', 'たろう', 'male'), ('和子', 'かずこ', 'female'), ('次郎', 'じろう', 'male'), ('正子', 'まさこ', 'female'),
               ('清', 'きよし', 'male'), ('トメ', 'とめ', 'female'), ('勇', 'いさむ', 'male'), ('幸子', 'さちこ', 'female'), ('茂', 'しげる', 'male'),
               ('春子', 'はるこ', 'female'), ('健一', 'けんいち', 'male')]
ROKEN_TOWNS = ['北町', '南町', '東町', '西町', '中央', '川沿', '山手', '駅前']
ROKEN_LEVELS = ['要支援1', '要支援2', '要介護1', '要介護1', '要介護2', '要介護2', '要介護3', '要介護3', '要介護4', '要介護5']
ROKEN_NOTES = ['膝の痛みがあり、歩くときは手すりを使う', '脳梗塞のあと右手に軽いまひ。リハビリに熱心', '認知症あり。声かけで落ち着く',
               '車いす。移乗に介助が要る', '杖で歩く。転倒に注意', '一人暮らし。物忘れが少しある', '耳が遠い。大きめの声でゆっくり話す',
               '糖尿病。昼食前に血糖を測る', 'パーキンソン病。すくみ足があり歩くときは見守り', '骨折のあと。平行棒での歩行練習中']
ROKEN_MEDS = [('アムロジピン', '1 錠　朝食後', ['morning'], '血圧', '併設の病院'), ('マグミット', '1 錠　朝夕食後', ['morning', 'evening'], '便秘', '併設の病院'),
              ('バイアスピリン', '1 錠　朝食後', ['morning'], '脳梗塞の再発予防', '併設の病院'), ('ドネペジル', '1 錠　朝食後', ['morning'], '認知症', '併設の病院'),
              ('メトホルミン', '1 錠　朝夕食後', ['morning', 'evening'], '糖尿病', '併設の病院'), ('レボドパ', '1 錠　朝昼夕食後', ['morning', 'noon', 'evening'], 'パーキンソン病', '併設の病院')]
# 通所リハの曜日の組（週 2〜3 回。月〜土）
ROKEN_WDAYS = [(0, 2, 4), (1, 3, 5), (0, 3), (1, 4), (2, 5), (0, 2), (1, 3), (0, 4), (2, 4), (1, 5), (0, 1, 3), (3, 5)]
# 同乗NG の組（通所リハの番号）。わざと同じ車に乗る組を 1 つ入れて、送迎表で赤く出るようにする
ROKEN_RIDE_NG = [(2, 14), (7, 31)]


def roken_people():
    """老健・通所リハの架空の利用者（通所リハ 36 人 → 入所 8 人）。毎回同じ並びになる"""
    rnd = random.Random(20261105)
    rows = []
    for i in range(36):
        ln, lk = ROKEN_SURNAMES[i % 12]
        fn, fk, gender = ROKEN_GIVEN[(i * 5 + i // 12) % 12]
        wdays = ROKEN_WDAYS[i % 12]
        level = ROKEN_LEVELS[(i * 7) % 10]
        wheelchair = level == '要介護5' or i == 5        # 7・17・27 番（要介護5）と 5 番
        town = ROKEN_TOWNS[i % 8]
        # 車両は地区で分ける（北・南・東 → 1 号車、西・中央 → 3 号車、川沿 → 4 号車、山手・駅前 → 2 号車）。
        # 車いすの人はリフト車（2 号車）か 3 号車（枠 1）。ただし 5 番は 4 号車のまま（車いす枠が無い車に入っている例）
        vehicle = {0: 0, 1: 0, 2: 0, 3: 2, 4: 2, 5: 3, 6: 1, 7: 1}[i % 8]
        if wheelchair and i != 5:
            vehicle = 2 if i == 27 else 1
        ride = None if i in (9, 21, 33) else (f'{8 + (i % 2)}:{(i * 7) % 6 * 10:02d}', f'{15 + (i % 2)}:{(i * 3) % 6 * 10:02d}', vehicle)
        meds = [ROKEN_MEDS[(i * 3) % 6]] if i % 3 else []
        rows.append({'ln': ln, 'fn': fn, 'lk': lk, 'fk': fk, 'gender': gender, 'dob': datetime.date(1930 + (i * 7) % 20, 1 + i % 12, 1 + (i * 3) % 28),
                     'level': level, 'wdays': wdays, 'fam': (ln, '家族', '長男' if i % 2 else '長女'), 'note': ROKEN_NOTES[i % 10] + '（通所リハ）',
                     'meds': meds, 'ride': ride, 'wheelchair': wheelchair, 'address': f'架空市 {town} {1 + i % 9}-{1 + (i * 3) % 20}（サンプル）', 'stay': False})
    for i in range(8):
        ln, lk = ROKEN_SURNAMES[(i * 5 + 3) % 12]
        fn, fk, gender = ROKEN_GIVEN[(i * 7 + 5) % 12]
        level = ['要介護3', '要介護4', '要介護5', '要介護4', '要介護3', '要介護5', '要介護2', '要介護4'][i]
        rows.append({'ln': ln, 'fn': fn, 'lk': lk, 'fk': fk, 'gender': gender, 'dob': datetime.date(1928 + i * 2, 1 + (i * 5) % 12, 1 + (i * 11) % 28),
                     'level': level, 'wdays': (), 'fam': (ln, '家族', '長男' if i % 2 else '長女'), 'note': ['在宅復帰を目指してリハビリ中', '骨折のあと入所。歩行練習中', '認知症あり。夜間に不穏',
                                                                                                        '経管栄養。褥瘡の予防', '脳梗塞のあと。言葉が出にくい', '誤嚥に注意。とろみ付き', '退所の相談中', '糖尿病。インスリン'][i] + '（入所）',
                     'meds': [ROKEN_MEDS[i % 6]], 'ride': None, 'wheelchair': i % 2 == 0, 'address': '', 'stay': True})
    del rnd
    return rows


def _as_row(t):
    """やすらぎの森の tuple を dict に"""
    if isinstance(t, dict):
        return t
    ln, fn, lk, fk, gender, dob, level, wdays, fam, note, meds, ride = t
    return {'ln': ln, 'fn': fn, 'lk': lk, 'fk': fk, 'gender': gender, 'dob': dob, 'level': level, 'wdays': wdays, 'fam': fam, 'note': note,
            'meds': meds, 'ride': ride, 'wheelchair': ride is not None and ride[2] == 1, 'address': '架空市 見本町 1-1（サンプル）', 'stay': False}


SCENARIOS = {
    'care': {'people': lambda: [_as_row(t) for t in PEOPLE], 'vehicles': VEHICLES, 'drivers': DRIVERS, 'ride_ng': [], 'label': 'やすらぎの森（通所介護）'},
    'roken': {'people': roken_people, 'vehicles': ROKEN_VEHICLES, 'drivers': ROKEN_DRIVERS, 'ride_ng': ROKEN_RIDE_NG, 'label': '老健・通所リハ'},
}

BODIES = [
    '午前は体操に参加し、腕を上げる動きも最後まで続けた。昼食は主食・副食とも 8 割ほど。午後はぬり絵をして、隣の人と話しながら過ごした。排泄は自分でトイレに行き、問題なし。帰りの際に「また来る」と笑顔。',
    '来所時の体温は平熱。午前は足浴と機能訓練（立ち上がりの練習 10 回）。昼食は副食を少し残した。午後はカラオケに参加し、好きな歌を 2 曲歌った。排便あり。特に変わりなし。',
    '少し表情が固く、午前は休みがち。声をかけると「眠い」とのこと。昼食は 5 割。午後は窓際でゆっくり過ごし、水分をこまめにすすめた。帰りには表情がやわらぎ、家族には様子を伝えた。',
    '入浴（一般浴）を行い、背中は職員が介助した。皮膚に赤みなし。昼食は全量。午後は手芸の時間にほかの利用者に編み方を教え、終始楽しそうだった。',
]
STAY_BODIES = [
    '朝の検温は平熱。午前は理学療法士と平行棒での歩行練習を 20 分。昼食は全量で、とろみ付きの水分を 300ml。午後は居室で休み、夕方に廊下を車いすで散歩した。夜間の排泄は 2 回、介助で問題なし。',
    '朝食は 7 割。午前に作業療法で手の運動を行い、「疲れた」と言うので途中で休憩をはさんだ。昼食後に入浴（機械浴）。皮膚の発赤なし。夕食は全量。消灯後は落ち着いて眠っていた。',
    '朝から少しぼんやりしており、水分をこまめにすすめた。午前のリハビリは見学のみ。昼食は 5 割。午後に家族の面会があり、表情が明るくなった。夕方の体温は平熱。',
]
FAMILY = [
    '本日は体操とぬり絵に参加され、昼食もしっかり召し上がりました。お帰りの際には「また来る」と笑顔でした。',
    '本日は足浴と立ち上がりの練習をされました。カラオケでは好きな歌を 2 曲歌われ、楽しそうなご様子でした。',
    '本日は少しお疲れのご様子で、午前は休みながら過ごされました。昼食は半分ほどでしたが、水分はこまめに取られています。ご自宅でもゆっくりお休みください。',
    '本日は入浴をされ、さっぱりされました。昼食は全部召し上がり、午後は手芸でほかの方に編み方を教えてくださいました。',
]
STAY_FAMILY = [
    '本日は平行棒での歩行練習を 20 分行い、昼食は全部召し上がりました。夕方は車いすで廊下を散歩されました。',
    '本日は作業療法で手の運動をされ、午後に入浴されました。皮膚のトラブルはありません。夜は落ち着いてお休みでした。',
    '本日は少しお疲れのご様子で、リハビリは見学でした。午後の面会のあとは表情が明るくなりました。',
]
KEYWORDS = ['体操・昼食・ぬり絵', '足浴・カラオケ', '疲れ・休息・水分', '入浴・手芸']
STAY_KEYWORDS = ['歩行練習・散歩', '作業療法・入浴', 'ぼんやり・面会']
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
ROKEN_INCIDENTS = INCIDENTS + [
    dict(kind='near', category='transport', days=3, place='送迎車', injury='none', reported_to=['manager'], status='review',
         situation='迎えの車で、車いすの固定ベルトをかけ忘れたまま発車しかけた。同乗の職員が気づいて停車し、固定してから出発した。',
         cause='乗車の人数が多く、固定の確認を 1 人で行っていた。', response='けがなし。本人と家族に説明した。',
         prevention='車いすの固定は運転手と同乗の職員の 2 人で声に出して確認する。送迎表に車いすの人を印で出す。', who=5),
]


def is_demo(b):
    return (b.notes or '').startswith(MARK)


def _vehicles(facility, scenario):
    """サンプルの車両と運転手（無ければ作る）。車両の番号順のリスト"""
    from transport.models import Driver, Vehicle
    out = []
    for i, (name, cap, plate, wc) in enumerate(scenario['vehicles']):
        v = Vehicle.objects.filter(facility=facility, name=name).first()
        if v is None:
            v = Vehicle.objects.create(facility=facility, name=name, capacity=cap, plate=plate, wheelchair_slots=wc, note=MARK, order=90 + i)
        out.append(v)
    drivers = []
    for i, (name, allowed) in enumerate(scenario['drivers']):
        d = Driver.objects.filter(facility=facility, name=name).first()
        if d is None:
            d = Driver.objects.create(facility=facility, name=name, note=MARK, order=90 + i)
            if allowed is not None:
                d.vehicles.set([out[k] for k in allowed])
        drivers.append(d)
    for i, v in enumerate(out):
        if v.default_driver_id is None and i < len(drivers):
            # 3 号車の運転手をわざと「4 号車しか運転できない D」にして、送迎表で「運転不可」が出る例にする（roken）
            v.default_driver = drivers[3] if (len(drivers) > 3 and i == 2) else drivers[i]
            v.save(update_fields=['default_driver'])
    return out


def _person(facility, row, idx, today, rnd, user):
    b = Beneficiary(facility=facility, last_name=row['ln'], first_name=row['fn'], last_name_kana=row['lk'], first_name_kana=row['fk'], gender=row['gender'],
                    date_of_birth=row['dob'], notes=NOTE + '\n' + row['note'], status=Beneficiary.STATUS_ACTIVE, address=row['address'],
                    admission_date=today - datetime.timedelta(days=rnd.randint(120, 900)))
    for i, f in enumerate(('weekday_mon', 'weekday_tue', 'weekday_wed', 'weekday_thu', 'weekday_fri', 'weekday_sat')):
        setattr(b, f, i in row['wdays'])
    b.save()
    fam = row['fam']
    Guardian.objects.create(beneficiary=b, last_name=fam[0], first_name=fam[1], relation='other', memo=fam[2], is_primary=True, phone='000-0000-0000')
    CareInsurance.objects.create(beneficiary=b, insurer_number='999999', insured_number=f'00000000{idx + 1:02d}', care_level=row['level'],
                                 valid_from=today.replace(day=1) - datetime.timedelta(days=200), valid_until=today + datetime.timedelta(days=300), copay_ratio=1)
    for name, dose, timings, purpose, doc in row['meds']:
        Medication.objects.create(facility=facility, beneficiary=b, name=name, dose=dose, timings=timings, purpose=purpose, prescriber=doc,
                                  start_on=today - datetime.timedelta(days=90), review_due=today + datetime.timedelta(days=rnd.choice([-3, 5, 20, 45, 60])))
    stay = row['stay']
    CarePlan.objects.create(facility=facility, beneficiary=b, created_on=today - datetime.timedelta(days=60), period_from=today - datetime.timedelta(days=60),
                            period_to=today + datetime.timedelta(days=120), care_level=row['level'], planner='支援相談員（サンプル）' if stay else '生活相談員（サンプル）',
                            wishes_person='家に帰って自分のことは自分でしたい。' if stay else 'できることは自分で続けたい。みんなと話したい。',
                            wishes_family='安全に過ごしてほしい。体調の変化を教えてほしい。',
                            policy='本人のペースを大切にし、できる動作は見守りながら自分で行ってもらう。体調の変化は家族と共有する。',
                            long_goal='歩行の力を取り戻し、在宅に復帰する。' if stay else '歩行の力を保ち、通所を続ける。',
                            short_goal='平行棒での歩行を 20 m 続ける。' if stay else '週 2 回の体操に参加し、立ち上がりの練習を続ける。',
                            rows=[{'goal': '転ばずに移動する', 'service': '個別リハビリ', 'detail': '歩行練習・立ち上がりの練習', 'frequency': '週 3 回 20 分', 'staff': '理学療法士'},
                                  {'goal': '楽しく過ごす', 'service': 'レクリエーション', 'detail': '体操・カラオケ・手芸', 'frequency': '毎回', 'staff': '介護職員'}],
                            review_due=today + datetime.timedelta(days=rnd.choice([10, 60, 120])), created_by=user)
    return b


def _ride(b, row, vehicles):
    from transport.models import TransportProfile
    ride = row['ride']
    if ride is None or TransportProfile.objects.filter(beneficiary=b).exists():
        return
    hm = lambda s: datetime.time(*map(int, s.split(':')))  # noqa: E731
    TransportProfile.objects.create(beneficiary=b, pickup=True, pickup_place='自宅', pickup_time=hm(ride[0]), dropoff=True, dropoff_place='自宅',
                                    dropoff_time=hm(ride[1]), default_vehicle=vehicles[ride[2]], wheelchair=row['wheelchair'],
                                    note='車いすのまま乗る' if row['wheelchair'] else '')


def _days(facility, b, row, today, rnd, user, days):
    """検温板（朝。重い人・入所の人は夕も）と介護記録。今日は記入の途中（未記入・下書き・確認待ちが混ざる）"""
    level, wdays, stay = row['level'], row['wdays'], row['stay']
    base_temp = Decimal(rnd.choice(['36.2', '36.4', '36.6']))
    heavy = level in ('要介護4', '要介護5')
    bodies, fams, kws = (STAY_BODIES, STAY_FAMILY, STAY_KEYWORDS) if stay else (BODIES, FAMILY, KEYWORDS)
    for i in range(days):
        d = today - datetime.timedelta(days=i)
        if not stay and d.weekday() not in wdays:
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
                                 bath=(d.weekday() == wdays[0]) if wdays else (d.weekday() in (1, 4)), **vit)
        if heavy or stay or i == 0:
            VitalsLog.objects.create(facility=facility, beneficiary=b, date=d, slot=VitalsLog.SLOT_PM, temp=temp + Decimal('0.1'), bp_high=vit['bp_high'] - 4,
                                     bp_low=vit['bp_low'] - 2, pulse=vit['pulse'] + 2, spo2=max(vit['spo2'], 95), water_ml=rnd.choice([200, 300]),
                                     urine=rnd.randint(1, 3), recorded_by=user)
        k = (3 if wdays and d.weekday() == wdays[0] and not heavy else rnd.randrange(3)) if not stay else rnd.randrange(3)
        if stay:    # 入所（毎日の記録）：前の日までは承認ずみ。今日は半分が確認待ち・下書き
            status = 'approved' if i >= 1 else rnd.choice(['approved', 'approved', 'review', 'draft'])
        else:
            status = 'approved' if i >= 2 else ('review' if i == 1 else rnd.choice(['draft', 'review']))
        r = CareRecord(facility=facility, beneficiary=b, date=d, author=user, body=bodies[k], family_message=fams[k], status=status,
                       keywords=kws[k], ai_drafted=True,
                       checks={'meal': 'yes' if k != 2 else 'no', 'water': 'yes', 'toilet': 'yes', 'bath': 'yes' if (wdays and d.weekday() == wdays[0]) else 'no',
                               'mood': 'yes' if k != 2 else 'no', 'activity': 'yes', 'pain': 'no', 'fall': 'no', 'family': 'yes' if k == 2 else 'no'})
        if b.first_name == '正子' and i <= 1 and not stay:
            r.lang, r.memo, r.body, r.family_message, r.keywords = 'vi', VI_MEMO, BODIES[0], FAMILY[0], KEYWORDS[0]
        if b.first_name == 'トメ':
            r.internal_note = '仙骨部の皮膚に赤みなし。体位変換 2 時間ごと。'
        if status == 'approved' and user is not None:
            r.approve(user)
        r.save()


def add(facility, user=None, days=14, stdout=None, scenario='care'):
    """入れる。足りないサンプルの利用者だけを足す（もう全部入っていれば何もしない）。戻り値は足した利用者の数"""
    sc = SCENARIOS[scenario]
    rows = sc['people']()
    incidents = ROKEN_INCIDENTS if scenario == 'roken' else INCIDENTS
    rnd = random.Random(20261031)
    today = datetime.date.today()
    have = {(b.last_name, b.first_name): b for b in Beneficiary.objects.filter(facility=facility, notes__startswith=MARK)}
    if all((row['ln'], row['fn']) in have for row in rows) and IncidentReport.objects.filter(facility=facility, situation=incidents[-1]['situation']).exists():
        add_forms(facility, user)
        return 0
    with transaction.atomic():
        vehicles = _vehicles(facility, sc) if facility.use_transport else []
        people, added = [], 0
        for idx, row in enumerate(rows):
            b = have.get((row['ln'], row['fn']))
            if b is None:
                b = _person(facility, row, idx, today, rnd, user)
                _days(facility, b, row, today, rnd, user, days)
                added += 1
            if vehicles:
                _ride(b, row, vehicles)
            people.append((b, row['wdays'], row['stay']))
        for a, c in sc['ride_ng']:
            if a < len(people) and c < len(people):
                people[a][0].no_ride_with.add(people[c][0])
        # ヒヤリハット・事故報告（同じ内容が無いものだけ）
        for inc in incidents:
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
            n = sum(1 for _b, w, stay in people if stay or d.weekday() in w)
            if not n:
                continue
            vals = dict(users=n, care_staff=max(2, (n + 4) // 5), nurses=1 if n <= 20 else 2, counselors=1, trainers=1 if i % 2 == 0 or n > 20 else 0, note=MARK)
            row = StaffingDay.objects.filter(facility=facility, date=d).first()
            if row is None:
                StaffingDay.objects.create(facility=facility, date=d, **vals)
            elif row.note == MARK or (row.note == '' and (row.care_staff, row.nurses, row.counselors) == (2, 1, 1)):  # 前の版のサンプル
                for k, v in vals.items():
                    setattr(row, k, v)
                row.save()
    add_forms(facility, user)
    return added


SAMPLE_DOCTORS = '一ノ瀬 Dr\n二階堂 Dr\n三上 Dr'
SAMPLE_LEADER = '見本 主任'
HANDOVERS = [('08:50', '見本 看護A', 0, '朝の検温 37.6℃。クーリングして 10 時に再検。水分を多めに。'),
             ('09:30', '見本 介護B', 1, '入浴は午後に変更（家族の希望）。'),
             ('10:15', '見本 看護A', 0, '再検 37.1℃。昼食は様子を見て全量で可。'),
             ('11:40', '見本 介護B', 2, '昼食 8 割。むせ込みなし。'),
             ('13:20', '見本 相談員C', None, '明日の送迎、ハイエースの添乗が 1 名少ない。調整中。')]


def add_forms(facility, user=None):
    """2026-11-11 の様式（申し送り・所見／処方処置・上申書・利用予定・送迎表）の見本。足りない分だけ足す"""
    from .models import DayUse, DoctorNote, HandoverDay, HandoverEntry, JoushinItem, NursingSetting
    from .views_forms import copy_medication_to_note
    today = datetime.date.today()
    rnd = random.Random(20261111)
    people = list(Beneficiary.objects.filter(facility=facility, notes__startswith=MARK).order_by('pk'))
    if not people:
        return
    with transaction.atomic():
        s = NursingSetting.of(facility)
        if not s.doctors:
            s.doctors = SAMPLE_DOCTORS
            s.save()
        doctors = s.doctor_list()
        # 薬 → 所見・処方処置（処方処置へ写す）と、所見の例
        for m in Medication.objects.filter(beneficiary__in=people):
            if not m.days:
                m.days = 14
                m.save(update_fields=['days'])
            if not DoctorNote.objects.filter(medication=m).exists():
                copy_medication_to_note(m, user)
        for i, b in enumerate(people[:4]):
            if not DoctorNote.objects.filter(beneficiary=b, finding__startswith='（見本）').exists():
                DoctorNote.objects.create(facility=facility, beneficiary=b, date=today - datetime.timedelta(days=7 - i), writer='dr', doctor=doctors[i % len(doctors)],
                                          finding='（見本）胸部聴診で異常なし。食欲あり。', treatment='経過観察', created_by=user)
                DoctorNote.objects.create(facility=facility, beneficiary=b, date=today - datetime.timedelta(days=5 - i), writer='reha',
                                          finding='（見本）平行棒内歩行 10 m を 2 往復。ふらつきなし。', created_by=user)
        # 申し送り（きょう・きのう）
        for day in (today - datetime.timedelta(days=1), today):
            if HandoverEntry.objects.filter(facility=facility, date=day, beneficiary__in=people).exists():
                continue
            for tm, giver, who, content in HANDOVERS:
                HandoverEntry.objects.create(facility=facility, date=day, time=datetime.time(*map(int, tm.split(':'))), giver=giver,
                                             beneficiary=people[who] if who is not None and who < len(people) else people[0], content=content, created_by=user)
            HandoverDay.objects.get_or_create(facility=facility, date=day, defaults={
                'leader': SAMPLE_LEADER, 'tomorrow': '午前：健康診断（3 名）\n午後：誕生日会', 'telemin': '見本 看護A', 'laxative': '見本 介護B',
                'decisions': '感染対策のため、面会は予約制を続ける。', 'rec_am': 12, 'rec_pm': 9})
        # 経過記録（きのう・きょう。職種ごと。夕方に書いても出来事の時刻で並ぶ例）
        from .models import ProgressNote
        notes = [('09:20', 'nurse', '見本 看護A', '朝の血圧 148/86。本人に自覚症状なし。昼に再検。', False, False),
                 ('10:40', 'reha', '見本 リハD', '平行棒内歩行 10 m × 2 往復。ふらつきなし。', False, False),
                 ('13:30', 'nurse', '見本 看護A', '再検 152/90。医師へ報告し、夕方にもう一度測る。', True, True),
                 ('15:10', 'care', '見本 介護B', 'おやつは全量。レクの体操に参加。', False, False),
                 ('16:00', 'sw', '見本 相談員C', '家族より、来週の通院の付き添いについて電話あり。', True, False)]
        for day in (today - datetime.timedelta(days=1), today):
            if ProgressNote.objects.filter(facility=facility, date=day, beneficiary__in=people).exists():
                continue
            for i, (tm, role, who, body, ho, imp) in enumerate(notes):
                ProgressNote.objects.create(facility=facility, beneficiary=people[i % min(3, len(people))], date=day,
                                            time=datetime.time(*map(int, tm.split(':'))), role=role, author_name=who, body=body,
                                            handover=ho, important=imp, created_by=user)
        # 薬の確認予定（処方日数から）
        for m in Medication.objects.filter(beneficiary__in=people[:3], check_on__isnull=True, is_active=True):
            m.start_on = today - datetime.timedelta(days=12)
            m.check_on = m.start_on + datetime.timedelta(days=m.days or 14)
            m.check_doctor = m.prescriber[:50]
            m.save(update_fields=['start_on', 'check_on', 'check_doctor'])
        # 上申書（きょう）
        if not JoushinItem.objects.filter(facility=facility, date=today, beneficiary__in=people).exists():
            JoushinItem.objects.create(facility=facility, date=today, kind='dr', doctor=doctors[0], beneficiary=people[0], content='37.6℃の発熱。咳なし。様子を見てよいか。', created_by=user)
            if len(doctors) > 1 and len(people) > 1:
                JoushinItem.objects.create(facility=facility, date=today, kind='dr', doctor=doctors[1], beneficiary=people[1], content='便秘 3 日目。下剤の調整を相談したい。', created_by=user)
            JoushinItem.objects.create(facility=facility, date=today, kind='exam', beneficiary=people[-1], content='定期採血（来週火曜）', oral=True, created_by=user)
        # 利用日（今月・来月）：利用の曜日から。過ぎた日は来た 9 割・休み 1 割。1 人は半日、1 人はショートの送迎
        fields = ('weekday_mon', 'weekday_tue', 'weekday_wed', 'weekday_thu', 'weekday_fri', 'weekday_sat')
        for y, mo in ((today.year, today.month), ((today.year + (today.month == 12)), today.month % 12 + 1)):
            for i, b in enumerate(people):
                if DayUse.objects.filter(beneficiary=b, date__year=y, date__month=mo).exists():
                    continue
                d = datetime.date(y, mo, 1)
                while d.month == mo:
                    if d.weekday() < 6 and getattr(b, fields[d.weekday()]):
                        att = (rnd.random() > 0.1) if d < today else None
                        DayUse.objects.create(facility=facility, beneficiary=b, date=d, planned=True, attended=att, source='weekday',
                                              half=(i == 2), short=(i == 5))
                    d += datetime.timedelta(days=1)
        # 送迎表（きょう・あした）：いつもの車両で並べる
        if facility.use_transport:
            from .views_days import auto_place
            for d in (today, today + datetime.timedelta(days=1)):
                auto_place(facility, d)


def remove(facility):
    """サンプルの利用者（とその記録・報告・送迎の設定）、サンプルの車両・運転手・人員配置だけを消す"""
    from transport.models import Driver, Vehicle
    qs = Beneficiary.objects.filter(facility=facility, notes__startswith=MARK)
    n = qs.count()
    from .models import HandoverDay, HandoverEntry, JoushinItem, TransportRun
    with transaction.atomic():
        IncidentReport.objects.filter(facility=facility, beneficiary__in=qs).delete()
        HandoverEntry.objects.filter(facility=facility, beneficiary__in=qs).delete()
        HandoverDay.objects.filter(facility=facility, leader=SAMPLE_LEADER).delete()
        JoushinItem.objects.filter(facility=facility, beneficiary__in=qs).delete()
        TransportRun.objects.filter(facility=facility, stops__beneficiary__in=qs).delete()
        qs.delete()
        StaffingDay.objects.filter(facility=facility, note=MARK).delete()
        Vehicle.objects.filter(facility=facility, note=MARK).delete()
        Driver.objects.filter(facility=facility, note=MARK).delete()
    return n

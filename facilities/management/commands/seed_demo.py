"""
デモ用サンプルデータの投入コマンド

    python manage.py seed_demo            # 施設が1つならその施設に投入
    python manage.py seed_demo --facility 1
    python manage.py seed_demo --reset    # サンプルデータを消してから入れ直す

作るもの（すべて架空。利用者の備考に「サンプルデータ」と入る）
- 活動タグ・支援内容タグ
- 利用者 6名（保護者・受給者証つき、利用曜日を設定）
- 来所予定：前月1日〜来月末（利用曜日ベース。過去分は来所／欠席／振替を振り分け）
- 日誌：直近3週間の来所日（活動・めあて・考察、観察/支援/反応の記録文、タグ、5領域）
- 請求マトリックス：前月〜今日までの実績
- スタッフメモ
"""

import datetime
import random

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import StaffAccount
from beneficiaries.models import Beneficiary, Guardian, RecipientCertificate
from billing.models import BillingMatrixEntry
from facilities.models import Facility, SupportContentTag
from records.models import ActivityTag, DailyRecord, StaffMemo
from schedules.models import ScheduledVisit
from support_plans.models import MonitoringRecord, PlanGoal, SupportPlan

SAMPLE_MARK = 'サンプルデータ'

ACTIVITY_TAGS = [
    # (名前, 実費単価)
    ('工作', 0), ('運動', 0), ('おやつ', 100), ('外出', 0),
    ('クッキング', 200), ('音楽', 0), ('学習', 0), ('自由遊び', 0),
]
SUPPORT_TAGS = ['送迎', '学習支援', '創作活動', '運動', '集団遊び', '個別対応', '感覚統合', 'SST']

# 利用者（架空）。曜日は 月火水木金土 の順
BENEFICIARIES = [
    dict(last='佐藤', first='はると', lk='さとう', fk='はると', dob=(2017, 5, 12), gender='male',
         dtype='自閉スペクトラム症', days='mon,wed,fri', cap=4600, guardians=[('佐藤', '美咲', 'mother')]),
    dict(last='鈴木', first='ゆい', lk='すずき', fk='ゆい', dob=(2016, 11, 3), gender='female',
         dtype='ADHD', days='tue,thu', cap=4600, guardians=[('鈴木', '健太', 'father'), ('鈴木', '恵', 'mother')]),
    dict(last='高橋', first='そうた', lk='たかはし', fk='そうた', dob=(2015, 2, 20), gender='male',
         dtype='知的障害（軽度）', days='mon,tue,wed,thu,fri', cap=37200, guardians=[('高橋', '由紀', 'mother')]),
    dict(last='田中', first='ひなた', lk='たなか', fk='ひなた', dob=(2018, 8, 8), gender='female',
         dtype='発達性協調運動症', days='wed,sat', cap=0, guardians=[('田中', '直樹', 'father')]),
    dict(last='伊藤', first='りく', lk='いとう', fk='りく', dob=(2014, 4, 1), gender='male',
         dtype='自閉スペクトラム症・知的障害', days='mon,thu,sat', cap=4600, guardians=[('伊藤', '沙織', 'mother')]),
    dict(last='渡辺', first='あおい', lk='わたなべ', fk='あおい', dob=(2016, 6, 30), gender='female',
         dtype='学習障害', days='tue,fri', cap=4600, guardians=[('渡辺', '真理', 'mother')]),
]

# 活動：(活動名, めあて・観点, 考察の型, 活動タグ, 支援タグ, 5領域)
ACTIVITIES = [
    ('昼食クッキング（カレー）',
     '役割分担、気を付けて調理道具をつかおう\n・順番を待って自分の役割ができるか\n・包丁・ピーラーを正しく持てるか\n・友だちに声をかけて協力できるか',
     '役割分担の場面では{name}さんが自分から「野菜を切る係をやりたい」と申し出て、最後まで担当をやり切りました。'
     'ピーラーの扱いは声かけで安全に行えており、次回は包丁の練習にも少しずつ挑戦できそうです。',
     ['クッキング', 'おやつ'], ['創作活動', '集団遊び'], ['domain_motor_sensory', 'domain_social']),
    ('かるた',
     '反射神経、動体視力\n・読み札を最後まで聞いてから動けるか\n・取れなかったときに気持ちを切り替えられるか\n・友だちの札を認められるか',
     'かるたでは{name}さんが読み札を最後まで聞いてから手を伸ばす場面が増え、お手つきが前回より減りました。'
     '取れなかったときに一度机を叩く様子がありましたが、職員の声かけで気持ちを切り替えて続けられました。',
     ['自由遊び', '学習'], ['集団遊び', 'SST'], ['domain_cognition_behavior', 'domain_social']),
    ('トンボストロー',
     '口の動かし方、息の吹きかけ練習\n・ストローをくわえて息を続けて出せるか\n・的に向かって息の向きを調整できるか\n・できたときに喜びを表現できるか',
     'トンボストローでは{name}さんが息を細く長く出すコツをつかみ、トンボを2m先まで飛ばせました。'
     '口の形を意識する練習として有効で、発語の明瞭さにもつながる活動として継続したいと考えます。',
     ['工作', '運動'], ['感覚統合', '個別対応'], ['domain_motor_sensory', 'domain_language_comm']),
    ('公園でボール遊び',
     '体を動かして友だちと関わる\n・ボールを相手に向かって投げられるか\n・順番やルールを守れるか\n・疲れたときに自分で休憩を選べるか',
     '公園では{name}さんが友だちの名前を呼んでからボールを投げるなど、相手を意識した関わりが見られました。'
     '後半は疲れて座り込む場面がありましたが、自分から「休む」と伝えられたのは大きな変化です。',
     ['外出', '運動'], ['運動', '集団遊び'], ['domain_health_life', 'domain_motor_sensory', 'domain_social']),
    ('リズム遊び（太鼓）',
     '音に合わせて体を動かす\n・リズムに合わせて叩けるか\n・大きい音・小さい音を出し分けられるか\n・順番を待てるか',
     'リズム遊びでは{name}さんが職員の見本をよく見て、速さの変化にも合わせて叩けていました。'
     '音が大きい場面で耳をふさぐ様子があったため、次回は音量に配慮しながら参加を促します。',
     ['音楽', '自由遊び'], ['感覚統合', '集団遊び'], ['domain_motor_sensory', 'domain_cognition_behavior']),
    ('宿題タイム＋折り紙',
     '集中して取り組む時間をつくる\n・15分間、席について課題に向かえるか\n・わからないときに助けを求められるか\n・完成まで手順を追えるか',
     '宿題では{name}さんが自分から「ここがわからない」と職員に伝えられ、以前より援助要求がスムーズになりました。'
     '折り紙は手順表を見ながら最後まで完成させ、達成感を持って終えられました。',
     ['学習', '工作'], ['学習支援', '個別対応'], ['domain_cognition_behavior', 'domain_language_comm']),
]

OBSERVATIONS = [
    '{act}に取り組みました。最初は様子を見ていましたが、職員の声かけで参加し、後半は自分から手を挙げる場面もありました。',
    '来所後すぐに{act}の準備を手伝ってくれました。活動中は友だちの動きをよく見ていて、真似をしながら進めていました。',
    '{act}では途中で集中が途切れる時間がありましたが、休憩をはさむと再び取り組めました。',
]
SUPPORTS = [
    '手順を絵カードで示し、1つずつ確認しながら進めました。難しい場面では職員が隣で一緒に行いました。',
    '見通しが持てるよう、活動の前に流れを説明しました。順番を待つ場面では声かけとタイマーを使いました。',
    '本人のペースを尊重し、できたところを具体的に言葉にして褒めました。',
]
REACTIONS = [
    '完成したときに「できた！」と笑顔で職員に見せに来ました。',
    '友だちに「かして」と言えたことを本人も嬉しそうにしていました。',
    '最後の片付けまで自分から取り組み、落ち着いて帰りの準備ができました。',
]
PARENT_MESSAGES = [
    '今日は{act}に取り組みました。{name}さんは最後まで集中して参加でき、完成したときにはとても嬉しそうな表情を見せてくれました。',
    '本日は{act}をしました。友だちとやり取りしながら進める場面が増えています。ご家庭でもぜひ今日の話を聞いてみてください。',
]
MEMOS = [
    '来週の工作の材料（画用紙・のり）を発注する',
    '佐藤はるとさんの保護者から、送迎時間を10分早めたいとの相談あり',
    '避難訓練の日程を月末までに決める',
    '高橋そうたさんの受給者証、更新時期が近いので確認',
]


class Command(BaseCommand):
    help = 'デモ用のサンプルデータ（利用者・予定・日誌など）を投入する'

    def add_arguments(self, parser):
        parser.add_argument('--facility', type=int, help='投入先の施設ID（省略時は施設が1つならそれを使う）')
        parser.add_argument('--create-facility', metavar='施設名', help='施設が無ければこの名前で作り、所属施設が未設定の職員（管理者を含む）をその施設に所属させる')
        parser.add_argument('--reset', action='store_true', help='既存のサンプルデータを削除してから投入する')
        parser.add_argument('--seed', type=int, default=20260912, help='乱数のシード（同じ値なら同じデータ）')

    def handle(self, *args, **options):
        facility = self._pick_facility(options.get('facility'), options.get('create_facility'))
        rng = random.Random(options['seed'])

        existing = Beneficiary.objects.filter(facility=facility, notes__contains=SAMPLE_MARK)
        if existing.exists():
            if not options['reset']:
                raise CommandError(
                    f'施設「{facility.name}」にはサンプル利用者が {existing.count()} 名すでにいます。'
                    '入れ直す場合は --reset を付けてください。'
                )
            self._reset(facility)

        with transaction.atomic():
            counts = self._create(facility, rng)

        self.stdout.write(self.style.SUCCESS(f'施設「{facility.name}」にサンプルデータを投入しました'))
        for label, n in counts.items():
            self.stdout.write(f'  {label}: {n}')
        self.stdout.write('削除するときは: python manage.py seed_demo --reset  のあと必要なら再投入、'
                          'または管理画面で備考「サンプルデータ」の利用者を削除')

    # ------------------------------------------------------------------
    def _pick_facility(self, facility_id, create_name=None):
        if create_name and not Facility.objects.exists():
            facility = Facility.objects.create(name=create_name)
            from accounts.models import StaffAccount
            n = StaffAccount.objects.filter(facility__isnull=True).update(facility=facility)
            self.stdout.write(f'施設「{facility.name}」を作成し、所属未設定の職員 {n} 名を所属させました。')
            return facility
        if facility_id:
            try:
                return Facility.objects.get(pk=facility_id)
            except Facility.DoesNotExist:
                raise CommandError(f'施設ID {facility_id} は存在しません')
        qs = Facility.objects.order_by('pk')
        if qs.count() == 1:
            return qs.first()
        if not qs.exists():
            raise CommandError('施設がありません。--create-facility "施設名" を付けて作成するか、先に管理画面で施設を作成してください。')
        names = ', '.join(f'{f.pk}:{f.name}' for f in qs)
        raise CommandError(f'施設が複数あります。--facility でIDを指定してください（{names}）')

    def _reset(self, facility):
        bens = Beneficiary.objects.filter(facility=facility, notes__contains=SAMPLE_MARK)
        n = bens.count()
        # 支援計画は PROTECT なので先に消す
        SupportPlan.objects.filter(beneficiary__in=bens).delete()
        # 予定・日誌・請求・保護者・受給者証は利用者の削除でカスケードされる
        bens.delete()
        StaffMemo.objects.filter(facility=facility, content__in=MEMOS).delete()
        self.stdout.write(f'サンプル利用者 {n} 名と関連データを削除しました')

    # ------------------------------------------------------------------
    def _create(self, facility, rng):
        today = datetime.date.today()
        counts = {}

        # --- 施設情報の空欄だけ埋める（入力済みの値は触らない） ---
        changed = False
        if not facility.office_number:
            facility.office_number = '2650000000'; changed = True
        if not facility.phone:
            facility.phone = '075-000-0000'; changed = True
        if not facility.address:
            facility.address = '京都市伏見区深草○○町1-1'; changed = True
        if not facility.standard_close_time:
            facility.standard_close_time = datetime.time(17, 0); changed = True
        if not facility.base_unit_count:
            facility.base_unit_count = 604; changed = True
        if changed:
            facility.save()

        # --- タグ ---
        activity_tags = {}
        for order, (name, price) in enumerate(ACTIVITY_TAGS):
            tag, _ = ActivityTag.objects.get_or_create(
                facility=facility, name=name, defaults={'display_order': order, 'price': price},
            )
            activity_tags[name] = tag
        support_tags = {}
        for order, name in enumerate(SUPPORT_TAGS):
            tag, _ = SupportContentTag.objects.get_or_create(
                facility=facility, name=name, defaults={'order': order},
            )
            support_tags[name] = tag
        counts['活動タグ'] = len(activity_tags)
        counts['支援内容タグ'] = len(support_tags)

        # --- 利用者・保護者・受給者証 ---
        fy_start = datetime.date(today.year if today.month >= 4 else today.year - 1, 4, 1)
        fy_end = datetime.date(fy_start.year + 1, 3, 31)
        beneficiaries = []
        for i, b in enumerate(BENEFICIARIES):
            days = b['days'].split(',')
            ben = Beneficiary.objects.create(
                facility=facility,
                last_name=b['last'], first_name=b['first'],
                last_name_kana=b['lk'], first_name_kana=b['fk'],
                date_of_birth=datetime.date(*b['dob']), gender=b['gender'],
                disability_class='2' if b['cap'] else '', disability_type=b['dtype'],
                notes=f'{SAMPLE_MARK}（seed_demo で作成）',
                weekday_mon='mon' in days, weekday_tue='tue' in days, weekday_wed='wed' in days,
                weekday_thu='thu' in days, weekday_fri='fri' in days, weekday_sat='sat' in days,
            )
            for j, (gl, gf, rel) in enumerate(b['guardians']):
                Guardian.objects.create(
                    beneficiary=ben, last_name=gl, first_name=gf, relation=rel,
                    phone=f'090-0000-{1000 + i * 10 + j:04d}', is_primary=(j == 0),
                )
            RecipientCertificate.objects.create(
                beneficiary=ben, certificate_number=f'26{100000 + i * 137:06d}',
                granted_days=23, monthly_cap=b['cap'],
                valid_from=fy_start, valid_until=fy_end,
                municipality='京都市', support_office='相談支援事業所 サンプル',
            )
            beneficiaries.append(ben)
        counts['利用者'] = len(beneficiaries)
        counts['保護者'] = Guardian.objects.filter(beneficiary__in=beneficiaries).count()

        # --- 来所予定：前月1日〜来月末 ---
        first_prev = (today.replace(day=1) - datetime.timedelta(days=1)).replace(day=1)
        next_month = (today.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
        end_next = (next_month.replace(day=28) + datetime.timedelta(days=4)).replace(day=1) - datetime.timedelta(days=1)
        visits = []
        d = first_prev
        while d <= end_next:
            for ben in beneficiaries:
                if d.weekday() in ben.scheduled_weekday_numbers:
                    if d < today:
                        r = rng.random()
                        status = ('attended' if r < 0.85 else 'absent' if r < 0.95 else 'transferred')
                    else:
                        status = 'scheduled'
                    visits.append(ScheduledVisit(
                        facility=facility, beneficiary=ben, date=d, status=status,
                        has_pickup=rng.random() < 0.7, has_dropoff=rng.random() < 0.7,
                    ))
            d += datetime.timedelta(days=1)
        ScheduledVisit.objects.bulk_create(visits)
        counts['来所予定'] = len(visits)

        # --- 請求マトリックス：前月〜昨日の実績 ---
        entries = [
            BillingMatrixEntry(facility=facility, beneficiary=v.beneficiary, date=v.date,
                               status=v.status, is_finalized=(v.date < today.replace(day=1)))
            for v in visits if v.status != 'scheduled'
        ]
        BillingMatrixEntry.objects.bulk_create(entries)
        counts['請求マトリックス'] = len(entries)

        # --- 日誌：直近3週間の来所日 ---
        staff = (StaffAccount.objects.filter(facility=facility).order_by('pk').first()
                 or StaffAccount.objects.order_by('pk').first())
        since = today - datetime.timedelta(days=21)
        n_records = 0
        for v in visits:
            if v.status != 'attended' or not (since <= v.date < today):
                continue
            act_name, aim, reflection, atags, stags, domains = rng.choice(ACTIVITIES)
            name = v.beneficiary.first_name
            # 「めあて」1行目と「・観点」の行に分け、観点には はい／いいえ を付ける
            aim_lines = [ln.strip() for ln in aim.split('\n') if ln.strip()]
            aim_line = next((ln for ln in aim_lines if not ln.startswith('・')), '')
            viewpoints = [{'text': ln.lstrip('・'), 'answer': rng.choice(['yes', 'yes', 'yes', 'no'])}
                          for ln in aim_lines if ln.startswith('・')]
            entry = datetime.time(rng.choice([13, 14, 15]), rng.choice([0, 15, 30, 45]))
            exit_ = datetime.time(rng.choice([17, 18]), rng.choice([0, 15, 30]))
            rec = DailyRecord.objects.create(
                facility=facility, beneficiary=v.beneficiary, date=v.date, author=staff,
                entry_time=entry, exit_time=exit_,
                health_condition=rng.choice(['good', 'good', 'good', 'normal']),
                activity_name=act_name, activity_aim=aim_line,
                activity_viewpoints=viewpoints,
                activity_reflection=reflection.format(name=name),
                observation_memo=f'{act_name}。声かけで参加、後半は自分から。',
                observation_text=rng.choice(OBSERVATIONS).format(act=act_name),
                support_text=rng.choice(SUPPORTS),
                reaction_text=rng.choice(REACTIONS),
                parent_message_draft=rng.choice(PARENT_MESSAGES).format(act=act_name, name=name),
                status='draft' if (today - v.date).days <= 2 else 'confirmed',
                **{dom: True for dom in domains},
            )
            rec.activity_tags.set([activity_tags[t] for t in atags])
            rec.support_tags.set([support_tags[t] for t in stags] + ([support_tags['送迎']] if v.has_pickup else []))
            n_records += 1
        counts['日誌'] = n_records

        # --- 個別支援計画：進み具合の違う計画を用意 ---
        counts['個別支援計画'] = self._create_plans(facility, beneficiaries, staff, today)

        # --- スタッフメモ ---
        for content in MEMOS:
            StaffMemo.objects.get_or_create(facility=facility, content=content, defaults={'author': staff})
        counts['スタッフメモ'] = len(MEMOS)

        # --- 事業所様式（はぴねす様式の施設だけ）---
        if facility.form_set == Facility.FORM_SET_HAPPINESS:
            counts.update(self._create_custom_forms(facility, beneficiaries, staff, today))

        return counts

    # ------------------------------------------------------------------
    def _create_custom_forms(self, facility, beneficiaries, staff, today):
        """はぴねす様式のサンプル：関係機関連携加算Ⅱ報告書・専門的支援実施計画書・計画書の追加項目"""
        from custom_forms.models import AgencyMeetingReport, SpecializedSupportPlan
        D = datetime.timedelta
        counts = {}

        meetings = [
            (beneficiaries[0], today - D(days=25), '明日香養護学校', 'face',
             [('明日香養護学校', '担任 田中'), ('明日香養護学校', '進路担当 鈴木'), (facility.name, str(staff))],
             '進級後の学校での様子と、放課後の支援方針の共有',
             '学校では朝の会の見通しカードが定着し、自分から準備に取りかかれる日が増えている。放課後は疲れが出やすく、活動の後半に離席が見られる。',
             '家庭・学校・事業所で同じ声かけ（「終わったら○○」）を使うと落ち着きやすい。',
             '活動の前に予定を絵カードで確認し、後半は短い休憩を入れる。学校と月1回情報共有を続ける。'),
            (beneficiaries[1], today - D(days=60), '相談支援事業所 サンプル（オンライン）', 'online',
             [('相談支援事業所 サンプル', '相談支援専門員 高橋'), (facility.name, str(staff))],
             'モニタリングに向けた現状共有',
             '「手伝って」と自分から言える場面が増えた。家庭では宿題への取りかかりに時間がかかる。',
             '成功体験を家庭にも伝え、保護者の負担感を減らす工夫を。',
             '連絡帳でその日のできたことを具体的に伝える。宿題は事業所で最初の10分だけ一緒に取り組む。'),
        ]
        for ben, when, place, fmt, people, purpose, result, opinions, policy in meetings:
            AgencyMeetingReport.objects.create(
                facility=facility, beneficiary=ben, date=when,
                start_time=datetime.time(9, 30), end_time=datetime.time(10, 30), place=place, format=fmt,
                participants=[{'affiliation': a, 'name': n} for a, n in people],
                purpose=purpose, result=result, opinions=opinions, policy=policy, recorder=staff,
            )
        counts['関係機関連携加算Ⅱ 報告書'] = len(meetings)

        SpecializedSupportPlan.objects.create(
            facility=facility, beneficiary=beneficiaries[0],
            period_start=today - D(days=90), period_end=today + D(days=92),
            wishes='本人：走るのが速くなりたい。\n家族：転びにくくなってほしい。階段を手すりなしで上れるようになってほしい。',
            rom_parts=['足'], pain_site='', weak_parts=['体幹', '下肢'], balance='yes', cardio='', muscle_tone='low',
            other_physical='扁平足（インソール使用）',
            move_rolling='independent', move_sitting='independent', move_getting_up='independent',
            move_standing='independent', move_stand_up='partial', other_movement='',
            abms={'neck': '3', 'sitting': '3', 'floor': '3', 'standing': '2', 'walking': '2'},
            abms_t={'oral': '3', 'hand': '2', 'one_leg': '1', 'both_legs': '2', 'stairs': '1'},
            key_areas='体幹の安定・バランス', goals='片足立ちを5秒保持できる。手すりなしで階段を上れる。',
            support_items=['筋力強化訓練', 'バランス訓練', '立位訓練'], support_other='',
            implementation='バランスボードやトランポリンを使った遊びの中で体幹を使う。階段昇降は手すりを軽く添える程度から段階的に減らす。週2回、各20分。',
            explained_date=today - D(days=85), explained_to='本人、家族（母）', explained_by=staff,
        )
        SpecializedSupportPlan.objects.create(
            facility=facility, beneficiary=beneficiaries[2],
            period_start=today - D(days=30), period_end=today + D(days=152),
            wishes='本人：はさみを上手に使いたい。\n家族：字をていねいに書けるようになってほしい。',
            rom_parts=[], pain_site='', weak_parts=['上肢'], balance='no', cardio='', muscle_tone='',
            other_physical='',
            move_rolling='independent', move_sitting='independent', move_getting_up='independent',
            move_standing='independent', move_stand_up='independent', other_movement='',
            abms={'neck': '3', 'sitting': '3', 'floor': '3', 'standing': '3', 'walking': '3'},
            abms_t={'oral': '3', 'hand': '1', 'one_leg': '2', 'both_legs': '3', 'stairs': '3'},
            key_areas='手先の巧緻性', goals='線に沿ってはさみで切れる。鉛筆を三点持ちで持てる。',
            support_items=['筋力強化訓練', '座位訓練'], support_other='手先の運動遊び',
            implementation='粘土・洗濯ばさみ・ビーズ通しなどの遊びで指先の力をつける。書字は太い鉛筆と補助具から始める。週2回、各15分。',
            explained_date=today - D(days=28), explained_to='本人、家族（父）', explained_by=staff,
        )
        counts['専門的支援実施計画書'] = 2

        # 個別支援計画書（詳細版・別紙1）の追加項目：目標のある計画に入れる
        n = 0
        for plan in SupportPlan.objects.filter(facility=facility).order_by('pk'):
            if not plan.goals.exists():
                continue
            days = ['月', '水', '金']
            plan.form_extra = {
                'usage_form': f'放課後等デイサービス（{"・".join(days)}曜日 週{len(days)}回）',
                'specialists': '理学療法士、保育士',
                'child_wishes': '友だちと一緒に遊びたい。工作をたくさんやりたい。',
                'guardian_wishes': '気持ちを言葉で伝えられるようになってほしい。集団の場に慣れてほしい。',
                'service_hours': '月・水・金 15:00〜17:30（学校休業日 10:00〜16:00）',
                'medical_care': '・主治医連携：年1回受診に同行し、所見を共有\n・医療的ケア等：なし\n・関係機関連携：学校と月1回、相談支援事業所と随時',
                'review_cycle': '原則6ヶ月毎（必要時随時）',
                'explain_method': '面談',
                'relation': '母',
                'delivery_method': '面談時手渡し',
            }
            plan.save(update_fields=['form_extra'])
            cats = ['self_social', 'self_language', 'self_cognition']
            for i, g in enumerate(plan.goals.order_by('goal_type', 'order', 'pk')):
                g.form_extra = {
                    'category': cats[i % len(cats)] if g.goal_type == 'short' else '',
                    'item': '人間関係・社会性' if i % 2 == 0 else '言語・コミュニケーション',
                    'timing': '3ヶ月後',
                    'procedure': '絵カードで見通しを示し、できたらその場で具体的にほめる。刺激の少ない場所を用意する。',
                    'staff': '保育士・児童指導員',
                    'notes': '本人の役割：自分の順番が来たら合図に気づいて動く。',
                    'criteria': '週の半分以上で自分からできれば達成',
                }
                g.save(update_fields=['form_extra'])
            n += 1
        counts['計画書の様式追加項目'] = n
        return counts

    # ------------------------------------------------------------------
    def _create_plans(self, facility, beneficiaries, staff, today):
        """ステップ1〜5のそれぞれの状態にある計画を作る（動きを確認しやすくする）"""
        D = datetime.timedelta

        def new_plan(ben, title='第1期 個別支援計画'):
            p = SupportPlan.objects.create(facility=facility, beneficiary=ben, title=title,
                                           manager=staff, created_by=staff)
            for n, _ in SupportPlan.STEPS:
                p.get_step(n)
            return p

        def fill_assessment(p, ben, when):
            a = p.get_step(1)
            a.interview_date = when
            a.interviewed_with = '本人・母'
            a.interviewer = staff
            a.condition = (f'{ben.disability_type}。好きな活動には集中して取り組めるが、予定の変更や大人数の場面で不安が強くなる。'
                           '言葉での説明より視覚的な手がかりがあると理解しやすい。')
            a.environment = f'小学{max(1, min(6, today.year - ben.date_of_birth.year - 6))}年生。放課後は週{sum([ben.weekday_mon, ben.weekday_tue, ben.weekday_wed, ben.weekday_thu, ben.weekday_fri, ben.weekday_sat])}回利用。家庭では母が主に対応。学校では通級を利用。'
            a.wishes = '本人：友だちと一緒に遊びたい、工作をたくさんやりたい。\n家族：気持ちを言葉で伝えられるようになってほしい。集団の場に慣れてほしい。'
            a.save()

        def fill_draft(p, start):
            d = p.get_step(2)
            d.period_start = start
            d.period_end = start + D(days=182)
            d.family_wishes = '友だちと関わりながら、自分の気持ちを言葉で伝えられるようになる。'
            d.policy = '本人の得意な活動（工作・クッキング）を軸に成功体験を積み、少人数から集団活動へ段階的に広げる。視覚支援と事前予告で見通しを持てるようにする。'
            d.save()
            PlanGoal.objects.create(plan=p, goal_type='long', content='友だちと協力して活動に参加できる',
                                    target_date=start + D(days=182), order=0)
            PlanGoal.objects.create(plan=p, goal_type='short', content='活動の順番を待って、自分の役割をやり切る',
                                    target_date=start + D(days=91), order=0,
                                    support_content='役割を絵カードで提示し、順番待ちの間は職員が横について声かけする。できたら具体的にほめる。',
                                    frequency='週2回・活動の前半')
            PlanGoal.objects.create(plan=p, goal_type='short', content='困ったときに「手伝って」と職員に伝えられる',
                                    target_date=start + D(days=91), order=1,
                                    support_content='困っている様子が見えたら先回りせず「どうしたい？」と聞き、言えたら必ず応じる。',
                                    frequency='毎回')

        def fill_meeting(p, when):
            m = p.get_step(3)
            m.meeting_date = when
            m.attendees = '児発管、担当職員2名、相談支援専門員'
            m.beneficiary_attended = True
            m.guardian_attended = True
            m.opinions = '短期目標は本人の負担にならない範囲で良い。家庭でも「手伝って」を練習してもらう。'
            m.revised = True
            m.revision_summary = '短期目標2の支援内容に家庭との連携を追記。'
            m.save()

        def fill_consent(p, when, start):
            c = p.get_step(4)
            c.explained_date = when
            c.explained_to = '本人・母'
            c.explained_by = staff
            c.consent_method = 'paper'
            c.consent_date = when
            c.consent_signer = f'{p.beneficiary.last_name} {p.beneficiary.guardians.first().first_name}'
            c.delivered_to_user_date = when
            c.consultation_office = '相談支援事業所 サンプル'
            c.delivered_to_office_date = when + D(days=2)
            c.service_start_date = start
            c.save()

        n = 0
        # 1人目：実施中（モニタリング1回済み、次回は先）
        p = new_plan(beneficiaries[0]); n += 1
        start = today - D(days=120)
        fill_assessment(p, beneficiaries[0], start - D(days=20)); p.complete_step(1, staff)
        fill_draft(p, start); p.complete_step(2, staff)
        fill_meeting(p, start - D(days=10)); p.complete_step(3, staff)
        fill_consent(p, start - D(days=5), start); p.complete_step(4, staff)
        MonitoringRecord.objects.create(
            plan=p, date=today - D(days=30), interviewed_with='本人・母', conducted_by=staff,
            implementation='絵カードでの役割提示は毎回実施。順番待ちの声かけは職員により差があるため統一する。',
            achievement='progressing', achievement_detail='「手伝って」は週に1〜2回言えるようになった。',
            next_due=today + D(days=60),
        )
        # 2人目：実施中だがモニタリング期限超過
        p = new_plan(beneficiaries[1]); n += 1
        start = today - D(days=150)
        fill_assessment(p, beneficiaries[1], start - D(days=20)); p.complete_step(1, staff)
        fill_draft(p, start); p.complete_step(2, staff)
        fill_meeting(p, start - D(days=10)); p.complete_step(3, staff)
        fill_consent(p, start - D(days=5), start); p.complete_step(4, staff)
        # 3人目：ステップ4（説明・同意待ち）
        p = new_plan(beneficiaries[2]); n += 1
        start = today + D(days=7)
        fill_assessment(p, beneficiaries[2], today - D(days=21)); p.complete_step(1, staff)
        fill_draft(p, start); p.complete_step(2, staff)
        fill_meeting(p, today - D(days=3)); p.complete_step(3, staff)
        c = p.get_step(4); c.explained_date = today - D(days=1); c.explained_to = '母'; c.explained_by = staff; c.save()
        # 4人目：ステップ2（目標を入力中）
        p = new_plan(beneficiaries[3]); n += 1
        fill_assessment(p, beneficiaries[3], today - D(days=7)); p.complete_step(1, staff)
        d = p.get_step(2); d.period_start = today + D(days=14); d.period_end = today + D(days=196)
        d.policy = '運動遊びを通して体の使い方に自信を持てるようにする。'; d.save()
        PlanGoal.objects.create(plan=p, goal_type='long', content='苦手な運動にも自分から挑戦できる', target_date=today + D(days=196))
        # 5人目：ステップ1（面談の途中）
        p = new_plan(beneficiaries[4]); n += 1
        a = p.get_step(1); a.interview_date = today - D(days=2); a.interviewed_with = '本人・母'; a.interviewer = staff
        a.condition = '（面談メモ）好きなこと：電車、パズル。苦手：大きな音。'; a.save()
        # 6人目：計画なし（一覧の「計画がまだ無い利用者」に出る）
        return n

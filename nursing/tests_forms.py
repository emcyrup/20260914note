"""2026-11-11 やすらぎの森の様式：申し送り・所見／処方処置・上申書・温度板・処方箋・介護記録（時間順）・提供票 → 利用日（A・B・C）。名前はすべて架空"""
import datetime
import json
from decimal import Decimal
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from accounts.models import StaffAccount
from beneficiaries.models import Beneficiary
from facilities.models import Facility
from kaigo.models import CareInsurance
from transport.models import TransportProfile, Vehicle

from .models import (CareRecord, DayUse, DoctorNote, HandoverDay, HandoverEntry, JoushinItem, Medication, NursingSetting, StaffingDay,
                     TransportRun, TransportStop, VitalsExtra, VitalsLog, week_start)
from .tests import CareBase
from .views_days import auto_place, match_person, parse_teikyo


class HandoverTests(CareBase):
    def test_group_by_giver_in_time_order_and_print(self):
        url = reverse('nursing:handover')
        d = '2026-11-11'
        for t, g, c in (('10:30', '青木', '入浴の予定を変更'), ('09:00', '石田', '朝の血圧が高め'), ('09:40', '青木', '昼食は全量'),
                        ('11:00', '石田', '家族から電話')):
            self.client.post(url, {'d': d, 'action': 'add', 'time': t, 'giver': g, 'b': self.b.pk, 'content': c})
        self.assertEqual(HandoverEntry.objects.filter(facility=self.f).count(), 4)
        self.client.post(url, {'d': d, 'action': 'day', 'leader': '上田', 'tomorrow': '健診', 'telemin': '石田', 'laxative': '', 'decisions': '感染対策',
                               'rec_am': '8', 'rec_pm': '6'})
        hd = HandoverDay.objects.get(facility=self.f, date=datetime.date(2026, 11, 11))
        self.assertEqual((hd.leader, hd.rec_am, hd.rec_pm), ('上田', 8, 6))
        html = self.client.get(reverse('nursing:handover_print') + f'?d={d}&fmt=html').content.decode()
        # 申し送り者ごと（最初に申し送った時刻の順：石田 09:00 → 青木 09:40）・中は時間順
        order = [html.find(x) for x in ('朝の血圧が高め', '家族から電話', '昼食は全量', '入浴の予定を変更')]
        self.assertEqual(order, sorted(order))
        self.assertIn('リーダー', html)
        self.assertIn('上田', html)
        self.assertIn('本日のレク人数', html)
        e = HandoverEntry.objects.first()
        self.client.post(url, {'d': d, 'action': 'delete', 'entry': e.pk})
        self.assertEqual(HandoverEntry.objects.count(), 3)

    def test_other_facility_cannot_delete(self):
        other = Facility.objects.create(name='別の施設')
        e = HandoverEntry.objects.create(facility=other, date=datetime.date(2026, 11, 11), time=datetime.time(9), giver='他', content='x')
        self.client.post(reverse('nursing:handover'), {'d': '2026-11-11', 'action': 'delete', 'entry': e.pk})
        self.assertTrue(HandoverEntry.objects.filter(pk=e.pk).exists())


class DoctorNoteTests(CareBase):
    def test_medication_copied_to_treatment_with_doctor(self):
        url = reverse('nursing:person_meds', args=[self.b.pk])
        self.client.post(url, {'name': 'テスト錠A', 'dose': '3 錠', 'timings': ['morning', 'noon', 'evening'], 'days': '7', 'route': 'oral',
                               'prescriber': '架空 Dr', 'start_on': '2026-11-11', 'is_active': '1'})
        m = Medication.objects.get(beneficiary=self.b)
        self.assertEqual((m.days, m.route), (7, 'oral'))
        note = DoctorNote.objects.get(beneficiary=self.b)
        self.assertEqual((note.date, note.doctor, note.medication_id), (datetime.date(2026, 11, 11), '架空 Dr', m.pk))
        self.assertIn('テスト錠A', note.treatment)
        self.assertIn('7日分', note.treatment)
        # 量を変えると同じ日の行を書き直す（増やさない）。中身が同じ保存では写さない
        self.client.post(url, {'med': m.pk, 'name': 'テスト錠A', 'dose': '2 錠', 'timings': ['morning'], 'days': '7', 'route': 'oral',
                               'prescriber': '架空 Dr', 'start_on': '2026-11-11', 'is_active': '1'})
        self.assertEqual(DoctorNote.objects.count(), 1)
        self.assertIn('2 錠', DoctorNote.objects.get().treatment)
        self.client.post(url, {'med': m.pk, 'name': 'テスト錠A', 'dose': '2 錠', 'timings': ['morning'], 'days': '7', 'route': 'oral',
                               'prescriber': '架空 Dr', 'start_on': '2026-11-11', 'is_active': '1', 'notes': '食後'})
        self.assertEqual(DoctorNote.objects.count(), 1)

    def test_notes_page_and_print_colors(self):
        url = reverse('nursing:person_notes', args=[self.b.pk])
        self.client.post(url, {'date': '2026-11-10', 'writer': 'reha', 'finding': '歩行が安定', 'treatment': ''})
        self.client.post(url, {'date': '2026-11-09', 'writer': 'dr', 'doctor': '架空 Dr', 'finding': '咳あり', 'treatment': '経過観察'})
        self.assertEqual(self.client.post(url, {'date': '2026-11-09', 'writer': 'dr'}).status_code, 302)   # 空は保存しない
        self.assertEqual(DoctorNote.objects.count(), 2)
        self.assertContains(self.client.get(url), '所見・処方処置')
        html = self.client.get(reverse('nursing:person_notes_print', args=[self.b.pk]) + '?fmt=html').content.decode()
        self.assertLess(html.find('咳あり'), html.find('歩行が安定'))   # 月日の順
        self.assertIn('w-reha', html)
        self.assertIn('Dr（黒字）・リハ', html)


class JoushinTests(CareBase):
    def test_doctor_columns_exams_and_print(self):
        s = NursingSetting.of(self.f)
        s.doctors = '一ノ瀬 Dr\n二階堂 Dr'
        s.save()
        url = reverse('nursing:joushin')
        d = '2026-11-11'
        self.client.post(url, {'d': d, 'kind': 'dr', 'doctor': '二階堂 Dr', 'b': self.b.pk, 'content': '夜間の咳'})
        self.client.post(url, {'d': d, 'kind': 'exam', 'b': self.b.pk, 'content': '採血（定期）', 'referral': '1', 'oral': '1'})
        self.client.post(url, {'d': d, 'action': 'other', 'other': '家族の面会あり'})
        ex = JoushinItem.objects.get(kind='exam')
        self.assertTrue(ex.referral and ex.oral and not ex.meal_slip)
        html = self.client.get(reverse('nursing:joushin_print') + f'?d={d}&fmt=html').content.decode()
        self.assertLess(html.find('一ノ瀬 Dr'), html.find('二階堂 Dr'))
        self.assertIn('夜間の咳', html)
        self.assertIn('採血（定期）', html)
        self.assertIn('家族の面会あり', html)
        self.assertIn('紹介状', html)
        self.assertContains(self.client.get(url + f'?d={d}'), '上申書')


class PrintFormTests(CareBase):
    def test_temp_board_two_weeks(self):
        start = week_start(datetime.date(2026, 11, 9))
        VitalsLog.objects.create(facility=self.f, beneficiary=self.b, date=start, slot='am', temp=Decimal('36.6'), pulse=72, bp_high=128, bp_low=70,
                                 meal_main=10, meal_side=8, water_ml=600, urine=5, stool=1)
        VitalsLog.objects.create(facility=self.f, beneficiary=self.b, date=start, slot='pm', temp=Decimal('37.8'), pulse=96, water_ml=600)
        VitalsExtra.objects.create(facility=self.f, beneficiary=self.b, date=start + datetime.timedelta(days=1), time=datetime.time(13, 0), temp=Decimal('38.2'))
        Medication.objects.create(facility=self.f, beneficiary=self.b, name='テスト散', dose='1 包', days=14, start_on=start + datetime.timedelta(days=2))
        Medication.objects.create(facility=self.f, beneficiary=self.b, name='テスト注', route='inj', start_on=start + datetime.timedelta(days=3))
        self.client.post(reverse('nursing:person_room', args=[self.b.pk]), {'room': '201'})
        html = self.client.get(reverse('nursing:temp_board_print', args=[self.b.pk]) + f'?start={start}&fmt=html').content.decode()
        self.assertIn('<svg', html)
        self.assertIn('201', html)
        self.assertIn('128/70', html)
        self.assertIn('1200ml', html)
        self.assertIn('テスト散', html)
        self.assertIn('14日分', html)
        self.assertIn('テスト注', html)
        self.assertIn((start + datetime.timedelta(days=13)).strftime('%-m/%-d'), html)   # 2 週間
        self.assertContains(self.client.get(reverse('nursing:vitals_week', args=[self.b.pk])), '温度板（2 週間）')

    def test_prescription_selected_meds(self):
        self.f.address = '架空県架空町1-2-3'
        self.f.save()
        a = Medication.objects.create(facility=self.f, beneficiary=self.b, name='テスト錠A', dose='1 錠', days=7, prescriber='架空 Dr')
        Medication.objects.create(facility=self.f, beneficiary=self.b, name='テスト錠B')
        html = self.client.get(reverse('nursing:prescription_print', args=[self.b.pk]) + f'?ids={a.pk}&d=2026-11-11&fmt=html').content.decode()
        self.assertIn('処方箋', html)
        self.assertIn('テスト錠A', html)
        self.assertNotIn('テスト錠B', html)
        self.assertIn('7日分', html)
        self.assertIn('架空 Dr', html)
        self.assertIn('架空県架空町1-2-3', html)
        self.assertIn('令和 <span>8</span> 年', html)
        html = self.client.get(reverse('nursing:prescription_print', args=[self.b.pk]) + '?fmt=html').content.decode()
        self.assertIn('テスト錠B', html)   # 印が無ければ服用中の薬すべて

    def test_care_records_in_time_order_with_sign(self):
        d = datetime.date(2026, 11, 10)
        other = Beneficiary.objects.create(facility=self.f, last_name='架空', first_name='二郎', date_of_birth=datetime.date(1938, 1, 1))
        CareRecord.objects.create(facility=self.f, beneficiary=self.b, date=d + datetime.timedelta(days=1), time=datetime.time(9, 0), body='翌日の記録', author=self.staff)
        CareRecord.objects.create(facility=self.f, beneficiary=self.b, date=d, time=datetime.time(15, 30), body='その日の記録', author=self.staff)
        CareRecord.objects.create(facility=self.f, beneficiary=other, date=d, body='別の人')
        html = self.client.get(reverse('nursing:person_records_print', args=[self.b.pk]) + '?fmt=html').content.decode()
        self.assertLess(html.find('その日の記録'), html.find('翌日の記録'))
        self.assertIn('15:30', html)
        self.assertIn('サイン', html)
        self.assertIn('care_staff', html)
        self.assertNotIn('別の人', html)
        # 記録の画面から時刻を保存できる
        self.client.post(reverse('nursing:record_edit', args=[self.b.pk]), {'d': '2026-11-12', 'action': 'save', 'body': '時刻の確認', 'time': '14:05'})
        self.assertEqual(CareRecord.objects.get(date=datetime.date(2026, 11, 12)).time, datetime.time(14, 5))


TEIKYO_JSON = json.dumps({'wareki_year': 8, 'month': 9, 'people': [
    {'name': '山本 和子 様', 'kana': 'ヤマモト カズコ', 'care_level': '要介護2', 'rows': [
        {'service': '通所リハⅠ262', 'time': '09:30-16:00', 'basic': True, 'plan_days': [1, 2, 4, 8, 31]},
        {'service': '通所リハ処遇改善加算', 'time': '', 'basic': False, 'plan_days': [1, 2, 3, 4, 5]}]},
    {'name': '架空 三郎', 'kana': '', 'rows': [{'service': '通所リハⅠ', 'time': '9:30-12:30', 'basic': True, 'plan_days': [3]}]},
]}, ensure_ascii=False)


class TeikyoTests(CareBase):
    def test_parse_takes_basic_row_and_half_day(self):
        r = parse_teikyo('読みました\n' + TEIKYO_JSON)
        self.assertEqual((r['year'], r['month']), (2026, 9))
        self.assertEqual(r['people'][0]['days'], [1, 2, 4, 8, 31])     # 加算の行の 3・5 は入れない
        self.assertFalse(r['people'][0]['half'])
        self.assertTrue(r['people'][1]['half'])                        # 3 時間 → 半日
        self.assertEqual(parse_teikyo('nope')['people'], [])
        self.assertEqual(match_person([self.b], '山本　和子 様').pk, self.b.pk)
        self.assertEqual(match_person([self.b], '別人', 'やまもと かずこ').pk, self.b.pk)
        self.assertIsNone(match_person([self.b], '架空 三郎'))

    @mock.patch('ai_assist.quick.ask_ai', return_value=(TEIKYO_JSON, None))
    def test_read_apply_and_replace(self, _ask):
        res = self.client.post(reverse('nursing:teikyo_read'), {'file': SimpleUploadedFile('teikyo.pdf', b'%PDF-1.4 test', content_type='application/pdf')})
        data = res.json()
        self.assertEqual(res.status_code, 200)
        self.assertEqual(data['people'][0]['b'], self.b.pk)
        self.assertIsNone(data['people'][1]['b'])
        apply = reverse('nursing:teikyo_apply')
        res = self.client.post(apply, json.dumps({'year': 2026, 'month': 9, 'rows': [{'b': self.b.pk, 'days': [1, 2, 4, 8, 31], 'half': False}]}),
                               content_type='application/json')
        self.assertIn(reverse('nursing:day_month'), res.json()['url'])
        self.assertEqual(sorted(u.date.day for u in DayUse.objects.filter(beneficiary=self.b)), [1, 2, 4, 8])   # 9 月は 30 日まで
        u = DayUse.objects.get(beneficiary=self.b, date=datetime.date(2026, 9, 1))
        u.attended = True
        u.save()
        # 読み直し：提供票からの予定は置きかえ、実績の付いた日は残す
        self.client.post(apply, json.dumps({'year': 2026, 'month': 9, 'rows': [{'b': self.b.pk, 'days': [2, 9]}]}), content_type='application/json')
        self.assertEqual(sorted(u.date.day for u in DayUse.objects.filter(beneficiary=self.b)), [1, 2, 9])
        # ほかの事業所の利用者には入れない
        other_f = Facility.objects.create(name='別')
        ob = Beneficiary.objects.create(facility=other_f, last_name='他', first_name='人', date_of_birth=datetime.date(1940, 1, 1))
        self.client.post(apply, json.dumps({'year': 2026, 'month': 9, 'rows': [{'b': ob.pk, 'days': [1]}]}), content_type='application/json')
        self.assertFalse(DayUse.objects.filter(beneficiary=ob).exists())

    def test_read_without_file(self):
        self.assertEqual(self.client.post(reverse('nursing:teikyo_read')).status_code, 400)
        self.assertContains(self.client.get(reverse('nursing:teikyo')), '提供票')


class DayUseTests(CareBase):
    def test_month_toggle_and_print(self):
        toggle = reverse('nursing:day_toggle')
        post = lambda part, d='2026-11-04': self.client.post(toggle, json.dumps({'b': self.b.pk, 'd': d, 'part': part}), content_type='application/json').json()  # noqa: E731
        self.assertTrue(post('plan')['plan'])
        self.assertTrue(post('att')['att'])
        self.assertIs(post('att')['att'], False)
        self.assertIsNone(post('att')['att'])
        self.assertFalse(post('plan')['plan'])
        self.assertFalse(DayUse.objects.exists())
        self.assertTrue(post('att', '2026-11-05')['att'])      # 予定に無い日に来た
        self.assertFalse(DayUse.objects.get().planned)
        res = self.client.post(reverse('nursing:day_month'), {'y': 2026, 'm': 11, 'action': 'weekday'})
        self.assertEqual(res.status_code, 302)
        self.assertFalse(DayUse.objects.filter(source='weekday').exists())   # その月に予定（実績）がある人は作らない
        b2 = Beneficiary.objects.create(facility=self.f, last_name='架空', first_name='花子', date_of_birth=datetime.date(1941, 2, 2), weekday_fri=True)
        self.client.post(reverse('nursing:day_month'), {'y': 2026, 'm': 11, 'action': 'weekday'})
        self.assertEqual(DayUse.objects.filter(beneficiary=b2).count(), 4)   # 2026-11 の金曜は 4 日
        page = self.client.get(reverse('nursing:day_month') + '?y=2026&m=11')
        self.assertContains(page, '架空 花子')
        html = self.client.get(reverse('nursing:day_month_print') + '?y=2026&m=11&fmt=html').content.decode()
        self.assertIn('デイケアサービス利用予定・実績表', html)
        self.assertIn('合計（実績人数）', html)
        bad = Beneficiary.objects.create(facility=Facility.objects.create(name='別'), last_name='他', first_name='人', date_of_birth=datetime.date(1940, 1, 1))
        self.assertEqual(self.client.post(toggle, json.dumps({'b': bad.pk, 'd': '2026-11-04', 'part': 'plan'}), content_type='application/json').status_code, 400)

    def test_week_plan_care_and_support(self):
        d = datetime.date(2026, 11, 16)   # 月曜
        sup = Beneficiary.objects.create(facility=self.f, last_name='架空', first_name='支子', date_of_birth=datetime.date(1945, 3, 3))
        CareInsurance.objects.create(beneficiary=sup, care_level='要支援1', valid_from=datetime.date(2026, 4, 1), valid_until=datetime.date(2027, 3, 31))
        DayUse.objects.create(facility=self.f, beneficiary=self.b, date=d)
        DayUse.objects.create(facility=self.f, beneficiary=sup, date=d, half=True, short=True)
        s = NursingSetting.of(self.f)
        s.capacity = 20
        s.save()
        page = self.client.get(reverse('nursing:week_plan') + f'?d={d}')
        cols = page.context['cols']
        self.assertEqual([u.beneficiary for u in cols[0]['groups']['care']], [self.b])
        self.assertEqual([u.beneficiary for u in cols[0]['groups']['prev_half']], [sup])
        self.assertEqual(cols[0]['total'], 2)
        self.assertEqual([u.beneficiary for u in cols[0]['short']], [sup])
        self.client.post(reverse('nursing:week_plan'), {'d': d, f'note_{d}': '架空さん半日'})
        self.assertEqual(StaffingDay.objects.get(facility=self.f, date=d).note, '架空さん半日')
        html = self.client.get(reverse('nursing:week_plan_print') + f'?d={d}&fmt=html').content.decode()
        self.assertIn('週間通所予定表', html)
        self.assertIn('2/20', html)


class TransportBoardTests(CareBase):
    def setUp(self):
        super().setUp()
        self.v1 = Vehicle.objects.create(facility=self.f, name='テスト車1', capacity=1)
        self.v2 = Vehicle.objects.create(facility=self.f, name='テスト車2', capacity=6)
        self.b2 = Beneficiary.objects.create(facility=self.f, last_name='架空', first_name='花子', date_of_birth=datetime.date(1941, 2, 2))
        TransportProfile.objects.create(beneficiary=self.b, pickup=True, dropoff=True, default_vehicle=self.v1, pickup_time=datetime.time(8, 30))
        TransportProfile.objects.create(beneficiary=self.b2, pickup=True, dropoff=True, default_vehicle=self.v1, pickup_time=datetime.time(8, 10))
        self.d = datetime.date(2026, 11, 18)
        DayUse.objects.create(facility=self.f, beneficiary=self.b, date=self.d)
        DayUse.objects.create(facility=self.f, beneficiary=self.b2, date=self.d, half=True)

    def test_candidates_auto_and_capacity(self):
        url = reverse('nursing:transport_board') + f'?d={self.d}'
        state = self.client.get(url).context['state']
        runs = {r['key']: r for r in state['runs']}
        self.assertEqual({u.beneficiary for u in runs['am']['pool']}, {self.b, self.b2})
        self.assertEqual([u.beneficiary for u in runs['noon']['pool']], [self.b2])      # 半日 → 昼の送り
        self.assertEqual([u.beneficiary for u in runs['pm']['pool']], [self.b])
        self.assertEqual(auto_place(self.f, self.d), 4)   # 迎え 2・昼の送り 1・夕の送り 1
        r = TransportRun.objects.get(date=self.d, run='am')
        self.assertEqual([s.beneficiary for s in r.stops.all()], [self.b2, self.b])   # 時刻の順
        state = self.client.get(url).context['state']
        am = {r['key']: r for r in state['runs']}['am']
        lane = [l for l in am['lanes'] if l['v'] == self.v1][0]
        self.assertTrue(lane['over'])           # 定員 1 に 2 人
        self.assertEqual(am['pool'], [])
        self.assertEqual(auto_place(self.f, self.d), 0)   # 組んである便は触らない

    def test_save_json_and_print(self):
        url = reverse('nursing:transport_board')
        body = {'d': str(self.d), 'lanes': [
            {'run': 'am', 'vehicle': self.v2.pk, 'driver': '架空 運転', 'escort': '架空 添乗', 'depart': '08:00',
             'stops': [{'b': self.b.pk, 'time': '08:15'}, {'b': self.b2.pk, 'time': '08:40'}, {'b': self.b.pk, 'time': ''}]},
            {'run': 'pm', 'vehicle': self.v1.pk, 'stops': [{'b': self.b.pk, 'time': '16:05'}]},
            {'run': 'xx', 'vehicle': self.v1.pk, 'stops': [{'b': self.b.pk}]}],
            'flags': [{'b': self.b.pk, 'half': False, 'short': True}]}
        res = self.client.post(url, json.dumps(body), content_type='application/json')
        self.assertTrue(res.json()['ok'])
        am = TransportRun.objects.get(run='am')
        self.assertEqual((am.vehicle, am.driver, am.escort, am.depart), (self.v2, '架空 運転', '架空 添乗', datetime.time(8)))
        self.assertEqual([(s.beneficiary, s.time) for s in am.stops.all()], [(self.b, datetime.time(8, 15)), (self.b2, datetime.time(8, 40))])
        self.assertEqual(TransportRun.objects.count(), 2)
        self.assertTrue(DayUse.objects.get(beneficiary=self.b).short)
        html = self.client.get(reverse('nursing:transport_board_print') + f'?d={self.d}&fmt=html').content.decode()
        self.assertIn('週間送迎予定表', html)
        self.assertIn('架空 運転:架空 添乗', html)
        self.assertIn('8:15', html)
        html = self.client.get(reverse('nursing:transport_board_print') + f'?d={self.d}&span=day&fmt=html').content.decode()
        self.assertIn('16:05', html)
        # 空にする
        self.client.post(url, {'d': str(self.d), 'action': 'clear'})
        self.assertFalse(TransportRun.objects.exists())

    def test_fallback_to_weekdays_without_plans(self):
        DayUse.objects.all().delete()
        monday = datetime.date(2026, 11, 16)
        state = self.client.get(reverse('nursing:transport_board') + f'?d={monday}').context['state']
        self.assertTrue(state['fallback'])
        self.assertEqual([u.beneficiary for u in state['uses']], [self.b])   # 利用の曜日（月・水）

    def test_menu_has_new_pages(self):
        res = self.client.get(reverse('nursing:home'))
        for text in ('申し送り', '上申書', '利用予定・実績'):
            self.assertContains(res, text)


# 要介護認定の一覧 CSV（やすらぎの森からいただいた形。Shift_JIS。中身は架空）
NINTEI_HEADER = '"個人コード","氏名(漢字)","氏名(カナ)","性別","年齢","生年月日","被保険者番号","申請種別","有効期間開始","有効期間終了","要介護状態","認定区分","作成支援事業者","作成支援事業者２","作成支援事業者３"'
NINTEI_ROWS = [
    '"00000001","架空　一郎","ｶｸｳ ｲﾁﾛｳ","1:男性"," 78歳","昭和23年05月10日","0000000001","2:更新申請","令和07年12月01日","令和11年11月30日","21:要介護１","3:認定済み","","",""',
    '"00000002","見本　花代","ﾐﾎﾝ ﾊﾅﾖ","2:女性"," 99歳","大正15年11月15日","0000000002","1:新規申請","令和07年07月01日","令和10年06月30日","13:要支援２","3:認定済み","9999999999:架空居宅介護支援事業所","",""',
    '"00000003","試験　三子","ｼｹﾝ ﾐﾂｺ","2:女性"," 85歳","昭和16年01月02日","0000000003","1:新規申請","","","","1:申請中","","",""',
]


def nintei_csv(rows=None):
    body = '\r\n'.join([NINTEI_HEADER] + (NINTEI_ROWS if rows is None else rows)) + '\r\n'
    return SimpleUploadedFile('nintei.csv', body.encode('cp932'), content_type='text/csv')


class NinteiCsvTests(CareBase):
    def test_wareki_dates(self):
        from beneficiaries.importer import parse_date
        self.assertEqual(parse_date('昭和23年05月10日'), datetime.date(1948, 5, 10))
        self.assertEqual(parse_date('令和07年12月01日'), datetime.date(2025, 12, 1))
        self.assertEqual(parse_date('令和元年5月1日'), datetime.date(2019, 5, 1))
        self.assertEqual(parse_date('H31.4.30'), datetime.date(2019, 4, 30))
        self.assertEqual(parse_date('2026-11-11'), datetime.date(2026, 11, 11))

    def test_import_people_and_insurance(self):
        url = reverse('beneficiaries:import')
        res = self.client.post(url, {'file': nintei_csv()})
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, '架空 一郎')
        self.client.post(url, {'action': 'commit', 'mode': 'keep'})
        a = Beneficiary.objects.get(facility=self.f, last_name='架空', first_name='一郎')
        self.assertEqual((a.last_name_kana, a.first_name_kana, a.gender, a.date_of_birth), ('かくう', 'いちろう', 'male', datetime.date(1948, 5, 10)))
        self.assertIn('個人コード：00000001', a.notes)
        ins = CareInsurance.objects.get(beneficiary=a)
        self.assertEqual((ins.care_level, ins.insured_number, ins.valid_from, ins.valid_until),
                         ('要介護1', '0000000001', datetime.date(2025, 12, 1), datetime.date(2029, 11, 30)))
        self.assertIn('更新申請', ins.notes)
        b = Beneficiary.objects.get(facility=self.f, last_name='見本', first_name='花代')
        ins_b = CareInsurance.objects.get(beneficiary=b)
        self.assertEqual(ins_b.care_level, '要支援2')
        self.assertIn('架空居宅介護支援事業所（9999999999）', ins_b.notes)
        self.assertEqual(b.date_of_birth, datetime.date(1926, 11, 15))
        c = Beneficiary.objects.get(facility=self.f, last_name='試験', first_name='三子')
        self.assertEqual(CareInsurance.objects.get(beneficiary=c).care_level, '申請中')
        # もう一度取り込んでも、備考の個人コード・介護保険証は増えない
        self.client.post(url, {'file': nintei_csv()})
        self.client.post(url, {'action': 'commit', 'mode': 'overwrite'})
        a.refresh_from_db()
        self.assertEqual(a.notes.count('個人コード'), 1)
        self.assertEqual(CareInsurance.objects.filter(beneficiary=a).count(), 1)
        self.assertEqual(Beneficiary.objects.filter(facility=self.f, last_name='架空', first_name='一郎').count(), 1)

    def test_unknown_level_goes_to_memo(self):
        row = '"00000009","架空　九郎","ｶｸｳ ｸﾛｳ","1:男性"," 80歳","昭和21年01月01日","0000000009","1:新規申請","令和08年01月01日","令和09年12月31日","99:経過的要介護","3:認定済み","","",""'
        self.client.post(reverse('beneficiaries:import'), {'file': nintei_csv([row])})
        self.client.post(reverse('beneficiaries:import'), {'action': 'commit', 'mode': 'keep'})
        ins = CareInsurance.objects.get(beneficiary__first_name='九郎')
        self.assertEqual(ins.care_level, '')
        self.assertIn('要介護状態：経過的要介護', ins.notes)


class MeetingFollowupTests(CareBase):
    """2026-11-11 の打ち合わせ：経過記録（職種横断・出来事の時刻）・申し送りの重要／勤務帯／記録からの抽出・薬の確認予定・家族向けの文"""

    def test_timeline_orders_by_event_time_and_mixes_sources(self):
        from .models import ProgressNote
        url = reverse('nursing:person_timeline', args=[self.b.pk])
        # 夕方にまとめて書いても、出来事の時刻の順
        self.client.post(url, {'date': '2026-11-10', 'time': '15:40', 'role': 'nurse', 'author': '架空 看護', 'body': '血圧 150/90。再検予定', 'important': '1', 'handover': '1'})
        self.client.post(url, {'date': '2026-11-10', 'time': '09:10', 'role': 'reha', 'author': '架空 リハ', 'body': '平行棒 2 往復'})
        DoctorNote.objects.create(facility=self.f, beneficiary=self.b, date=datetime.date(2026, 11, 10), writer='dr', doctor='架空 Dr', finding='聴診で異常なし')
        CareRecord.objects.create(facility=self.f, beneficiary=self.b, date=datetime.date(2026, 11, 10), time=datetime.time(12, 0), body='昼食は全量')
        self.assertEqual(ProgressNote.objects.count(), 2)
        page = self.client.get(url + '?from=2026-11-10&to=2026-11-10')
        bodies = [x['body'] for x in page.context['items']]
        self.assertLess(bodies.index('平行棒 2 往復'), bodies.index('昼食は全量'))
        self.assertLess(bodies.index('昼食は全量'), bodies.index('血圧 150/90。再検予定'))
        self.assertTrue(any('聴診で異常なし' in x for x in bodies))
        only = self.client.get(url + '?from=2026-11-10&to=2026-11-10&role=reha').context['items']
        self.assertEqual([x['body'] for x in only], ['平行棒 2 往復'])
        html = self.client.get(reverse('nursing:person_timeline_print', args=[self.b.pk]) + '?from=2026-11-10&to=2026-11-10&fmt=html').content.decode()
        self.assertIn('経過記録', html)
        self.assertIn('rl-nurse', html)
        self.assertIn('★血圧 150/90', html)
        self.assertIn('架空 リハ', html)
        # 全員・1 日
        day = self.client.get(reverse('nursing:timeline_day') + '?d=2026-11-10')
        self.assertEqual(len(day.context['notes']), 2)
        self.assertEqual(len(day.context['important']), 1)

    def test_handover_important_shift_and_from_records(self):
        from .models import ProgressNote
        d = datetime.date.today()
        url = reverse('nursing:handover')
        self.client.post(url, {'d': d.isoformat(), 'action': 'add', 'time': '22:10', 'giver': '架空 夜勤', 'shift': 'night', 'content': '夜間に 2 回トイレ', 'important': ''})
        self.client.post(url, {'d': d.isoformat(), 'action': 'add', 'time': '10:00', 'giver': '架空 日勤', 'shift': 'day', 'content': '38.0℃。受診の相談', 'important': '1'})
        ProgressNote.objects.create(facility=self.f, beneficiary=self.b, date=d, time=datetime.time(14, 0), role='nurse', author_name='架空 看護',
                                    body='記録から申し送り', handover=True)
        ProgressNote.objects.create(facility=self.f, beneficiary=self.b, date=d, time=datetime.time(14, 30), role='care', author_name='架空 介護', body='出さない記録')
        page = self.client.get(url + f'?d={d}')
        texts = [e.content for g in page.context['groups'] for e in g['entries']]
        self.assertIn('記録から申し送り', texts)
        self.assertNotIn('出さない記録', texts)
        self.assertEqual([e.content for e in page.context['important']], ['38.0℃。受診の相談'])
        night = self.client.get(url + f'?d={d}&shift=night').context['groups']
        self.assertEqual([e.content for g in night for e in g['entries']], ['夜間に 2 回トイレ'])
        html = self.client.get(reverse('nursing:handover_print') + f'?d={d}&shift=day&fmt=html').content.decode()
        self.assertIn('日勤帯の申し送り', html)
        self.assertIn('★重要', html)
        self.assertIn('記録から申し送り', html)
        self.assertNotIn('夜間に 2 回トイレ', html)
        home = self.client.get(reverse('nursing:home'))
        self.assertEqual([e.content for e in home.context['important_handovers']], ['38.0℃。受診の相談'])

    def test_medication_check_date_from_days(self):
        url = reverse('nursing:person_meds', args=[self.b.pk])
        start = datetime.date.today() - datetime.timedelta(days=7)
        self.client.post(url, {'name': 'テスト錠C', 'dose': '1 錠', 'days': '7', 'start_on': start.isoformat(), 'prescriber': '架空 Dr', 'is_active': '1'})
        m = Medication.objects.get(name='テスト錠C')
        self.assertEqual((m.check_on, m.check_doctor), (datetime.date.today(), '架空 Dr'))   # 7 日分 → 7 日後に処方した医師が確認
        self.assertIn('確認', DoctorNote.objects.get(medication=m).treatment)
        self.assertEqual([x.name for x in self.client.get(reverse('nursing:home')).context['med_checks']], ['テスト錠C'])
        self.client.post(url, {'med': m.pk, 'action': 'checked'})
        m.refresh_from_db()
        self.assertIsNotNone(m.checked_at)
        self.assertEqual(self.client.get(reverse('nursing:home')).context['med_checks'], [])
        # 確認する医師を別の人に（その他の医師）
        self.client.post(url, {'med': m.pk, 'name': 'テスト錠C', 'dose': '1 錠', 'days': '14', 'start_on': start.isoformat(), 'prescriber': '架空 Dr',
                               'check_doctor': '臨時 Dr', 'is_active': '1'})
        m.refresh_from_db()
        self.assertEqual((m.check_on, m.check_doctor, m.checked_at), (start + datetime.timedelta(days=14), '臨時 Dr', None))

    def test_family_text_can_be_turned_off(self):
        s = NursingSetting.of(self.f)
        CareRecord.objects.create(facility=self.f, beneficiary=self.b, date=datetime.date(2026, 11, 10), body='本文', family_message='家族向け')
        self.client.post(reverse('nursing:settings'), {'record_chars': 250, 'family_chars': 150, 'fever_temp': '37.5', 'capacity': 0, 'use_family_shown': '1'})
        s.refresh_from_db()
        self.assertFalse(s.use_family)
        home = self.client.get(reverse('nursing:home'))
        self.assertNotContains(home, reverse('nursing:renraku'))
        form = self.client.get(reverse('nursing:record_edit', args=[self.b.pk]) + '?d=2026-11-10')
        self.assertNotContains(form, 'name="family_message"')
        self.client.post(reverse('nursing:record_edit', args=[self.b.pk]), {'d': '2026-11-10', 'action': 'save', 'body': '本文を直した'})
        self.assertEqual(CareRecord.objects.get(date=datetime.date(2026, 11, 10)).family_message, '家族向け')   # 入っている文は残す


class PersonJoushinTests(CareBase):
    """上申書を利用者ごとに：日付ごとに用紙と同じ形（医師の欄・検査・その他）でぶら下げる"""

    def test_add_group_by_date_and_print(self):
        s = NursingSetting.of(self.f)
        s.doctors = '一ノ瀬 Dr\n二階堂 Dr'
        s.save()
        url = reverse('nursing:person_joushin', args=[self.b.pk])
        self.client.post(url, {'date': '2026-11-10', 'kind': 'dr', 'doctor': '二階堂 Dr', 'content': '咳が続く'})
        self.client.post(url, {'date': '2026-11-10', 'kind': 'exam', 'content': '胸部レントゲン', 'referral': '1'})
        self.client.post(url, {'date': '2026-11-11', 'kind': 'other', 'content': '家族へ連絡ずみ'})
        self.client.post(url, {'date': '2026-11-11', 'kind': 'dr', 'doctor': '臨時 Dr', 'content': '発熱の相談'})
        days = self.client.get(url + '?from=2026-11-01&to=2026-11-30').context['days']
        self.assertEqual([d['date'] for d in days], [datetime.date(2026, 11, 11), datetime.date(2026, 11, 10)])   # 新しい順
        cols10 = {c['doctor']: [i.content for i in c['items']] for c in days[1]['columns']}
        self.assertEqual(cols10, {'一ノ瀬 Dr': [], '二階堂 Dr': ['咳が続く']})
        self.assertEqual([i.content for i in days[1]['exams']], ['胸部レントゲン'])
        self.assertIn('臨時 Dr', [c['doctor'] for c in days[0]['columns']])     # 設定に無い医師も列に
        self.assertEqual([i.content for i in days[0]['others']], ['家族へ連絡ずみ'])
        html = self.client.get(reverse('nursing:person_joushin_print', args=[self.b.pk]) + '?from=2026-11-01&to=2026-11-30&fmt=html').content.decode()
        self.assertLess(html.find('咳が続く'), html.find('発熱の相談'))   # 印刷は古い日から
        self.assertIn('【検査・採血・他科受診', html)
        # 全員の上申書（その日）にも出る。その他は【その他】の欄に名前つきで
        day = self.client.get(reverse('nursing:joushin') + '?d=2026-11-11')
        self.assertEqual([i.content for i in day.context['others']], ['家族へ連絡ずみ'])
        dhtml = self.client.get(reverse('nursing:joushin_print') + '?d=2026-11-11&fmt=html').content.decode()
        self.assertIn('山本 和子：家族へ連絡ずみ', dhtml)
        # 消す（ほかの利用者の行は消せない）
        it = JoushinItem.objects.get(content='咳が続く')
        other = Beneficiary.objects.create(facility=self.f, last_name='架空', first_name='別人', date_of_birth=datetime.date(1940, 1, 1))
        self.client.post(reverse('nursing:person_joushin', args=[other.pk]), {'action': 'delete', 'item': it.pk})
        self.assertTrue(JoushinItem.objects.filter(pk=it.pk).exists())
        self.client.post(url, {'action': 'delete', 'item': it.pk})
        self.assertFalse(JoushinItem.objects.filter(pk=it.pk).exists())
        self.assertContains(self.client.get(url), '上申書')

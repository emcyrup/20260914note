import datetime
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import StaffAccount
from beneficiaries.models import Beneficiary, Guardian
from facilities.management.commands.create_facility import apply_care_preset, apply_kyotaku_preset
from facilities.models import Facility
from kaigo.models import CareInsurance

from . import demo
from .models import STATUS_APPROVED, STATUS_DRAFT, STATUS_REVIEW, CarePlan, CareRecord, IncidentReport, Medication, StaffingDay, VitalsLog
from .staffing import judge, required_care_staff


class CareBase(TestCase):
    def setUp(self):
        self.f = Facility.objects.create(name='介護施設（仮）')
        apply_care_preset(self.f)
        self.admin = StaffAccount.objects.create_user('care_admin', password='pw12345678', facility=self.f, role=StaffAccount.ROLE_ADMIN)
        self.staff = StaffAccount.objects.create_user('care_staff', password='pw12345678', facility=self.f, role=StaffAccount.ROLE_STAFF)
        self.b = Beneficiary.objects.create(facility=self.f, last_name='山本', first_name='和子', last_name_kana='やまもと', first_name_kana='かずこ',
                                            date_of_birth=datetime.date(1940, 5, 12), weekday_mon=True, weekday_wed=True)
        CareInsurance.objects.create(beneficiary=self.b, care_level='要介護2', valid_from=datetime.date(2026, 4, 1), valid_until=datetime.date(2027, 3, 31))
        self.client.force_login(self.admin)
        self.today = datetime.date.today()


class LayoutTests(CareBase):
    def test_preset_and_flags(self):
        self.f.refresh_from_db()
        self.assertEqual(self.f.layout, Facility.LAYOUT_CARE)
        self.assertTrue(self.f.is_care and self.f.is_kaigo and not self.f.is_kyotaku)
        self.assertTrue(self.f.use_transport and self.f.use_line and not self.f.use_billing)

    def test_dashboard_redirects_to_care_home(self):
        res = self.client.get(reverse('facilities:dashboard'))
        self.assertRedirects(res, reverse('nursing:home'))

    def test_home_and_menu(self):
        res = self.client.get(reverse('nursing:home'))
        self.assertEqual(res.status_code, 200)
        for text in ('検温板', '介護記録', '連絡帳', 'ヒヤリハット・事故報告', '薬の管理', '送迎表', '人員配置', '資料・加算の検索'):
            self.assertContains(res, text)
        self.assertContains(res, '介護施設')

    def test_other_layout_cannot_open(self):
        other = Facility.objects.create(name='放デイ')
        u = StaffAccount.objects.create_user('other', password='pw12345678', facility=other, role=StaffAccount.ROLE_ADMIN)
        self.client.force_login(u)
        self.assertEqual(self.client.get(reverse('nursing:home')).status_code, 404)

    def test_kaigo_insurance_pages_work_for_care(self):
        res = self.client.get(reverse('kaigo:insurance_new', args=[self.b.pk]))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, '検温板')     # 介護施設のタブ
        res = self.client.get(reverse('beneficiaries:detail', args=[self.b.pk]))
        self.assertContains(res, '介護保険証')
        self.assertContains(res, '家族・緊急連絡先')

    def test_kyotaku_keeps_its_tabs(self):
        k = Facility.objects.create(name='居宅')
        apply_kyotaku_preset(k)
        u = StaffAccount.objects.create_user('cm', password='pw12345678', facility=k, role=StaffAccount.ROLE_ADMIN)
        b = Beneficiary.objects.create(facility=k, last_name='試', first_name='験', date_of_birth=datetime.date(1940, 1, 1))
        self.client.force_login(u)
        res = self.client.get(reverse('beneficiaries:detail', args=[b.pk]))
        self.assertContains(res, '支援経過')
        self.assertNotContains(res, '検温板')
        self.assertEqual(self.client.get(reverse('nursing:home')).status_code, 404)


class VitalsTests(CareBase):
    def test_day_save_and_alerts(self):
        d = self.today
        res = self.client.post(reverse('nursing:vitals_day'), {'d': d.isoformat(), 'slot': 'am', f'b{self.b.pk}_present': '1', f'b{self.b.pk}_temp': '37.8',
                                                                f'b{self.b.pk}_bp_high': '165', f'b{self.b.pk}_bp_low': '90', f'b{self.b.pk}_pulse': '72',
                                                                f'b{self.b.pk}_spo2': '97', f'b{self.b.pk}_meal_main': '8', f'b{self.b.pk}_meal_side': '5',
                                                                f'b{self.b.pk}_water_ml': '500', f'b{self.b.pk}_urine': '3', f'b{self.b.pk}_bath': '1', f'b{self.b.pk}_note': '咳あり'})
        self.assertEqual(res.status_code, 302)
        log = VitalsLog.objects.get(beneficiary=self.b, date=d)
        self.assertEqual(log.temp, Decimal('37.8'))
        self.assertTrue(log.fever and log.bp_alert and not log.pulse_alert and not log.spo2_alert)
        self.assertEqual(log.meal_text, '8/5')
        self.assertTrue(log.bath)
        res = self.client.get(reverse('nursing:vitals_day') + f'?d={d.isoformat()}')
        self.assertContains(res, '発熱 37.8℃')
        # 空の行は作らない・範囲外の値は捨てる
        self.client.post(reverse('nursing:vitals_day'), {'d': d.isoformat(), 'slot': 'pm', f'b{self.b.pk}_present': '1', f'b{self.b.pk}_temp': '99'})
        self.assertFalse(VitalsLog.objects.filter(beneficiary=self.b, date=d, slot='pm').exists())

    def test_week_sheet_and_print(self):
        for i in range(7):
            VitalsLog.objects.create(facility=self.f, beneficiary=self.b, date=self.today - datetime.timedelta(days=i), temp=Decimal('36.5') + Decimal(i) / 10, bp_high=120 + i, bp_low=70, pulse=70)
        res = self.client.get(reverse('nursing:vitals_week', args=[self.b.pk]))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, '<svg')
        self.assertContains(res, '体温（℃）')
        res = self.client.get(reverse('nursing:vitals_week_print', args=[self.b.pk]) + '?fmt=html')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, '検温板（1 週間）')
        res = self.client.get(reverse('nursing:vitals_week_all_print') + '?fmt=html')
        self.assertContains(res, self.b.full_name)


class RecordTests(CareBase):
    def test_draft_review_approve_flow(self):
        self.client.force_login(self.staff)
        url = reverse('nursing:record_edit', args=[self.b.pk])
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'AI で下書き')
        data = {'d': self.today.isoformat(), 'chk_meal': 'yes', 'chk_pain': 'no', 'keywords': '体操・昼食', 'memo': 'げんき', 'lang': 'vi',
                'body': '午前は体操に参加した。', 'family_message': '本日は体操に参加されました。', 'internal_note': '', 'action': 'review'}
        res = self.client.post(url, data)
        self.assertEqual(res.status_code, 302)
        r = CareRecord.objects.get(beneficiary=self.b, date=self.today)
        self.assertEqual(r.status, STATUS_REVIEW)
        self.assertEqual(r.checks['meal'], 'yes')
        self.assertEqual(r.lang, 'vi')
        self.assertEqual(r.author, self.staff)
        # 一般の職員は承認できない
        res = self.client.post(url, {'d': self.today.isoformat(), 'action': 'approve'})
        r.refresh_from_db()
        self.assertEqual(r.status, STATUS_REVIEW)
        # リーダー・管理者が承認
        self.client.force_login(self.admin)
        res = self.client.get(reverse('nursing:review'))
        self.assertContains(res, '午前は体操に参加した。')
        res = self.client.post(reverse('nursing:record_approve', args=[r.pk]), {'back': reverse('nursing:review')})
        self.assertEqual(res.status_code, 302)
        r.refresh_from_db()
        self.assertEqual(r.status, STATUS_APPROVED)
        self.assertEqual(r.approved_by, self.admin)
        # 一般の職員が承認ずみを直すと確認待ちに戻る
        self.client.force_login(self.staff)
        self.client.post(url, {**data, 'body': '午前は体操に参加し、昼食は 8 割。', 'action': 'save'})
        r.refresh_from_db()
        self.assertEqual(r.status, STATUS_REVIEW)
        # 戻す
        self.client.force_login(self.admin)
        self.client.post(reverse('nursing:record_approve', args=[r.pk]), {'action': 'send_back', 'review_note': '昼食の量を足して'})
        r.refresh_from_db()
        self.assertEqual((r.status, r.review_note), (STATUS_DRAFT, '昼食の量を足して'))

    def test_review_requires_body(self):
        url = reverse('nursing:record_edit', args=[self.b.pk])
        self.client.post(url, {'d': self.today.isoformat(), 'keywords': '体操', 'body': '', 'action': 'review'})
        r = CareRecord.objects.get(beneficiary=self.b, date=self.today)
        self.assertEqual(r.status, STATUS_DRAFT)

    def test_records_list_and_person_print(self):
        CareRecord.objects.create(facility=self.f, beneficiary=self.b, date=self.today, body='記録の本文', status=STATUS_APPROVED, author=self.admin)
        res = self.client.get(reverse('nursing:records'))
        self.assertContains(res, '承認ずみ')
        res = self.client.get(reverse('nursing:person_records', args=[self.b.pk]))
        self.assertContains(res, '記録の本文')
        res = self.client.get(reverse('nursing:person_records_print', args=[self.b.pk]) + '?fmt=html')
        self.assertContains(res, '介護記録')

    def test_ai_without_key_returns_error(self):
        with self.settings(ANTHROPIC_API_KEY=''):
            res = self.client.post(reverse('nursing:ai_record', args=[self.b.pk]), '{"keywords": "体操", "date": "%s"}' % self.today.isoformat(), content_type='application/json')
            self.assertEqual(res.status_code, 500)
            self.assertIn('ANTHROPIC_API_KEY', res.json()['error'])
            res = self.client.post(reverse('nursing:ai_rewrite'), '{"text": ""}', content_type='application/json')
            self.assertEqual(res.status_code, 400)


class RenrakuTests(CareBase):
    def test_list_and_print(self):
        CareRecord.objects.create(facility=self.f, beneficiary=self.b, date=self.today, body='本文', family_message='本日は元気でした。', status=STATUS_APPROVED)
        Guardian.objects.create(beneficiary=self.b, last_name='山本', first_name='一郎', relation='other', is_primary=True)
        res = self.client.get(reverse('nursing:renraku'))
        self.assertContains(res, '本日は元気でした。')
        self.assertContains(res, 'LINE 未連携')
        res = self.client.get(reverse('nursing:renraku_print') + '?fmt=html')
        self.assertContains(res, '山本 和子 様')
        # LINE 未設定なら送らない
        res = self.client.post(reverse('nursing:renraku'), {'d': self.today.isoformat(), 'record': [CareRecord.objects.first().pk]})
        self.assertEqual(res.status_code, 302)
        self.assertIsNone(CareRecord.objects.first().line_sent_at)


class IncidentTests(CareBase):
    def test_create_review_approve_print(self):
        self.client.force_login(self.staff)
        res = self.client.post(reverse('nursing:incident_new'), {'kind': 'near', 'category': 'fall', 'date': self.today.isoformat(), 'time': '14:05', 'beneficiary': self.b.pk,
                                                                 'place': '食堂', 'injury': 'none', 'lang': 'ja', 'memo': 'ふらつき', 'situation': '立ち上がりでふらついた。',
                                                                 'cause': 'ブレーキ', 'response': '支えた', 'prevention': '声出し確認', 'reported_to': ['family', 'manager'], 'action': 'review'})
        self.assertEqual(res.status_code, 302)
        rep = IncidentReport.objects.get()
        self.assertEqual((rep.status, rep.reported_to, rep.place), (STATUS_REVIEW, ['family', 'manager'], '食堂'))
        self.assertEqual(rep.occurred_at.astimezone().strftime('%H:%M'), '14:05')
        self.client.force_login(self.admin)
        res = self.client.post(reverse('nursing:incident_edit', args=[rep.pk]), {'action': 'approve'})
        rep.refresh_from_db()
        self.assertEqual(rep.status, STATUS_APPROVED)
        res = self.client.get(reverse('nursing:incident_print', args=[rep.pk]) + '?fmt=html')
        self.assertContains(res, 'ヒヤリハット報告書')
        self.assertContains(res, '声出し確認')
        res = self.client.get(reverse('nursing:incidents') + '?kind=near')
        self.assertContains(res, '立ち上がりでふらついた')


class MedicationTests(CareBase):
    def test_add_and_due_alert(self):
        url = reverse('nursing:person_meds', args=[self.b.pk])
        res = self.client.post(url, {'name': 'アムロジピン', 'dose': '1 錠 朝食後', 'timings': ['morning'], 'review_due': (self.today + datetime.timedelta(days=5)).isoformat(), 'is_active': '1'})
        self.assertEqual(res.status_code, 302)
        m = Medication.objects.get()
        self.assertEqual((m.timing_text, m.due_state(self.today)), ('朝', 'soon'))
        res = self.client.get(reverse('nursing:medications'))
        self.assertContains(res, '更新の期限が近い')
        res = self.client.get(reverse('nursing:person_meds_print', args=[self.b.pk]) + '?fmt=html')
        self.assertContains(res, 'アムロジピン')
        self.client.post(url, {'med': m.pk, 'action': 'stop'})
        m.refresh_from_db()
        self.assertFalse(m.is_active)
        res = self.client.get(reverse('nursing:home'))
        self.assertEqual(res.status_code, 200)


class PlanTests(CareBase):
    def test_create_and_print(self):
        res = self.client.post(reverse('nursing:plan_new', args=[self.b.pk]), {'created_on': self.today.isoformat(), 'care_level': '要介護2', 'planner': '相談員',
                                                                                'wishes_person': '歩きたい', 'policy': '見守り', 'row_0_goal': '転ばない', 'row_0_service': '機能訓練',
                                                                                'row_1_goal': '', 'review_due': (self.today + datetime.timedelta(days=10)).isoformat(), 'next': 'print'})
        self.assertEqual(res.status_code, 302)
        plan = CarePlan.objects.get()
        self.assertEqual(len(plan.rows), 1)
        self.assertEqual(plan.review_state(self.today), 'soon')
        res = self.client.get(reverse('nursing:plan_print', args=[self.b.pk, plan.pk]) + '?fmt=html')
        self.assertContains(res, '通所介護計画書')
        self.assertContains(res, '転ばない')
        res = self.client.get(reverse('nursing:home'))
        self.assertContains(res, '見直し')


class StaffingTests(CareBase):
    def test_rules(self):
        self.assertEqual([required_care_staff(n) for n in (0, 1, 15, 16, 20, 21, 25)], [0, 1, 1, 2, 2, 3, 3])
        d = StaffingDay(facility=self.f, date=self.today, users=18, care_staff=1, nurses=0, counselors=1, trainers=1)
        j = judge(d)
        self.assertEqual(j['level'], 'ng')
        self.assertTrue(any('看護職員' in p for p in j['problems']))
        d = StaffingDay(facility=self.f, date=self.today, users=10, care_staff=1, nurses=0, counselors=0, trainers=0)
        self.assertEqual(judge(d)['level'], 'warn')
        d = StaffingDay(facility=self.f, date=self.today, users=10, care_staff=1, nurses=1, counselors=1, trainers=1)
        self.assertTrue(judge(d)['ok'])

    def test_view_save(self):
        res = self.client.post(reverse('nursing:staffing'), {'date': self.today.isoformat(), 'users': '12', 'care_staff': '2', 'nurses': '1', 'counselors': '1', 'trainers': '1'})
        self.assertEqual(res.status_code, 302)
        res = self.client.get(reverse('nursing:staffing'))
        self.assertContains(res, '安全')


class TransportTests(CareBase):
    def test_day_from_weekdays(self):
        from transport.models import TransportProfile, Vehicle
        v = Vehicle.objects.create(facility=self.f, name='ハイエース', capacity=4)
        TransportProfile.objects.create(beneficiary=self.b, pickup=True, pickup_place='自宅', pickup_time=datetime.time(8, 30), dropoff=True, default_vehicle=v)
        monday = self.today - datetime.timedelta(days=self.today.weekday())
        res = self.client.get(reverse('nursing:transport_day') + f'?d={monday.isoformat()}')
        self.assertContains(res, '山本 和子')
        self.assertContains(res, 'ハイエース 1/4')
        tuesday = monday + datetime.timedelta(days=1)
        res = self.client.get(reverse('nursing:transport_day') + f'?d={tuesday.isoformat()}')
        self.assertNotContains(res, '山本 和子')
        res = self.client.get(reverse('nursing:transport_day_print') + f'?d={monday.isoformat()}&fmt=html')
        self.assertContains(res, '送迎表')


class DemoTests(CareBase):
    def test_demo_add_and_remove(self):
        n = demo.add(self.f, self.admin)
        self.assertEqual(n, 4)
        self.assertEqual(demo.add(self.f, self.admin), 0)
        self.assertTrue(VitalsLog.objects.filter(facility=self.f).exists())
        self.assertTrue(CareRecord.objects.filter(facility=self.f, status=STATUS_APPROVED).exists())
        self.assertTrue(IncidentReport.objects.filter(facility=self.f).exists())
        for name in ('nursing:home', 'nursing:vitals_day', 'nursing:records', 'nursing:review', 'nursing:renraku', 'nursing:incidents', 'nursing:medications', 'nursing:staffing', 'nursing:transport_day', 'nursing:docs', 'nursing:settings'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        self.assertEqual(demo.remove(self.f), 4)
        self.assertFalse(CareRecord.objects.filter(facility=self.f, beneficiary__notes__startswith=demo.MARK).exists())
        self.assertTrue(Beneficiary.objects.filter(pk=self.b.pk).exists())

    def test_settings_save(self):
        res = self.client.post(reverse('nursing:settings'), {'record_chars': '300', 'family_chars': '120', 'style_note': '敬語は最小限', 'fever_temp': '37.3', 'capacity': '25'})
        self.assertEqual(res.status_code, 302)
        from .models import NursingSetting
        s = NursingSetting.of(self.f)
        self.assertEqual((s.record_chars, s.family_chars, str(s.fever_temp), s.capacity), (300, 120, '37.3', 25))

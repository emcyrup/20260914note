from datetime import date, time

from django.test import TestCase
from django.urls import reverse

from accounts.models import StaffAccount
from beneficiaries.models import Beneficiary, Guardian, RecipientCertificate
from billing.models import BillingMatrixAddon, BillingMatrixEntry
from facilities.models import AddonMaster, Facility
from records.models import DailyRecord
from schedules.models import ScheduledVisit
from support_plans.models import SupportPlan

from . import services


class ReportTests(TestCase):
    """帳票：データの組み立てと CSV／PDF／画面の出力"""

    def setUp(self):
        self.facility = Facility.objects.create(name='テスト事業所', office_number='1234567890', term_beneficiary='園児')
        self.user = StaffAccount.objects.create_user('staff', password='pass12345', facility=self.facility, display_name='てすと')
        self.client.force_login(self.user)
        self.b = Beneficiary.objects.create(facility=self.facility, last_name='山田', first_name='太郎',
                                            last_name_kana='やまだ', first_name_kana='たろう',
                                            date_of_birth=date(2016, 4, 1), weekday_mon=True)
        Guardian.objects.create(beneficiary=self.b, last_name='山田', first_name='花子', relation='mother', phone='090-0000-0000', is_primary=True)
        RecipientCertificate.objects.create(beneficiary=self.b, certificate_number='0001', granted_days=23, monthly_cap=4600,
                                            valid_from=date(2026, 4, 1), valid_until=date(2027, 3, 31))
        ScheduledVisit.objects.create(facility=self.facility, beneficiary=self.b, date=date(2026, 9, 7),
                                      status=ScheduledVisit.STATUS_ATTENDED, has_pickup=True, notes='学校から')
        ScheduledVisit.objects.create(facility=self.facility, beneficiary=self.b, date=date(2026, 9, 14), status=ScheduledVisit.STATUS_ABSENT)
        entry = BillingMatrixEntry.objects.create(facility=self.facility, beneficiary=self.b, date=date(2026, 9, 7),
                                                  status=BillingMatrixEntry.STATUS_ATTENDED)
        addon = AddonMaster.objects.create(name='送迎加算（往・迎え）', addon_type='individual', unit_count=54)
        BillingMatrixAddon.objects.create(entry=entry, addon=addon)
        self.rec = DailyRecord.objects.create(
            facility=self.facility, beneficiary=self.b, date=date(2026, 9, 7), author=self.user,
            entry_time=time(15, 30), exit_time=time(17, 30), activity_name='クッキー作り',
            activity_viewpoints=[{'text': '順番を待てるか', 'answer': 'yes'}],
            observation_text='友だちと協力して作れました。', support_text='声かけをしました。', reaction_text='笑顔でした。',
            status=DailyRecord.STATUS_CONFIRMED,
        )
        self.plan = SupportPlan.objects.create(facility=self.facility, beneficiary=self.b, title='第1期 個別支援計画')

    def test_service_record_rows(self):
        r = services.service_record(self.facility, self.b, 2026, 9)
        self.assertEqual(len(r.rows), 2)
        self.assertEqual(r.rows[0][:5], ['7', '月', '利用', '15:30', '17:30'])
        self.assertEqual(r.rows[0][5], '○')
        self.assertIn('送迎加算', r.rows[0][7])
        self.assertEqual(r.rows[1][2], '欠席')
        self.assertIn(('利用日数', '1 日'), r.meta)
        self.assertIn(('園児氏名', '山田 太郎（やまだ たろう）'), r.meta)

    def test_attendance_summary_and_daily_journal(self):
        r = services.attendance_summary(self.facility, 2026, 9)
        self.assertEqual(r.rows[0][0], '山田 太郎')
        self.assertEqual(r.rows[0][4:8], ['1', '1', '0', '1'])   # 来所・欠席・振替・迎え
        self.assertEqual(r.rows[0][10], '1')                     # 請求：利用
        j = services.daily_journal(self.facility, date(2026, 9, 7))
        self.assertEqual(j.columns[0], '園児')
        self.assertEqual(j.rows[0][0], '山田 太郎')
        self.assertIn('クッキー作り', j.rows[0])

    def test_records_roster_plans(self):
        r = services.beneficiary_records(self.facility, self.b, date(2026, 9, 1), date(2026, 9, 30))
        self.assertEqual(len(r.rows), 1)
        self.assertIn('順番を待てるか：はい', r.rows[0][3])
        roster = services.beneficiary_roster(self.facility)
        self.assertEqual(roster.rows[0][8], '山田 花子')
        self.assertEqual(roster.rows[0][11], '0001')
        self.assertEqual(roster.rows[0][14], '4,600')
        plans = services.plan_list(self.facility, 'open')
        self.assertEqual(plans.rows[0][1], '第1期 個別支援計画')
        self.assertTrue(plans.rows[0][3].startswith('1.'))

    def test_index_page(self):
        res = self.client.get(reverse('reports:index'))
        self.assertContains(res, 'サービス提供実績記録票')
        self.assertContains(res, '園児名簿')

    def test_csv_export(self):
        res = self.client.get(reverse('reports:export'), {'kind': 'service_record', 'fmt': 'csv',
                                                         'beneficiary': self.b.pk, 'year': 2026, 'month': 9})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Content-Type'], 'text/csv; charset=utf-8')
        self.assertTrue(res.content.startswith(b'\xef\xbb\xbf'))
        self.assertEqual(res.content.count(b'\xef\xbb\xbf'), 1)
        body = res.content.decode('utf-8-sig')
        self.assertIn('サービス提供実績記録票', body)
        self.assertIn('7,月,利用,15:30,17:30,○,,送迎加算（往・迎え）,学校から,', body)
        res = self.client.get(reverse('reports:export'), {'kind': 'beneficiary_roster', 'fmt': 'csv'})
        self.assertIn('山田 太郎,やまだ たろう,2016/04/01', res.content.decode('utf-8-sig'))

    def test_html_and_pdf_export(self):
        res = self.client.get(reverse('reports:export'), {'kind': 'daily_journal', 'fmt': 'html', 'date': '2026-09-07'})
        self.assertContains(res, '業務日誌')
        self.assertContains(res, '友だちと協力して作れました。')
        self.assertContains(res, 'PDFをダウンロード')
        res = self.client.get(reverse('reports:export'), {'kind': 'attendance_summary', 'fmt': 'pdf', 'year': 2026, 'month': 9})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Content-Type'], 'application/pdf')
        self.assertTrue(res.content.startswith(b'%PDF'))

    def test_invalid_and_other_facility(self):
        res = self.client.get(reverse('reports:export'), {'kind': 'nope', 'fmt': 'csv'})
        self.assertRedirects(res, reverse('reports:index'))
        other = Facility.objects.create(name='別施設')
        u2 = StaffAccount.objects.create_user('other', password='pass12345', facility=other)
        self.client.force_login(u2)
        res = self.client.get(reverse('reports:export'), {'kind': 'service_record', 'fmt': 'csv', 'beneficiary': self.b.pk})
        self.assertEqual(res.status_code, 404)
        res = self.client.get(reverse('reports:export'), {'kind': 'beneficiary_roster', 'fmt': 'csv'})
        self.assertNotIn('山田', res.content.decode('utf-8-sig'))


class RyoikuReportTests(TestCase):
    """療育の事業所（ゆあーず・オウル）：予約の実績と療育記録から帳票を作る。メニューは use_reports で出す"""

    def setUp(self):
        from facilities.management.commands.create_facility import apply_ryoiku_preset
        from reservations.models import Reservation
        from therapy.models import TherapyRecord
        self.facility = Facility.objects.create(name='療育（仮）')
        apply_ryoiku_preset(self.facility)
        self.facility.use_reports = True
        self.facility.save()
        self.user = StaffAccount.objects.create_user('ryo', password='pass12345', facility=self.facility, display_name='担当A', role=StaffAccount.ROLE_ADMIN)
        self.client.force_login(self.user)
        self.b = Beneficiary.objects.create(facility=self.facility, last_name='試験', first_name='花子', last_name_kana='しけん', first_name_kana='はなこ',
                                            date_of_birth=date(2020, 4, 1))
        RecipientCertificate.objects.create(beneficiary=self.b, certificate_number='0002', granted_days=10, monthly_cap=0,
                                            valid_from=date(2026, 4, 1), valid_until=date(2027, 3, 31))
        Reservation.objects.create(facility=self.facility, beneficiary=self.b, date=date(2026, 9, 7), start_time=time(10, 0), attendance='attended')
        Reservation.objects.create(facility=self.facility, beneficiary=self.b, date=date(2026, 9, 14), start_time=time(10, 0), attendance='absent')
        Reservation.objects.create(facility=self.facility, beneficiary=self.b, date=date(2026, 9, 21), start_time=time(11, 0))
        TherapyRecord.objects.create(facility=self.facility, beneficiary=self.b, date=date(2026, 9, 7), time=time(10, 0), staff=self.user,
                                     activities=['型はめ', '絵カード'], body='型はめを 3 回やりとげた。', situation='集中して取り組んだ。')

    def test_service_record_from_reservations(self):
        r = services.service_record(self.facility, self.b, 2026, 9)
        self.assertEqual([row[2] for row in r.rows], ['利用', '欠席', '予定'])
        self.assertEqual(r.rows[0][3:5], ['10:00', '10:45'])      # 1 枠 45 分
        self.assertEqual(r.rows[0][5], '担当A')
        self.assertIn('型はめ', r.rows[0][6])
        self.assertIn(('利用日数', '1 日'), r.meta)

    def test_summary_journal_records(self):
        s = services.attendance_summary(self.facility, 2026, 9)
        self.assertEqual(s.rows[0][3:9], ['3', '1', '1', '0', '1', '1'])
        j = services.daily_journal(self.facility, date(2026, 9, 7))
        self.assertEqual(j.title, '業務日誌（療育記録）')
        self.assertIn('型はめ・絵カード', j.rows[0])
        rec = services.beneficiary_records(self.facility, self.b, date(2026, 9, 1), date(2026, 9, 30))
        self.assertEqual(rec.title, '療育記録一覧')
        self.assertEqual(len(rec.rows), 1)
        self.assertEqual(rec.rows[0][5], '型はめを 3 回やりとげた。')

    def test_menu_and_pages(self):
        res = self.client.get(reverse('reports:index'))
        self.assertContains(res, '療育記録一覧')
        self.assertContains(res, '帳票出力')      # メニュー
        res = self.client.get(reverse('reports:export') + f'?kind=service_record&beneficiary={self.b.pk}&year=2026&month=9&fmt=html')
        self.assertContains(res, 'サービス提供実績記録票')
        res = self.client.get(reverse('reports:export') + f'?kind=beneficiary_records&beneficiary={self.b.pk}&start=2026-09-01&end=2026-09-30&fmt=csv')
        self.assertEqual(res.status_code, 200)
        self.facility.use_reports = False
        self.facility.save()
        res = self.client.get(reverse('facilities:dashboard'))
        self.assertNotContains(res, '帳票出力')

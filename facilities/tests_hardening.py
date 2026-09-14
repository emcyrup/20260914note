"""監査の小さな項目：管理者チェック、電子サインの所属、入力値の検証、リダイレクト先"""
from datetime import date

from django.test import TestCase
from django.urls import reverse

from accounts.models import StaffAccount
from beneficiaries.models import Beneficiary
from esignatures.models import EsignatureRecord
from facilities.models import Facility
from records.models import ActivityTag, DailyRecord
from support_plans.models import MonitoringRecord, SupportPlan

SIG = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='


class SettingsAdminOnlyTests(TestCase):
    def setUp(self):
        self.f = Facility.objects.create(name='F', line_channel_access_token='tok', line_channel_secret='sec', use_line=True)
        self.staff = StaffAccount.objects.create_user('stf', password='pass12345', facility=self.f, role=StaffAccount.ROLE_STAFF)
        self.admin = StaffAccount.objects.create_user('adm', password='pass12345', facility=self.f, role=StaffAccount.ROLE_ADMIN)

    def test_staff_cannot_change_facility_or_tags(self):
        self.client.force_login(self.staff)
        res = self.client.post(reverse('facilities:facility_update'), {'name': 'X', 'region_category': self.f.region_category, 'base_unit_count': 604})
        self.assertRedirects(res, reverse('facilities:dashboard'))
        self.f.refresh_from_db()
        self.assertEqual(self.f.name, 'F')
        self.client.post(reverse('facilities:activity_tag_add'), {'name': 'タグ', 'display_order': 1, 'price': 0, 'is_active': 'on'})
        self.assertFalse(ActivityTag.objects.filter(facility=self.f, name='タグ').exists())
        # LINE のトークンは画面に出ない
        res = self.client.get(reverse('facilities:settings'))
        self.assertNotContains(res, 'value="sec"')
        self.assertContains(res, 'トークンは管理者だけが見られます')
        # 加算マスタの投入は開発向けユーザーのみ
        self.client.force_login(self.admin)
        res = self.client.post(reverse('facilities:addon_defaults'))
        self.assertRedirects(res, reverse('facilities:settings'))
        from facilities.models import AddonMaster
        self.assertFalse(AddonMaster.objects.exists())
        res = self.client.get(reverse('facilities:settings'))
        self.assertContains(res, 'value="sec"')


class EsignatureScopeTests(TestCase):
    def setUp(self):
        self.a = Facility.objects.create(name='A')
        self.b = Facility.objects.create(name='B')
        self.ua = StaffAccount.objects.create_user('ua', password='pass12345', facility=self.a)
        ben_b = Beneficiary.objects.create(facility=self.b, last_name='B', first_name='子', date_of_birth=date(2016, 4, 1))
        plan_b = SupportPlan.objects.create(facility=self.b, beneficiary=ben_b, title='B計画')
        self.mon_b = MonitoringRecord.objects.create(plan=plan_b, date=date(2026, 9, 1))

    def test_monitoring_of_other_facility_rejected(self):
        self.client.force_login(self.ua)
        res = self.client.post(reverse('esignatures:save'), {'target_type': 'monitoring', 'target_id': self.mon_b.pk, 'signature': SIG})
        self.assertFalse(res.json()['ok'])
        self.assertEqual(EsignatureRecord.objects.count(), 0)
        res = self.client.post(reverse('esignatures:save'), {'target_type': 'daily_record', 'target_id': 'abc', 'signature': SIG})
        self.assertFalse(res.json()['ok'])
        self.assertEqual(res.status_code, 200)


class InputValidationTests(TestCase):
    def setUp(self):
        self.f = Facility.objects.create(name='F', use_billing=True)
        self.u = StaffAccount.objects.create_user('u', password='pass12345', facility=self.f, role=StaffAccount.ROLE_ADMIN)
        self.client.force_login(self.u)
        self.ben = Beneficiary.objects.create(facility=self.f, last_name='佐', first_name='藤', date_of_birth=date(2016, 4, 1))

    def test_bad_month_is_404_not_500(self):
        for url in (reverse('schedules:calendar_month', args=[2026, 13]), reverse('billing:matrix_month', args=[2026, 13]),
                    reverse('billing:billing_csv', args=[2026, 0])):
            self.assertEqual(self.client.get(url).status_code, 404, url)
        self.assertEqual(self.client.get(reverse('billing:cell_popup', args=[self.ben.pk, 2026, 2, 31])).status_code, 404)

    def test_bad_ids_and_dates_do_not_crash(self):
        res = self.client.get(reverse('records:list', args=[self.ben.pk]) + '?selected=abc')
        self.assertEqual(res.status_code, 200)
        res = self.client.post(reverse('records:create', args=[self.ben.pk]), {'date': 'not-a-date'})
        self.assertEqual(res.status_code, 302)
        self.assertFalse(DailyRecord.objects.exists())
        res = self.client.get(reverse('reports:export') + '?kind=service_record&fmt=csv&beneficiary=abc&year=2026&month=9')
        self.assertIn(res.status_code, (302, 404))
        res = self.client.post(reverse('support_plans:create'), {'beneficiary': 'abc', 'title': 'x'})
        self.assertEqual(res.status_code, 404)

    def test_next_redirect_stays_on_site(self):
        res = self.client.post(reverse('records:create', args=[self.ben.pk]), {'date': '2026-09-01', 'next': 'https://evil.example/'})
        self.assertEqual(res.status_code, 302)
        self.assertTrue(res['Location'].startswith('/records/'))
        res = self.client.post(reverse('records:create', args=[self.ben.pk]), {'date': '2026-09-02', 'next': '/\\evil.example'})
        self.assertTrue(res['Location'].startswith('/records/'))
        self.u.is_developer = True; self.u.save()
        res = self.client.post(reverse('accounts:switch_facility'), {'facility': 'abc', 'next': '//evil.example'})
        self.assertEqual(res['Location'], reverse('facilities:dashboard'))

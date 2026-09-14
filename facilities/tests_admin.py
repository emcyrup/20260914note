"""Django 管理画面：スーパーユーザー以外は自事業所の行だけ"""
from datetime import date

from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from accounts.models import StaffAccount
from beneficiaries.models import Beneficiary
from facilities.models import Facility
from records.models import DailyRecord


class AdminScopingTests(TestCase):
    def setUp(self):
        self.a = Facility.objects.create(name='Aえん')
        self.b = Facility.objects.create(name='Bえん')
        self.staff_a = StaffAccount.objects.create_user('sa', password='pass12345', facility=self.a, is_staff=True)
        self.staff_a.user_permissions.set(Permission.objects.filter(
            content_type__app_label__in=['records', 'accounts', 'facilities', 'support_plans', 'ai_assist']))
        ben_a = Beneficiary.objects.create(facility=self.a, last_name='A', first_name='子', date_of_birth=date(2016, 4, 1))
        ben_b = Beneficiary.objects.create(facility=self.b, last_name='Bひみつ', first_name='子', date_of_birth=date(2016, 4, 1))
        self.rec_a = DailyRecord.objects.create(facility=self.a, beneficiary=ben_a, date=date(2026, 9, 1))
        self.rec_b = DailyRecord.objects.create(facility=self.b, beneficiary=ben_b, date=date(2026, 9, 1))
        self.user_b = StaffAccount.objects.create_user('ub', password='pass12345', facility=self.b, display_name='Bの職員')

    def test_staff_sees_only_own_facility_rows(self):
        self.client.force_login(self.staff_a)
        res = self.client.get(reverse('admin:records_dailyrecord_changelist'))
        self.assertContains(res, 'A 子')
        self.assertNotContains(res, 'Bひみつ')
        self.assertEqual(self.client.get(reverse('admin:records_dailyrecord_change', args=[self.rec_b.pk])).status_code, 302)
        res = self.client.get(reverse('admin:accounts_staffaccount_changelist'))
        self.assertNotContains(res, 'Bの職員')
        res = self.client.get(reverse('admin:facilities_facility_changelist'))
        self.assertContains(res, 'Aえん')
        self.assertNotContains(res, 'Bえん')

    def test_staff_cannot_change_facility_or_flags(self):
        self.client.force_login(self.staff_a)
        res = self.client.get(reverse('admin:accounts_staffaccount_change', args=[self.staff_a.pk]))
        self.assertEqual(res.status_code, 200)
        self.assertNotContains(res, 'name="is_superuser"')
        self.assertNotContains(res, 'name="is_developer"')
        self.assertNotContains(res, '<select name="facility"')
        # 加算マスタは読むだけ
        res = self.client.get(reverse('admin:facilities_addonmaster_add'))
        self.assertEqual(res.status_code, 403)

    def test_superuser_sees_everything(self):
        root = StaffAccount.objects.create_superuser('root', 'r@example.com', 'pass12345')
        self.client.force_login(root)
        res = self.client.get(reverse('admin:records_dailyrecord_changelist'))
        self.assertContains(res, 'Bひみつ')
        res = self.client.get(reverse('admin:accounts_staffaccount_change', args=[self.staff_a.pk]))
        self.assertContains(res, 'name="is_superuser"')

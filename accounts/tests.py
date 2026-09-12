from django.test import TestCase
from django.urls import reverse

from facilities.models import Facility

from .models import StaffAccount


class StaffAccountAdminTests(TestCase):
    """管理画面から所属施設・権限区分を設定できること。"""

    def setUp(self):
        self.facility = Facility.objects.create(name='テスト施設', office_number='1234567890')
        self.admin = StaffAccount.objects.create_superuser('root', 'root@example.com', 'pass12345')
        self.client.force_login(self.admin)

    def test_change_form_shows_facility_and_role(self):
        url = reverse('admin:accounts_staffaccount_change', args=[self.admin.pk])
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'name="facility"')
        self.assertContains(res, 'name="role"')
        self.assertContains(res, 'name="display_name"')

    def test_change_form_saves_facility_and_role(self):
        url = reverse('admin:accounts_staffaccount_change', args=[self.admin.pk])
        res = self.client.post(url, {
            'username': 'root',
            'facility': self.facility.pk,
            'role': StaffAccount.ROLE_ADMIN,
            'display_name': '管理者',
            'first_name': '', 'last_name': '', 'email': 'root@example.com',
            'is_active': 'on', 'is_staff': 'on', 'is_superuser': 'on',
            'date_joined_0': '2026-01-01', 'date_joined_1': '00:00:00',
            '_save': '保存',
        })
        self.assertEqual(res.status_code, 302)
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.facility, self.facility)
        self.assertEqual(self.admin.role, StaffAccount.ROLE_ADMIN)
        self.assertEqual(self.admin.display_name, '管理者')

    def test_add_form_shows_facility(self):
        res = self.client.get(reverse('admin:accounts_staffaccount_add'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'name="facility"')

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
            'display_name': '管理者', 'ui_theme': 'standard',
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


class ThemeTests(TestCase):
    """画面の着せ替え：自分の切替、管理者による割り当て、かんたん3ステップのホーム"""

    def setUp(self):
        self.facility = Facility.objects.create(name='テスト施設')
        self.admin = StaffAccount.objects.create_user('adm', password='pass12345', facility=self.facility,
                                                      role=StaffAccount.ROLE_ADMIN)
        self.staff = StaffAccount.objects.create_user('stf', password='pass12345', facility=self.facility,
                                                      role=StaffAccount.ROLE_STAFF)

    def test_page_lists_four_themes(self):
        self.client.force_login(self.staff)
        res = self.client.get(reverse('accounts:theme'))
        self.assertEqual(res.status_code, 200)
        for label in ('スタンダード', '大きな文字', 'スタイリッシュ', 'かんたん3ステップ'):
            self.assertContains(res, label)
        self.assertNotContains(res, '職員ごとの割り当て')

    def test_switch_own_theme_sets_body_class(self):
        self.client.force_login(self.staff)
        res = self.client.post(reverse('accounts:theme'), {'theme': 'large'})
        self.assertRedirects(res, reverse('facilities:dashboard'))
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.ui_theme, 'large')
        res = self.client.get(reverse('facilities:dashboard'))
        self.assertContains(res, 'class="theme-large"')

    def test_simple_theme_redirects_home_and_hides_menu(self):
        self.client.force_login(self.staff)
        res = self.client.post(reverse('accounts:theme'), {'theme': 'simple'})
        self.assertRedirects(res, reverse('records:simple_home'))
        res = self.client.get(reverse('facilities:dashboard'))
        self.assertRedirects(res, reverse('records:simple_home'))
        res = self.client.get(reverse('records:simple_home'))
        self.assertContains(res, 'class="theme-simple"')
        self.assertContains(res, 'きょうの きろく')

    def test_staff_cannot_change_others(self):
        self.client.force_login(self.staff)
        self.client.post(reverse('accounts:theme'), {'theme': 'stylish', 'staff_id': self.admin.pk})
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.ui_theme, 'standard')

    def test_admin_assigns_theme_to_staff(self):
        self.client.force_login(self.admin)
        res = self.client.get(reverse('accounts:theme'))
        self.assertContains(res, '職員ごとの割り当て')
        res = self.client.post(reverse('accounts:theme'), {'theme': 'large', 'staff_id': self.staff.pk})
        self.assertRedirects(res, reverse('accounts:theme'))
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.ui_theme, 'large')
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.ui_theme, 'standard')

    def test_invalid_theme_rejected(self):
        self.client.force_login(self.staff)
        self.client.post(reverse('accounts:theme'), {'theme': 'neon'})
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.ui_theme, 'standard')


class StaffManagementTests(TestCase):
    def setUp(self):
        self.facility = Facility.objects.create(name='F')
        self.admin = StaffAccount.objects.create_user('adm', password='pass12345', facility=self.facility, role=StaffAccount.ROLE_ADMIN)
        self.staff = StaffAccount.objects.create_user('stf', password='pass12345', facility=self.facility, role=StaffAccount.ROLE_STAFF)

    def test_staff_cannot_open(self):
        self.client.force_login(self.staff)
        self.assertRedirects(self.client.get(reverse('accounts:staff')), reverse('facilities:dashboard'))

    def test_admin_adds_and_updates_staff(self):
        self.client.force_login(self.admin)
        res = self.client.get(reverse('accounts:staff'))
        self.assertContains(res, 'stf')
        res = self.client.post(reverse('accounts:staff'), {'username': 'new1', 'display_name': '新人', 'role': 'staff',
                                                           'ui_theme': 'large', 'password': 'secret123'})
        self.assertRedirects(res, reverse('accounts:staff'))
        u = StaffAccount.objects.get(username='new1')
        self.assertEqual((u.facility, u.ui_theme, u.display_name), (self.facility, 'large', '新人'))
        self.assertTrue(u.check_password('secret123'))
        res = self.client.post(reverse('accounts:staff_update', args=[u.pk]),
                               {'display_name': '新人2', 'role': 'office', 'ui_theme': 'standard', 'new_password': 'another99'})
        u.refresh_from_db()
        self.assertEqual((u.display_name, u.role, u.is_active), ('新人2', 'office', False))
        self.assertTrue(u.check_password('another99'))
        # 自分を無効にはできない
        self.client.post(reverse('accounts:staff_update', args=[self.admin.pk]), {'display_name': 'a', 'role': 'admin', 'ui_theme': 'standard'})
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_other_facility_staff_not_editable(self):
        other = StaffAccount.objects.create_user('o', password='p', facility=Facility.objects.create(name='G'))
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(reverse('accounts:staff_update', args=[other.pk]), {'display_name': 'x', 'role': 'admin', 'ui_theme': 'standard', 'is_active': 'on'}).status_code, 404)


class BrandingTests(TestCase):
    def test_terms_and_color_applied(self):
        facility = Facility.objects.create(name='F', term_beneficiary='利用児', term_staff='支援員', brand_color='#aa3366')
        user = StaffAccount.objects.create_user('adm', password='p', facility=facility, role=StaffAccount.ROLE_ADMIN)
        self.client.force_login(user)
        res = self.client.get(reverse('beneficiaries:list'))
        self.assertContains(res, '利用児台帳')
        self.assertContains(res, '利用児一覧')
        self.assertContains(res, '支援員・運用管理')
        self.assertContains(res, '--lake: #aa3366')

    def test_invalid_color_rejected(self):
        facility = Facility.objects.create(name='F')
        user = StaffAccount.objects.create_user('adm', password='p', facility=facility, role=StaffAccount.ROLE_ADMIN)
        self.client.force_login(user)
        self.client.post(reverse('facilities:facility_update'), {'name': 'F', 'region_category': facility.region_category,
                                                                  'base_unit_count': 604, 'brand_color': 'red', 'term_staff': '先生', 'term_beneficiary': '園児'})
        facility.refresh_from_db()
        self.assertEqual(facility.brand_color, '')
        self.client.post(reverse('facilities:facility_update'), {'name': 'F', 'region_category': facility.region_category,
                                                                  'base_unit_count': 604, 'brand_color': '#112233', 'term_staff': '先生', 'term_beneficiary': '園児'})
        facility.refresh_from_db()
        self.assertEqual((facility.brand_color, facility.term_staff), ('#112233', '先生'))

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
        self.assertContains(res, 'class="theme-large mode-light"')

    def test_simple_theme_redirects_home_and_hides_menu(self):
        self.client.force_login(self.staff)
        res = self.client.post(reverse('accounts:theme'), {'theme': 'simple'})
        self.assertRedirects(res, reverse('records:simple_home'))
        res = self.client.get(reverse('facilities:dashboard'))
        self.assertRedirects(res, reverse('records:simple_home'))
        res = self.client.get(reverse('records:simple_home'))
        self.assertContains(res, 'class="theme-simple mode-light"')
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


class DisplayPrefsTests(TestCase):
    def setUp(self):
        self.facility = Facility.objects.create(name='F')
        self.user = StaffAccount.objects.create_user('u', password='p', facility=self.facility)
        self.client.force_login(self.user)

    def test_defaults(self):
        p = self.user.prefs
        self.assertEqual((p['font'], p['mode'], p['scale'], p['bg']), ('biz', 'light', 100, ''))
        res = self.client.get(reverse('facilities:dashboard'))
        self.assertContains(res, 'mode-light')
        self.assertContains(res, 'BIZ+UDPGothic')

    def test_save_and_apply(self):
        res = self.client.post(reverse('accounts:theme'), {'form': 'prefs', 'font': 'mincho', 'mode': 'dark', 'scale': 115, 'bg': '#1e1e1e'})
        self.assertRedirects(res, reverse('accounts:theme'))
        self.user.refresh_from_db()
        self.assertEqual(self.user.ui_prefs, {'font': 'mincho', 'mode': 'dark', 'scale': 115, 'bg': '#1e1e1e'})
        res = self.client.get(reverse('facilities:dashboard'))
        self.assertContains(res, 'theme-standard mode-dark')
        self.assertContains(res, 'Noto+Serif+JP')
        self.assertContains(res, 'zoom: 115%')
        self.assertContains(res, '--paper: #1e1e1e')

    def test_custom_color_and_reset(self):
        self.client.post(reverse('accounts:theme'), {'form': 'prefs', 'font': 'biz', 'mode': 'auto', 'scale': 100, 'bg': '', 'bg_custom': '#AABBCC'})
        self.user.refresh_from_db()
        self.assertEqual(self.user.prefs['bg'], '#aabbcc')
        res = self.client.get(reverse('facilities:dashboard'))
        self.assertContains(res, 'prefers-color-scheme: dark')
        self.client.post(reverse('accounts:theme'), {'form': 'prefs', 'font': 'biz', 'mode': 'light', 'scale': 100, 'bg': '#ffffff', 'bg_reset': '1'})
        self.user.refresh_from_db()
        self.assertEqual(self.user.prefs['bg'], '')

    def test_invalid_values_rejected(self):
        self.client.post(reverse('accounts:theme'), {'form': 'prefs', 'font': 'comic', 'mode': 'dark', 'scale': 100})
        self.user.refresh_from_db()
        self.assertEqual(self.user.ui_prefs, {})
        self.user.ui_prefs = {'font': 'zzz', 'scale': 'big', 'bg': 'red', 'mode': 'neon'}
        self.assertEqual(self.user.prefs['font'], 'biz')
        self.assertEqual(self.user.prefs['scale'], 100)
        self.assertEqual(self.user.prefs['bg'], '')


class DeveloperFacilitySwitchTests(TestCase):
    """開発向けユーザー：事業所の切り替え、帳票様式の表示制限"""

    def setUp(self):
        self.f1 = Facility.objects.create(name='みちのて', form_set=Facility.FORM_SET_STANDARD)
        self.f2 = Facility.objects.create(name='はぴねす', form_set=Facility.FORM_SET_HAPPINESS)
        self.dev = StaffAccount.objects.create_user('dev', password='pass12345', facility=self.f1,
                                                    role=StaffAccount.ROLE_ADMIN, is_developer=True)
        self.admin = StaffAccount.objects.create_user('adm', password='pass12345', facility=self.f1,
                                                      role=StaffAccount.ROLE_ADMIN)

    def test_switch_and_back(self):
        self.client.force_login(self.dev)
        res = self.client.get(reverse('facilities:dashboard'))
        self.assertContains(res, 'devFacilitySelect')
        self.assertContains(res, 'みちのて（所属）')
        self.assertNotContains(res, '事業所様式')
        # はぴねすに切り替えると、その事業所として動く（専用様式のメニューが出る）
        res = self.client.post(reverse('accounts:switch_facility'), {'facility': self.f2.pk, 'next': '/'})
        self.assertRedirects(res, '/', fetch_redirect_response=False)
        res = self.client.get(reverse('facilities:dashboard'))
        self.assertContains(res, '事業所様式')
        self.assertContains(res, 'はぴねす')
        res = self.client.get(reverse('custom_forms:index'))
        self.assertEqual(res.status_code, 200)
        # DB の所属は変わらない
        self.dev.refresh_from_db()
        self.assertEqual(self.dev.facility_id, self.f1.pk)
        # 戻す
        res = self.client.post(reverse('accounts:switch_facility'), {'facility': 'home'})
        self.assertRedirects(res, reverse('facilities:dashboard'), fetch_redirect_response=False)
        res = self.client.get(reverse('facilities:dashboard'))
        self.assertNotContains(res, '事業所様式')

    def test_switched_user_save_keeps_home_facility(self):
        self.client.force_login(self.dev)
        self.client.post(reverse('accounts:switch_facility'), {'facility': self.f2.pk})
        # 切り替え中に自分の表示設定を保存しても、所属は元のまま
        self.client.post(reverse('accounts:theme'), {'theme': StaffAccount.THEME_LARGE})
        self.dev.refresh_from_db()
        self.assertEqual(self.dev.facility_id, self.f1.pk)
        self.assertEqual(self.dev.ui_theme, StaffAccount.THEME_LARGE)

    def test_non_developer_cannot_switch(self):
        self.client.force_login(self.admin)
        res = self.client.get(reverse('facilities:dashboard'))
        self.assertNotContains(res, 'devFacilitySelect')
        res = self.client.post(reverse('accounts:switch_facility'), {'facility': self.f2.pk})
        self.assertRedirects(res, reverse('facilities:dashboard'), fetch_redirect_response=False)
        res = self.client.get(reverse('facilities:dashboard'))
        self.assertNotContains(res, '事業所様式')

    def test_superuser_without_facility_gets_first(self):
        root = StaffAccount.objects.create_superuser('root', 'root@example.com', 'pass12345')
        self.client.force_login(root)
        res = self.client.get(reverse('facilities:dashboard'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'devFacilitySelect')

    def test_form_set_only_for_developer(self):
        # 管理者には帳票様式の選択が出ず、送っても変わらない
        self.client.force_login(self.admin)
        res = self.client.get(reverse('facilities:settings'))
        self.assertNotContains(res, 'name="form_set"')
        self.assertNotContains(res, 'はぴねす様式')
        self.client.post(reverse('facilities:feature_settings'), {
            'use_billing': 'on', 'journal_sections': ['activity', 'observation'], 'form_set': 'happiness'})
        self.f1.refresh_from_db()
        self.assertEqual(self.f1.form_set, Facility.FORM_SET_STANDARD)
        # 開発向けユーザーには出て、変えられる
        self.client.force_login(self.dev)
        res = self.client.get(reverse('facilities:settings'))
        self.assertContains(res, 'name="form_set"')
        self.client.post(reverse('facilities:feature_settings'), {
            'use_billing': 'on', 'journal_sections': ['activity', 'observation'], 'form_set': 'happiness'})
        self.f1.refresh_from_db()
        self.assertEqual(self.f1.form_set, Facility.FORM_SET_HAPPINESS)

    def test_set_developer_command(self):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        call_command('set_developer', 'adm', stdout=out)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_developer)
        call_command('set_developer', 'adm', '--off', stdout=out)
        self.admin.refresh_from_db()
        self.assertFalse(self.admin.is_developer)


class SelfRegistrationTests(TestCase):
    """自己登録：招待リンクによる職員登録、新しい事業所の登録"""

    def setUp(self):
        self.facility = Facility.objects.create(name='みちのて')
        self.admin = StaffAccount.objects.create_user('adm', password='pass12345', facility=self.facility, role=StaffAccount.ROLE_ADMIN)

    def _invite(self, **kw):
        from django.utils import timezone
        import datetime
        from .models import StaffInvitation
        d = dict(facility=self.facility, role=StaffAccount.ROLE_STAFF, max_uses=1,
                 expires_at=timezone.now() + datetime.timedelta(days=7), created_by=self.admin)
        d.update(kw)
        return StaffInvitation.objects.create(**d)

    def test_admin_issues_and_revokes_invitation(self):
        from .models import StaffInvitation
        self.client.force_login(self.admin)
        res = self.client.post(reverse('accounts:invitation_add'), {'role': 'office', 'note': '4月入職', 'max_uses': 3, 'expires_days': 3})
        self.assertRedirects(res, reverse('accounts:staff'))
        inv = StaffInvitation.objects.get(facility=self.facility)
        self.assertEqual((inv.role, inv.note, inv.max_uses, inv.created_by), ('office', '4月入職', 3, self.admin))
        self.assertTrue(inv.is_usable)
        res = self.client.get(reverse('accounts:staff'))
        self.assertContains(res, inv.get_absolute_url())
        self.assertContains(res, '招待リンクを発行')
        res = self.client.post(reverse('accounts:invitation_revoke', args=[inv.pk]))
        inv.refresh_from_db()
        self.assertFalse(inv.is_active)
        self.assertEqual(inv.status_label, '取り消し')

    def test_non_admin_cannot_issue(self):
        staff = StaffAccount.objects.create_user('stf', password='pass12345', facility=self.facility)
        self.client.force_login(staff)
        res = self.client.post(reverse('accounts:invitation_add'), {'role': 'admin', 'max_uses': 1, 'expires_days': 7})
        self.assertRedirects(res, reverse('facilities:dashboard'))
        from .models import StaffInvitation
        self.assertFalse(StaffInvitation.objects.exists())

    def test_other_facility_admin_cannot_revoke(self):
        inv = self._invite()
        other = StaffAccount.objects.create_user('o', password='pass12345', facility=Facility.objects.create(name='G'), role=StaffAccount.ROLE_ADMIN)
        self.client.force_login(other)
        self.assertEqual(self.client.post(reverse('accounts:invitation_revoke', args=[inv.pk])).status_code, 404)

    def test_join_creates_account_and_logs_in(self):
        inv = self._invite(role=StaffAccount.ROLE_CHILD_DEV_MANAGER)
        url = reverse('accounts:join', args=[inv.token])
        res = self.client.get(url)
        self.assertContains(res, 'みちのて')
        self.assertContains(res, '児発管')
        res = self.client.post(url, {'username': 'hanako', 'display_name': '山田 花子', 'password1': 'kaede-2026!', 'password2': 'kaede-2026!'})
        self.assertRedirects(res, reverse('facilities:dashboard'), fetch_redirect_response=False)
        u = StaffAccount.objects.get(username='hanako')
        self.assertEqual((u.facility, u.role, u.display_name), (self.facility, 'child_dev_manager', '山田 花子'))
        self.assertTrue(u.check_password('kaede-2026!'))
        inv.refresh_from_db()
        self.assertEqual(inv.used_count, 1)
        self.assertFalse(inv.is_usable)
        # ログイン済みになっている
        res = self.client.get(reverse('facilities:dashboard'))
        self.assertContains(res, '山田 花子')
        # 使い切ったリンクは登録できない
        res = self.client.post(url, {'username': 'taro', 'display_name': 't', 'password1': 'kaede-2026!', 'password2': 'kaede-2026!'})
        self.assertContains(res, '使用済み')
        self.assertFalse(StaffAccount.objects.filter(username='taro').exists())

    def test_join_validation(self):
        inv = self._invite()
        url = reverse('accounts:join', args=[inv.token])
        # 既存ID・パスワード不一致・短いパスワード
        res = self.client.post(url, {'username': 'adm', 'display_name': 'x', 'password1': 'kaede-2026!', 'password2': 'kaede-2026!'})
        self.assertContains(res, 'すでに使われています')
        res = self.client.post(url, {'username': 'new', 'display_name': 'x', 'password1': 'kaede-2026!', 'password2': 'other'})
        self.assertContains(res, '一致しません')
        res = self.client.post(url, {'username': 'new', 'display_name': 'x', 'password1': '1234', 'password2': '1234'})
        self.assertEqual(res.status_code, 200)
        self.assertFalse(StaffAccount.objects.filter(username='new').exists())
        inv.refresh_from_db()
        self.assertEqual(inv.used_count, 0)

    def test_expired_or_revoked_or_unknown(self):
        import datetime
        from django.utils import timezone
        inv = self._invite(expires_at=timezone.now() - datetime.timedelta(minutes=1))
        res = self.client.get(reverse('accounts:join', args=[inv.token]))
        self.assertContains(res, '期限切れ')
        inv2 = self._invite(is_active=False)
        res = self.client.get(reverse('accounts:join', args=[inv2.token]))
        self.assertContains(res, '取り消し')
        self.assertEqual(self.client.get(reverse('accounts:join', args=['no-such-token'])).status_code, 404)

    def test_signup_disabled_by_default(self):
        with self.settings(ALLOW_FACILITY_SIGNUP=False):
            self.assertEqual(self.client.get(reverse('accounts:signup')).status_code, 404)
            res = self.client.get(reverse('accounts:login'))
            self.assertNotContains(res, '新しい事業所として登録')

    def test_signup_creates_facility_and_admin(self):
        from records.models import ActivityTag
        with self.settings(ALLOW_FACILITY_SIGNUP=True, SIGNUP_CODE=''):
            res = self.client.get(reverse('accounts:login'))
            self.assertContains(res, '新しい事業所として登録')
            res = self.client.get(reverse('accounts:signup'))
            self.assertContains(res, '事業所名')
            self.assertNotContains(res, '登録コード')
            res = self.client.post(reverse('accounts:signup'), {
                'facility_name': 'あおば教室', 'office_number': '1234567890', 'display_name': '佐藤 太郎',
                'username': 'aoba_admin', 'password1': 'aoba-2026!!', 'password2': 'aoba-2026!!'})
            self.assertRedirects(res, reverse('facilities:settings'), fetch_redirect_response=False)
        f = Facility.objects.get(name='あおば教室')
        u = StaffAccount.objects.get(username='aoba_admin')
        self.assertEqual((u.facility, u.role, f.office_number), (f, 'admin', '1234567890'))
        self.assertTrue(ActivityTag.objects.filter(facility=f).exists())
        # 同名の事業所は登録できない
        with self.settings(ALLOW_FACILITY_SIGNUP=True, SIGNUP_CODE=''):
            self.client.logout()
            res = self.client.post(reverse('accounts:signup'), {
                'facility_name': 'あおば教室', 'display_name': 'x', 'username': 'x2', 'password1': 'aoba-2026!!', 'password2': 'aoba-2026!!'})
            self.assertContains(res, 'すでに登録されています')

    def test_signup_code_required(self):
        with self.settings(ALLOW_FACILITY_SIGNUP=True, SIGNUP_CODE='abc123'):
            res = self.client.get(reverse('accounts:signup'))
            self.assertContains(res, '登録コード')
            data = {'facility_name': 'こもれび', 'display_name': 'x', 'username': 'komo', 'password1': 'komo-2026!!', 'password2': 'komo-2026!!'}
            res = self.client.post(reverse('accounts:signup'), {**data, 'signup_code': 'wrong'})
            self.assertContains(res, '登録コードが正しくありません')
            self.assertFalse(Facility.objects.filter(name='こもれび').exists())
            res = self.client.post(reverse('accounts:signup'), {**data, 'signup_code': 'abc123'})
            self.assertRedirects(res, reverse('facilities:settings'), fetch_redirect_response=False)
            self.assertTrue(Facility.objects.filter(name='こもれび').exists())


class StaffRegisterTests(TestCase):
    """ログイン画面からの職員登録（職員登録コード＋管理者の承認）"""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.f = Facility.objects.create(name='発達支援ルーム　ゆあーず', staff_signup_code='ABCD2345')
        self.admin = StaffAccount.objects.create_user('boss', password='pw12345678', facility=self.f, role=StaffAccount.ROLE_ADMIN)
        self.url = reverse('accounts:register')
        self.data = {'code': 'abcd 2345', 'display_name': '山田 花子', 'username': 'hanako',
                     'password1': 'Kodomo-2026-yu', 'password2': 'Kodomo-2026-yu'}

    def test_login_page_links_to_register(self):
        self.assertContains(self.client.get(reverse('accounts:login')), self.url)

    def test_register_waits_for_approval(self):
        res = self.client.post(self.url, self.data)
        self.assertContains(res, '登録を受け付けました')
        u = StaffAccount.objects.get(username='hanako')
        self.assertEqual((u.facility, u.is_active, u.signup_pending, u.role), (self.f, False, True, StaffAccount.ROLE_STAFF))
        # 承認前はログインできない（承認待ちと分かる）
        res = self.client.post(reverse('accounts:login'), {'username': 'hanako', 'password': 'Kodomo-2026-yu'})
        self.assertContains(res, '管理者の承認を待っています')
        self.assertNotIn('_auth_user_id', self.client.session)
        # パスワードが違えば、ふつうの失敗のまま
        res = self.client.post(reverse('accounts:login'), {'username': 'hanako', 'password': 'wrong-pass-1'})
        self.assertNotContains(res, '承認を待っています')
        # 管理者に知らせが出る → 承認（権限区分を決める）
        self.client.login(username='boss', password='pw12345678')
        res = self.client.get(reverse('accounts:staff'))
        self.assertContains(res, '承認を待っています')
        self.assertContains(res, '山田 花子')
        self.client.post(reverse('accounts:staff_approve', args=[u.pk]), {'role': StaffAccount.ROLE_OFFICE})
        u.refresh_from_db()
        self.assertEqual((u.is_active, u.signup_pending, u.role), (True, False, StaffAccount.ROLE_OFFICE))
        self.assertNotContains(self.client.get(reverse('accounts:staff')), '承認を待っています')
        self.client.logout()
        self.assertTrue(self.client.login(username='hanako', password='Kodomo-2026-yu'))

    def test_wrong_code_and_no_code(self):
        res = self.client.post(self.url, {**self.data, 'code': 'ZZZZ9999'})
        self.assertContains(res, '職員登録コードが違います')
        self.f.staff_signup_code = ''
        self.f.save()
        res = self.client.post(self.url, {**self.data, 'code': ''})
        self.assertFalse(StaffAccount.objects.filter(username='hanako').exists())

    def test_too_many_tries_are_blocked(self):
        for _ in range(10):
            self.client.post(self.url, {**self.data, 'code': 'ZZZZ9999'})
        res = self.client.post(self.url, self.data)
        self.assertContains(res, 'しばらく受け付けを止めています')
        self.assertFalse(StaffAccount.objects.filter(username='hanako').exists())

    def test_reject_deletes_and_only_admin_of_same_facility(self):
        self.client.post(self.url, self.data)
        u = StaffAccount.objects.get(username='hanako')
        other = Facility.objects.create(name='ほか')
        StaffAccount.objects.create_user('ob', password='pw12345678', facility=other, role=StaffAccount.ROLE_ADMIN)
        self.client.login(username='ob', password='pw12345678')
        self.assertEqual(self.client.post(reverse('accounts:staff_approve', args=[u.pk])).status_code, 404)
        StaffAccount.objects.create_user('st', password='pw12345678', facility=self.f, role=StaffAccount.ROLE_STAFF)
        self.client.login(username='st', password='pw12345678')
        self.client.post(reverse('accounts:staff_approve', args=[u.pk]))
        u.refresh_from_db()
        self.assertFalse(u.is_active)                  # 職員は承認できない
        self.client.login(username='boss', password='pw12345678')
        self.client.post(reverse('accounts:staff_approve', args=[u.pk]), {'action': 'reject'})
        self.assertFalse(StaffAccount.objects.filter(username='hanako').exists())

    def test_admin_issues_and_stops_code(self):
        self.client.login(username='boss', password='pw12345678')
        self.client.post(reverse('accounts:signup_code'))
        self.f.refresh_from_db()
        self.assertEqual(len(self.f.staff_signup_code), 8)
        self.assertNotEqual(self.f.staff_signup_code, 'ABCD2345')
        self.assertContains(self.client.get(reverse('accounts:staff')), self.f.staff_signup_code)
        self.client.post(reverse('accounts:signup_code'), {'action': 'off'})
        self.f.refresh_from_db()
        self.assertEqual(self.f.staff_signup_code, '')

    def test_logged_in_user_is_sent_home(self):
        self.client.login(username='boss', password='pw12345678')
        self.assertEqual(self.client.get(self.url).status_code, 302)

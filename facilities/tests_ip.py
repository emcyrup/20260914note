"""事業所ごとの「使える場所」（IP アドレスの制限）と、相手の IP の取り方（2026-11-11）"""
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from accounts import security
from accounts.models import LoginEvent, StaffAccount

from . import ip_rules
from .models import Facility

OFFICE = '203.0.113.10'
HOME = '198.51.100.20'


def at(ip):
    """nginx が付ける X-Real-IP として、送り主の IP を渡す"""
    return {'HTTP_X_REAL_IP': ip}


class ClientIpTests(TestCase):
    def test_sources(self):
        rf = RequestFactory()
        req = rf.get('/', HTTP_X_FORWARDED_FOR='1.1.1.1, 2.2.2.2', HTTP_X_REAL_IP='3.3.3.3', REMOTE_ADDR='127.0.0.1')
        self.assertEqual(security.client_ip(req), '3.3.3.3')                 # 既定：nginx の X-Real-IP
        with override_settings(CLIENT_IP_SOURCE='xff-last'):
            self.assertEqual(security.client_ip(req), '2.2.2.2')             # 先頭（相手が書ける）は使わない
        with override_settings(CLIENT_IP_SOURCE='remote'):
            self.assertEqual(security.client_ip(req), '127.0.0.1')
        self.assertEqual(security.client_ip(rf.get('/', HTTP_X_FORWARDED_FOR='9.9.9.9, 4.4.4.4')), '4.4.4.4')
        self.assertEqual(self.client.get('/healthz/ip/', **at(HOME)).content.decode(), HOME)

    def test_rules(self):
        nets, bad = ip_rules.parse('203.0.113.10  # 事業所\n198.51.100.0/24\nだめ\n\n2001:db8::/32')
        self.assertEqual(bad, ['だめ'])
        self.assertTrue(ip_rules.ip_in(nets, '203.0.113.10'))
        self.assertTrue(ip_rules.ip_in(nets, '198.51.100.77'))
        self.assertTrue(ip_rules.ip_in(nets, '2001:db8::1'))
        self.assertTrue(ip_rules.ip_in(nets, '::ffff:203.0.113.10'))
        self.assertFalse(ip_rules.ip_in(nets, '192.0.2.1'))
        self.assertFalse(ip_rules.ip_in(nets, 'not-an-ip'))


class IpRestrictionTests(TestCase):
    def setUp(self):
        self.f = Facility.objects.create(name='制限のある事業所', allowed_ips=OFFICE)
        self.admin = StaffAccount.objects.create_user('ipadmin', password='pw-12345678', facility=self.f, role=StaffAccount.ROLE_ADMIN)
        self.staff = StaffAccount.objects.create_user('ipstaff', password='pw-12345678', facility=self.f, role=StaffAccount.ROLE_STAFF)

    def login(self, user, ip):
        return self.client.post(reverse('accounts:login'), {'username': user.username, 'password': 'pw-12345678'}, **at(ip))

    def test_login_only_from_allowed_place(self):
        res = self.login(self.staff, HOME)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'この場所からはログインできません')
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertTrue(LoginEvent.objects.filter(kind=LoginEvent.KIND_IP, username='ipstaff').exists())
        res = self.login(self.staff, OFFICE)
        self.assertEqual(res.status_code, 302)
        self.assertIn('_auth_user_id', self.client.session)

    def test_session_is_cut_when_place_changes(self):
        self.client.force_login(self.staff)
        self.assertNotEqual(self.client.get('/', **at(OFFICE)).status_code, 403)
        res = self.client.get('/', **at(HOME))
        self.assertEqual(res.status_code, 403)
        self.assertContains(res, 'この場所からは使えません', status_code=403)
        self.assertNotIn('_auth_user_id', self.client.session)     # ログアウトした
        self.assertEqual(self.client.get('/healthz/', **at(HOME)).status_code, 200)

    def test_admin_exempt_and_superuser(self):
        self.f.ip_admin_exempt = True
        self.f.save()
        self.assertEqual(self.login(self.admin, HOME).status_code, 302)          # 管理者は自宅からも
        self.client.logout()
        self.assertEqual(self.login(self.staff, HOME).status_code, 200)          # 職員はだめ
        su = StaffAccount.objects.create_superuser('ipsu', password='pw-12345678', facility=self.f)
        self.client.force_login(su)
        self.assertNotEqual(self.client.get('/', **at(HOME)).status_code, 403)

    def test_no_rules_means_anywhere(self):
        self.f.allowed_ips = ''
        self.f.save()
        self.assertEqual(self.login(self.staff, HOME).status_code, 302)

    def test_settings_prevent_lockout(self):
        self.f.allowed_ips = ''
        self.f.save()
        self.client.force_login(self.admin)
        url = reverse('facilities:ip_restriction')
        # いまの場所（自宅）が入っていないので保存しない
        self.client.post(url, {'allowed_ips': OFFICE}, **at(HOME))
        self.f.refresh_from_db()
        self.assertEqual(self.f.allowed_ips, '')
        # 読めない行は保存しない
        self.client.post(url, {'allowed_ips': f'{OFFICE}\nabc'}, **at(OFFICE))
        self.f.refresh_from_db()
        self.assertEqual(self.f.allowed_ips, '')
        # いまの場所が入っていれば保存
        self.client.post(url, {'allowed_ips': f'{OFFICE}  # 事業所\n'}, **at(OFFICE))
        self.f.refresh_from_db()
        self.assertEqual(self.f.allowed_ips, f'{OFFICE}  # 事業所')
        # 制限したあとは、自宅からは設定の画面も使えない（止めてログアウト）
        self.client.post(url, {'allowed_ips': '', 'ip_admin_exempt': '1'}, **at(HOME))
        self.f.refresh_from_db()
        self.assertEqual((self.f.allowed_ips, self.f.ip_admin_exempt), (f'{OFFICE}  # 事業所', False))
        # 事業所から「管理者は場所を制限しない」に印 → 管理者は自宅からも
        self.client.force_login(self.admin)
        self.client.post(url, {'allowed_ips': OFFICE, 'ip_admin_exempt': '1'}, **at(OFFICE))
        self.f.refresh_from_db()
        self.assertTrue(self.f.ip_admin_exempt)
        self.assertNotEqual(self.client.get(reverse('facilities:settings'), **at(HOME)).status_code, 403)
        # 職員は設定できない
        self.client.force_login(self.staff)
        self.client.post(url, {'allowed_ips': ''}, **at(OFFICE))
        self.f.refresh_from_db()
        self.assertEqual(self.f.allowed_ips, OFFICE)
        # 設定の画面にいまの場所が出る
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('facilities:settings'), **at(OFFICE)), OFFICE)

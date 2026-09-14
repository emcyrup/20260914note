from django.test import SimpleTestCase, override_settings


class HealthzTests(SimpleTestCase):
    """コンテナのヘルスチェック用エンドポイント"""

    def test_returns_ok_without_login(self):
        res = self.client.get('/healthz/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content, b'ok')

    @override_settings(
        DEBUG=False,
        SECURE_SSL_REDIRECT=True,
        SECURE_REDIRECT_EXEMPT=[r'^healthz/$'],
        SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'),
    )
    def test_not_redirected_to_https_in_production_settings(self):
        # Docker の HEALTHCHECK はコンテナ内から http:// で叩くため、HTTPS リダイレクトの対象外であること
        res = self.client.get('/healthz/')
        self.assertEqual(res.status_code, 200)
        # 通常のパスは従来どおり https へ
        res = self.client.get('/accounts/login/')
        self.assertEqual(res.status_code, 301)
        self.assertTrue(res['Location'].startswith('https://'))


class HttpsSwitchTests(SimpleTestCase):
    """SECURE_SSL_REDIRECT=False（IP検証モード）では Cookie に Secure が付かず HTTP でログインできる"""

    @staticmethod
    def _load_settings(**env):
        """
        settings.py を Django が使っている本体とは別のモジュールとして読み込む。
        環境変数は一時的に上書きし、終わったら元に戻す（.env の有無に依存しない）。
        """
        import importlib.util
        import os
        from pathlib import Path
        from unittest import mock

        path = Path(__file__).resolve().parent / 'settings.py'
        base = {'SECRET_KEY': 'test-only', 'DEBUG': 'False', 'DJANGO_ALLOWED_HOSTS': 'localhost'}
        base.update(env)
        with mock.patch.dict(os.environ, base, clear=False):
            spec = importlib.util.spec_from_file_location('settings_probe', path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        return module

    def test_http_mode_disables_secure_cookies_and_hsts(self):
        s = self._load_settings(SECURE_SSL_REDIRECT='False')
        self.assertFalse(s.SECURE_SSL_REDIRECT)
        self.assertFalse(s.SESSION_COOKIE_SECURE)
        self.assertFalse(s.CSRF_COOKIE_SECURE)
        self.assertFalse(hasattr(s, 'SECURE_HSTS_SECONDS'))

    def test_https_mode_is_the_default(self):
        s = self._load_settings()
        self.assertTrue(s.SECURE_SSL_REDIRECT)
        self.assertTrue(s.SESSION_COOKIE_SECURE)
        self.assertTrue(s.CSRF_COOKIE_SECURE)
        self.assertEqual(s.SECURE_HSTS_SECONDS, 31536000)
        self.assertEqual(s.SECURE_REDIRECT_EXEMPT, [r'^healthz/$'])


class AllowedHostsTests(SimpleTestCase):
    """DJANGO_ALLOWED_HOSTS に無くても、コンテナ内ヘルスチェック用の localhost / 127.0.0.1 は通る"""

    def test_local_hosts_are_always_allowed(self):
        s = HttpsSwitchTests._load_settings(DJANGO_ALLOWED_HOSTS='203.0.113.10')
        self.assertIn('203.0.113.10', s.ALLOWED_HOSTS)
        self.assertIn('127.0.0.1', s.ALLOWED_HOSTS)
        self.assertIn('localhost', s.ALLOWED_HOSTS)

    def test_healthz_accepts_loopback_host(self):
        with self.settings(ALLOWED_HOSTS=['203.0.113.10', '127.0.0.1', 'localhost']):
            res = self.client.get('/healthz/', HTTP_HOST='127.0.0.1:8000')
            self.assertEqual(res.status_code, 200)

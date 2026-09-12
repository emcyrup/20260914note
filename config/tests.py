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

    def test_settings_follow_single_switch(self):
        import importlib, os
        from decouple import config as _config  # noqa: F401
        os.environ['SECRET_KEY'] = 'x'
        os.environ['DEBUG'] = 'False'
        os.environ['SECURE_SSL_REDIRECT'] = 'False'
        import config.settings as s
        importlib.reload(s)
        try:
            self.assertFalse(s.SECURE_SSL_REDIRECT)
            self.assertFalse(s.SESSION_COOKIE_SECURE)
            self.assertFalse(s.CSRF_COOKIE_SECURE)
            self.assertFalse(hasattr(s, 'SECURE_HSTS_SECONDS'))
            os.environ['SECURE_SSL_REDIRECT'] = 'True'
            importlib.reload(s)
            self.assertTrue(s.SESSION_COOKIE_SECURE)
            self.assertEqual(s.SECURE_HSTS_SECONDS, 31536000)
        finally:
            for k in ('SECRET_KEY', 'DEBUG', 'SECURE_SSL_REDIRECT'):
                os.environ.pop(k, None)
            importlib.reload(s)

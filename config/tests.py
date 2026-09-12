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

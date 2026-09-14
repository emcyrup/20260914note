"""LINE 連携：事業所ごとの Webhook、登録コードの期限・回数制限"""
import base64
import hashlib
import hmac
import json
from datetime import date, timedelta
from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from beneficiaries.models import Beneficiary, Guardian
from facilities.models import Facility


def _body(text, user_id='U1'):
    return json.dumps({'destination': 'Ubot', 'events': [{
        'type': 'message', 'mode': 'active', 'timestamp': 1, 'webhookEventId': 'e1',
        'deliveryContext': {'isRedelivery': False}, 'replyToken': 'r1',
        'source': {'type': 'user', 'userId': user_id},
        'message': {'id': 'm1', 'type': 'text', 'text': text, 'quoteToken': 'q'},
    }]})


def _sig(secret, body):
    return base64.b64encode(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()).decode()


class LineWebhookTests(TestCase):
    def setUp(self):
        cache.clear()
        self.a = Facility.objects.create(name='A', line_channel_secret='secret-a', line_channel_access_token='tok-a')
        self.b = Facility.objects.create(name='B', line_channel_secret='secret-b', line_channel_access_token='tok-b')
        ben_a = Beneficiary.objects.create(facility=self.a, last_name='A', first_name='子', date_of_birth=date(2016, 4, 1))
        ben_b = Beneficiary.objects.create(facility=self.b, last_name='B', first_name='子', date_of_birth=date(2016, 4, 1))
        self.ga = Guardian.objects.create(beneficiary=ben_a, last_name='A', first_name='母')
        self.gb = Guardian.objects.create(beneficiary=ben_b, last_name='B', first_name='母')

    def _post(self, facility, secret, text, user_id='U1'):
        body = _body(text, user_id)
        url = reverse('line_integration:webhook_facility', args=[facility.pk])
        with mock.patch('linebot.v3.messaging.MessagingApi.reply_message') as reply:
            res = self.client.post(url, data=body, content_type='application/json', HTTP_X_LINE_SIGNATURE=_sig(secret, body))
        return res, reply

    def test_code_format_and_expiry(self):
        self.assertEqual(len(self.ga.line_registration_code), 8)
        self.assertTrue(self.ga.line_code_valid)
        self.assertNotEqual(self.ga.line_registration_code, self.gb.line_registration_code)

    def test_webhook_is_bound_to_facility_in_url(self):
        # B の保護者のコードを A の Webhook に送っても結びつかない
        res, reply = self._post(self.a, 'secret-a', self.gb.line_registration_code)
        self.assertEqual(res.status_code, 200)
        self.gb.refresh_from_db()
        self.assertFalse(self.gb.line_linked)
        self.assertIn('見つからない', reply.call_args[0][0].messages[0].text)
        # A のコードは A で結びつく（小文字・空白も受け付ける）
        code = self.ga.line_registration_code
        res, reply = self._post(self.a, 'secret-a', code.lower()[:4] + ' ' + code[4:])
        self.ga.refresh_from_db()
        self.assertTrue(self.ga.line_linked)
        self.assertEqual(self.ga.line_user_id, 'U1')
        self.assertNotEqual(self.ga.line_registration_code, code)   # 使ったコードは置き換わる
        self.assertIsNone(self.ga.line_code_expires_at)
        self.assertIn('登録しました', reply.call_args[0][0].messages[0].text)

    def test_signature_uses_that_facility_secret(self):
        res, _ = self._post(self.b, 'secret-a', self.gb.line_registration_code)
        self.assertEqual(res.status_code, 400)
        res, _ = self._post(self.b, 'secret-b', self.gb.line_registration_code)
        self.assertEqual(res.status_code, 200)
        self.gb.refresh_from_db()
        self.assertTrue(self.gb.line_linked)

    def test_expired_code_is_rejected(self):
        self.ga.line_code_expires_at = timezone.now() - timedelta(minutes=1)
        self.ga.save()
        res, reply = self._post(self.a, 'secret-a', self.ga.line_registration_code)
        self.ga.refresh_from_db()
        self.assertFalse(self.ga.line_linked)
        self.assertIn('期限', reply.call_args[0][0].messages[0].text)

    def test_failed_attempts_are_throttled(self):
        for _ in range(5):
            self._post(self.a, 'secret-a', 'WRONG123', user_id='U9')
        res, reply = self._post(self.a, 'secret-a', self.ga.line_registration_code, user_id='U9')
        self.ga.refresh_from_db()
        self.assertFalse(self.ga.line_linked)
        self.assertIn('しばらく', reply.call_args[0][0].messages[0].text)
        # 別の LINE ユーザーには影響しない
        res, reply = self._post(self.a, 'secret-a', self.ga.line_registration_code, user_id='U2')
        self.ga.refresh_from_db()
        self.assertTrue(self.ga.line_linked)

    def test_legacy_url_only_when_single_facility_configured(self):
        body = _body(self.ga.line_registration_code)
        with mock.patch('linebot.v3.messaging.MessagingApi.reply_message'):
            res = self.client.post(reverse('line_integration:webhook'), data=body, content_type='application/json',
                                   HTTP_X_LINE_SIGNATURE=_sig('secret-a', body))
        self.assertEqual(res.status_code, 200)
        self.ga.refresh_from_db()
        self.assertFalse(self.ga.line_linked)   # 2事業所が設定済みなので旧 URL では何もしない
        self.b.line_channel_secret = ''
        self.b.save()
        with mock.patch('linebot.v3.messaging.MessagingApi.reply_message'):
            res = self.client.post(reverse('line_integration:webhook'), data=body, content_type='application/json',
                                   HTTP_X_LINE_SIGNATURE=_sig('secret-a', body))
        self.ga.refresh_from_db()
        self.assertTrue(self.ga.line_linked)

    def test_regenerate_view_issues_new_code(self):
        from accounts.models import StaffAccount
        u = StaffAccount.objects.create_user('ua', password='pass12345', facility=self.a)
        self.client.force_login(u)
        old = self.ga.line_registration_code
        self.client.post(reverse('beneficiaries:regenerate_line_code', args=[self.ga.pk]))
        self.ga.refresh_from_db()
        self.assertNotEqual(self.ga.line_registration_code, old)
        self.assertTrue(self.ga.line_code_valid)
        # 他事業所の保護者は再発行できない
        self.assertEqual(self.client.post(reverse('beneficiaries:regenerate_line_code', args=[self.gb.pk])).status_code, 404)

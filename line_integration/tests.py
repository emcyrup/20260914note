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


def _body(text, user_id='U1', source=None, message_id='m1'):
    return json.dumps({'destination': 'Ubot', 'events': [{
        'type': 'message', 'mode': 'active', 'timestamp': 1, 'webhookEventId': 'e1',
        'deliveryContext': {'isRedelivery': False}, 'replyToken': 'r1',
        'source': source or {'type': 'user', 'userId': user_id},
        'message': {'id': message_id, 'type': 'text', 'text': text, 'quoteToken': 'q'},
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


class WebhookInboxTests(TestCase):
    """予約管理を使う事業所では、登録コード以外の文を受信箱へ積む（自動では反映しない）"""

    def setUp(self):
        cache.clear()
        self.f = Facility.objects.create(name='なゆた', line_channel_secret='secret-a',
                                         line_channel_access_token='tok-a', use_reservation=True)
        ben = Beneficiary.objects.create(facility=self.f, last_name='青木', first_name='はると',
                                         date_of_birth=date(2016, 4, 1))
        self.guardian = Guardian.objects.create(beneficiary=ben, last_name='青木', first_name='花子')

    def _post(self, text, **kw):
        body = _body(text, **kw)
        url = reverse('line_integration:webhook_facility', args=[self.f.pk])
        with mock.patch('linebot.v3.messaging.MessagingApi.reply_message') as reply:
            res = self.client.post(url, data=body, content_type='application/json',
                                   HTTP_X_LINE_SIGNATURE=_sig('secret-a', body))
        return res, reply

    def test_reservation_text_goes_to_inbox(self):
        from reservations.models import LineInbox
        res, reply = self._post('10/1 予約おねがいします')
        self.assertEqual(res.status_code, 200)
        entry = LineInbox.objects.get(facility=self.f)
        self.assertEqual((entry.text, entry.source_type, entry.line_user_id),
                         ('10/1 予約おねがいします', 'user', 'U1'))
        self.assertEqual(entry.status, LineInbox.STATUS_PENDING)
        self.assertIn('承りました', reply.call_args[0][0].messages[0].text)

    def test_link_code_still_works_and_linked_user_is_inboxed(self):
        from reservations.models import LineInbox
        res, reply = self._post(self.guardian.line_registration_code)
        self.guardian.refresh_from_db()
        self.assertTrue(self.guardian.line_linked)
        self.assertEqual(LineInbox.objects.count(), 0)
        # つないだ保護者は顧客台帳にも入るので、予約の文は事業所の決まりどおりに扱う
        # （自動で確定する設定なら予約になり、そうでなければ受信箱に積む）
        from reservations.models import Customer, Reservation
        self.assertTrue(Customer.objects.filter(facility=self.f, line_user_id='U1').exists())
        res, reply = self._post('10/2 予約', message_id='m2')
        self.assertEqual(LineInbox.objects.count() + Reservation.objects.count(), 1)

    def test_group_message_is_inboxed_with_group_id(self):
        from reservations.models import LineInbox
        self._post('9/12 Aさん ×', source={'type': 'group', 'groupId': 'G1', 'userId': 'U5'})
        entry = LineInbox.objects.get(facility=self.f)
        self.assertEqual((entry.source_type, entry.group_id), ('group', 'G1'))

    def test_without_the_feature_nothing_is_inboxed(self):
        from reservations.models import LineInbox
        self.f.use_reservation = False
        self.f.save()
        res, reply = self._post('10/1 予約おねがいします')
        self.assertEqual(LineInbox.objects.count(), 0)
        self.assertIn('見つからない', reply.call_args[0][0].messages[0].text)



class CustomerLineLinkTests(TestCase):
    """登録コードで LINE をつなぐと、予約の顧客台帳にも同じ LINE が付く（お知らせが LINE で届くように）"""

    def setUp(self):
        cache.clear()
        self.f = Facility.objects.create(name='ゆあーず', line_channel_secret='secret-a',
                                         line_channel_access_token='tok-a', use_reservation=True)
        self.kid = Beneficiary.objects.create(facility=self.f, last_name='青木', first_name='はると',
                                              date_of_birth=date(2016, 4, 1))
        self.sister = Beneficiary.objects.create(facility=self.f, last_name='青木', first_name='ゆい',
                                                 date_of_birth=date(2018, 4, 1))
        self.g1 = Guardian.objects.create(beneficiary=self.kid, last_name='青木', first_name='花子', phone='090-1')
        self.g2 = Guardian.objects.create(beneficiary=self.sister, last_name='青木', first_name='花子')

    def _post(self, text, message_id='m1'):
        body = _body(text, user_id='U-aoki', message_id=message_id)
        url = reverse('line_integration:webhook_facility', args=[self.f.pk])
        with mock.patch('linebot.v3.messaging.MessagingApi.reply_message') as reply:
            self.client.post(url, data=body, content_type='application/json', HTTP_X_LINE_SIGNATURE=_sig('secret-a', body))
        return reply.call_args[0][0].messages[0].text

    def test_code_links_existing_customer_and_sibling(self):
        from reservations.models import Customer
        # 利用希望のお願いのときに作った、LINE の無い顧客
        other = Customer.objects.create(facility=self.f, name='青木 父')
        other.children.add(self.kid)
        mom = Customer.objects.create(facility=self.f, name='青木　花子')
        mom.children.add(self.kid)
        self._post(self.g1.line_registration_code)
        mom.refresh_from_db(); other.refresh_from_db()
        self.assertEqual((mom.line_user_id, other.line_user_id), ('U-aoki', ''))     # 名前が同じ人に付ける
        # きょうだいのコードを、つないだあとで送る → その子の保護者にもなり、同じ顧客の担当に足す
        text = self._post(self.g2.line_registration_code, message_id='m2')
        self.assertIn('青木 ゆい様の保護者としても登録しました', text)
        self.g2.refresh_from_db()
        self.assertTrue(self.g2.line_linked)
        self.assertEqual(set(mom.children.all()), {self.kid, self.sister})
        self.assertEqual(Customer.objects.filter(line_user_id='U-aoki').count(), 1)

    def test_code_creates_customer_when_none(self):
        from reservations.models import Customer
        self._post(self.g1.line_registration_code)
        c = Customer.objects.get(line_user_id='U-aoki')
        self.assertEqual((c.name, c.phone, list(c.children.all())), ('青木 花子', '090-1', [self.kid]))

    def test_without_reservation_feature_no_customer(self):
        from reservations.models import Customer
        self.f.use_reservation = False
        self.f.save()
        self._post(self.g1.line_registration_code)
        self.assertFalse(Customer.objects.exists())

    def test_inbox_sender_can_be_linked_to_existing_customer(self):
        from accounts.models import StaffAccount
        from reservations.models import Customer, LineInbox
        staff = StaffAccount.objects.create_user('st', password='pw12345678', facility=self.f, role=StaffAccount.ROLE_ADMIN)
        self.client.force_login(staff)
        c = Customer.objects.create(facility=self.f, name='青木 花子')
        c.children.add(self.kid)
        entry = LineInbox.objects.create(facility=self.f, message_id='x1', text='こんにちは', line_user_id='U-new')
        page = self.client.get(reverse('reservations:line'))
        self.assertContains(page, '顧客台帳の人につなぐ')
        self.client.post(reverse('reservations:line'), {'action': 'link', 'entry': entry.pk, 'customer': c.pk})
        c.refresh_from_db()
        self.assertEqual(c.line_user_id, 'U-new')
        # 同じ LINE をほかの人にはつながない
        c2 = Customer.objects.create(facility=self.f, name='別の人')
        res = self.client.post(reverse('reservations:line'), {'action': 'link', 'entry': entry.pk, 'customer': c2.pk}, follow=True)
        self.assertContains(res, 'すでに「青木 花子」さんにつながっています')
        c2.refresh_from_db()
        self.assertEqual(c2.line_user_id, '')

class WebhookAutoApplyTests(TestCase):
    """LINE の投稿から直接、予約を入れる・取り消す（顧客は公式LINE、職員はグループ）"""

    def setUp(self):
        cache.clear()
        from reservations.models import Customer
        from reservations.services import get_setting
        self.f = Facility.objects.create(name='なゆた', line_channel_secret='secret-a',
                                         line_channel_access_token='tok-a', use_reservation=True)
        self.ben = Beneficiary.objects.create(facility=self.f, last_name='青木', first_name='はると',
                                              date_of_birth=date(2016, 4, 1))
        self.customer = Customer.objects.create(facility=self.f, name='青木 花子', line_user_id='U1')
        self.customer.children.add(self.ben)
        self.setting = get_setting(self.f)
        self.setting.notify_group_id = 'G1'   # 受け方は既定（来た順に自動で確定）
        self.setting.save()
        self.day = date.today() + timedelta(days=7)

    def _post(self, text, **kw):
        body = _body(text, **kw)
        url = reverse('line_integration:webhook_facility', args=[self.f.pk])
        with mock.patch('linebot.v3.messaging.MessagingApi.reply_message') as reply:
            res = self.client.post(url, data=body, content_type='application/json',
                                   HTTP_X_LINE_SIGNATURE=_sig('secret-a', body))
        return res, reply

    def _text(self, reply):
        return reply.call_args[0][0].messages[0].text

    def test_customer_message_books_and_cancels(self):
        from reservations.models import LineInbox, Reservation
        res, reply = self._post(f'{self.day.month}/{self.day.day} 予約おねがいします')
        self.assertEqual(res.status_code, 200)
        booked = Reservation.objects.get(facility=self.f, date=self.day)
        self.assertEqual((booked.status, booked.source, booked.customer),
                         (Reservation.STATUS_CONFIRMED, Reservation.SOURCE_LINE, self.customer))
        self.assertIn('承りました', self._text(reply))
        self.assertEqual(LineInbox.objects.count(), 0)   # 反映できた文は受信箱に積まない

        res, reply = self._post(f'{self.day.month}/{self.day.day} キャンセルします', message_id='m2')
        booked.refresh_from_db()
        self.assertEqual(booked.status, Reservation.STATUS_CANCELLED)
        self.assertIn('取り消しました', self._text(reply))

    def test_customer_message_is_inboxed_in_approve_mode(self):
        from reservations.models import LineInbox, Reservation, ReservationSetting
        self.setting.booking_mode = ReservationSetting.MODE_APPROVE
        self.setting.save()
        res, reply = self._post(f'{self.day.month}/{self.day.day} 予約おねがいします')
        self.assertEqual(Reservation.objects.count(), 0)
        self.assertEqual(LineInbox.objects.count(), 1)
        self.assertIn('承りました', self._text(reply))

    def test_unreadable_customer_message_is_inboxed(self):
        from reservations.models import LineInbox, Reservation
        res, reply = self._post('こんにちは。おせわになっています。')
        self.assertEqual(Reservation.objects.count(), 0)
        self.assertEqual(LineInbox.objects.count(), 1)

    def test_unknown_line_user_is_inboxed(self):
        from reservations.models import LineInbox, Reservation
        self.customer.line_user_id = ''
        self.customer.save()
        self._post(f'{self.day.month}/{self.day.day} 予約おねがいします')
        self.assertEqual(Reservation.objects.count(), 0)
        self.assertEqual(LineInbox.objects.count(), 1)

    def test_staff_group_books_with_a_name(self):
        from reservations.models import LineInbox, Reservation
        res, reply = self._post(f'{self.day.month}/{self.day.day} 青木はると 予約',
                                source={'type': 'group', 'groupId': 'G1', 'userId': 'U9'})
        booked = Reservation.objects.get(facility=self.f, date=self.day)
        self.assertEqual((booked.status, booked.source),
                         (Reservation.STATUS_CONFIRMED, Reservation.SOURCE_GROUP))
        self.assertIn('承りました', self._text(reply))
        self.assertEqual(LineInbox.objects.count(), 0)

    def test_another_group_is_only_inboxed(self):
        from reservations.models import LineInbox, Reservation
        self._post(f'{self.day.month}/{self.day.day} 青木はると 予約',
                   source={'type': 'group', 'groupId': 'G-other', 'userId': 'U9'})
        self.assertEqual(Reservation.objects.count(), 0)
        entry = LineInbox.objects.get(facility=self.f)
        self.assertEqual(entry.group_id, 'G-other')

    def test_group_without_a_name_is_inboxed(self):
        from reservations.models import LineInbox, Reservation
        self._post(f'{self.day.month}/{self.day.day} 予約おねがいします',
                   source={'type': 'group', 'groupId': 'G1', 'userId': 'U9'})
        self.assertEqual(Reservation.objects.count(), 0)
        self.assertEqual(LineInbox.objects.count(), 1)


class WebhookPageGuideTests(TestCase):
    """「予約」と送られたら、顧客向け予定表のアドレスを返す"""

    def setUp(self):
        cache.clear()
        from reservations.models import Customer
        from reservations.services import get_setting
        self.f = Facility.objects.create(name='なゆた', line_channel_secret='secret-a',
                                         line_channel_access_token='tok-a', use_reservation=True)
        self.ben = Beneficiary.objects.create(facility=self.f, last_name='青木', first_name='はると',
                                              date_of_birth=date(2016, 4, 1))
        self.customer = Customer.objects.create(facility=self.f, name='青木 花子', line_user_id='U1')
        self.customer.children.add(self.ben)
        self.setting = get_setting(self.f)

    def _post(self, text, **kw):
        body = _body(text, **kw)
        url = reverse('line_integration:webhook_facility', args=[self.f.pk])
        with mock.patch('linebot.v3.messaging.MessagingApi.reply_message') as reply:
            self.client.post(url, data=body, content_type='application/json',
                             HTTP_X_LINE_SIGNATURE=_sig('secret-a', body))
        return reply.call_args[0][0].messages[0].text

    def test_a_known_customer_gets_their_own_page(self):
        from reservations.models import LineInbox
        text = self._post('予約')
        self.assertIn(f'/yoyaku/mypage/{self.customer.token}/', text)
        self.assertIn('なゆた', text)
        self.assertEqual(LineInbox.objects.count(), 0)   # 職員の手を煩わせない

    def test_a_first_time_sender_gets_the_vacancy_page(self):
        from reservations.models import LineInbox
        text = self._post('予約', user_id='U-new')
        self.assertIn(f'/yoyaku/aki/{self.setting.public_token}/', text)
        self.assertEqual(LineInbox.objects.count(), 0)

    def test_asking_about_vacancies_also_gets_the_page(self):
        self.assertIn('/yoyaku/mypage/', self._post('空いてますか？'))

    def test_a_message_with_a_date_books_right_away(self):
        from reservations.models import LineInbox, Reservation
        day = date.today() + timedelta(days=7)
        text = self._post(f'{day.month}/{day.day} 予約おねがいします')
        self.assertIn('ご予約を承りました', text)
        self.assertIn('/yoyaku/mypage/', text)     # 確認・取り消しのページも案内する
        self.assertEqual(Reservation.objects.filter(facility=self.f, date=day).count(), 1)
        self.assertEqual(LineInbox.objects.count(), 0)

    def test_a_message_with_a_date_waits_for_staff_in_approve_mode(self):
        from reservations.models import LineInbox, Reservation, ReservationSetting
        setting = self.setting
        setting.booking_mode = ReservationSetting.MODE_APPROVE
        setting.save()
        day = date.today() + timedelta(days=7)
        text = self._post(f'{day.month}/{day.day} 予約おねがいします')
        self.assertIn('承りました', text)          # 職員が確かめる
        self.assertIn('/yoyaku/mypage/', text)
        self.assertEqual(Reservation.objects.count(), 0)
        self.assertEqual(LineInbox.objects.count(), 1)

    def test_other_messages_are_unchanged(self):
        from reservations.models import LineInbox
        from .views import LineWebhookView
        text = self._post('いつもありがとうございます')
        self.assertEqual(text, LineWebhookView.RECEIVED_REPLY)   # これまでどおりの文面
        self.assertNotIn('/yoyaku/', text)
        self.assertEqual(LineInbox.objects.count(), 1)

    def test_no_page_when_the_vacancy_page_is_closed(self):
        from reservations.models import LineInbox
        self.setting.public_calendar = False
        self.setting.save()
        text = self._post('予約', user_id='U-new')
        self.assertNotIn('/yoyaku/', text)
        self.assertIn('承りました', text)
        self.assertEqual(LineInbox.objects.count(), 1)

    def test_a_first_time_sender_asking_to_cancel_goes_to_staff(self):
        from reservations.models import LineInbox
        text = self._post('キャンセルしたいです', user_id='U-new')
        self.assertNotIn('/yoyaku/', text)
        self.assertEqual(LineInbox.objects.count(), 1)

    def test_the_site_url_setting_wins_over_the_webhook_host(self):
        with self.settings(RESERVATION_SITE_URL='https://yoyaku.example.jp'):
            self.assertIn(f'https://yoyaku.example.jp/yoyaku/mypage/{self.customer.token}/',
                          self._post('予約'))

"""予約管理（なゆた由来）のテスト"""
import datetime
from unittest import mock

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import StaffAccount
from beneficiaries.models import Beneficiary
from facilities.models import Facility

from . import services
from .models import ClosedDate, Customer, LineInbox, Reservation, ReservationNotice

D = datetime.timedelta


def child(facility, last, first='子'):
    return Beneficiary.objects.create(facility=facility, last_name=last, first_name=first,
                                      last_name_kana='てすと', date_of_birth=datetime.date(2016, 4, 1))


class ReservationLogicTests(TestCase):
    def setUp(self):
        self.f = Facility.objects.create(name='なゆた', use_reservation=True)
        self.setting = services.get_setting(self.f)
        self.setting.capacity = 2
        self.setting.signature = 'なゆた'
        self.setting.save()
        self.day = datetime.date(2026, 10, 1)   # 木曜
        self.a, self.b, self.c = child(self.f, '青木'), child(self.f, '井上'), child(self.f, '上田')

    def test_capacity_and_confirm(self):
        res, notice = services.create_reservation(self.f, self.a, self.day)
        self.assertEqual(res.status, Reservation.STATUS_CONFIRMED)
        self.assertEqual(notice.kind, ReservationNotice.KIND_ACCEPTED)
        st = services.day_state(self.f, self.day)
        self.assertEqual((st['capacity'], st['confirmed'], st['remaining'], st['full']), (2, 1, 1, False))

    def test_full_goes_to_waitlist_then_promotes_in_order(self):
        r1, _ = services.create_reservation(self.f, self.a, self.day)
        services.create_reservation(self.f, self.b, self.day)
        self.assertTrue(services.day_state(self.f, self.day)['full'])
        r3, n3 = services.create_reservation(self.f, self.c, self.day)
        self.assertEqual(r3.status, Reservation.STATUS_WAITLIST)
        self.assertEqual(n3.kind, ReservationNotice.KIND_WAITLISTED)
        # 取消で繰り上げ
        promoted = services.cancel_reservation(r1)
        self.assertEqual([p.pk for p in promoted], [r3.pk])
        r3.refresh_from_db()
        self.assertEqual(r3.status, Reservation.STATUS_CONFIRMED)
        self.assertTrue(ReservationNotice.objects.filter(kind=ReservationNotice.KIND_PROMOTED,
                                                         reservation=r3).exists())

    def test_declined_when_waitlist_is_off(self):
        self.setting.allow_waitlist = False
        self.setting.save()
        services.create_reservation(self.f, self.a, self.day)
        services.create_reservation(self.f, self.b, self.day)
        res, notice = services.create_reservation(self.f, self.c, self.day)
        self.assertEqual(res.status, Reservation.STATUS_DECLINED)
        self.assertEqual(notice.kind, ReservationNotice.KIND_DECLINED)
        self.assertEqual(services.day_state(self.f, self.day)['confirmed'], 2)

    def test_double_booking_and_closed_day_are_refused(self):
        services.create_reservation(self.f, self.a, self.day)
        with self.assertRaises(services.ReservationError):
            services.create_reservation(self.f, self.a, self.day)
        # 取消のあとは取り直せる
        services.cancel_reservation(Reservation.objects.get(beneficiary=self.a, date=self.day,
                                                            status=Reservation.STATUS_CONFIRMED))
        again, _ = services.create_reservation(self.f, self.a, self.day)
        self.assertEqual(again.status, Reservation.STATUS_CONFIRMED)
        # 休業曜日・臨時休業日
        self.setting.closed_weekdays = [self.day.weekday()]
        self.setting.save()
        with self.assertRaises(services.ReservationError):
            services.create_reservation(self.f, self.b, self.day)
        self.setting.closed_weekdays = []
        self.setting.save()
        ClosedDate.objects.create(facility=self.f, date=self.day)
        self.assertEqual(services.day_state(self.f, self.day)['capacity'], 0)
        with self.assertRaises(services.ReservationError):
            services.create_reservation(self.f, self.b, self.day)

    def test_vacancy_notice_only_when_full_and_not_promoted(self):
        waiting = Customer.objects.create(facility=self.f, name='待ちの人', line_user_id='U-wait')
        booked = Customer.objects.create(facility=self.f, name='予約ずみの人', line_user_id='U-booked')
        booked.children.add(self.b)
        r1, _ = services.create_reservation(self.f, self.a, self.day)
        services.create_reservation(self.f, self.b, self.day)   # 満枠
        services.cancel_reservation(r1)
        kinds = ReservationNotice.objects.filter(kind=ReservationNotice.KIND_VACANCY)
        self.assertEqual([n.customer_id for n in kinds], [waiting.pk])   # その日に予約のある人には出さない
        self.assertIn('空きが出ました', kinds.first().body)
        self.assertIn('なゆた', kinds.first().body)
        # 2回目は重ねない
        services.offer_vacancy(self.f, self.day)
        self.assertEqual(ReservationNotice.objects.filter(kind=ReservationNotice.KIND_VACANCY).count(), 1)

    def test_group_notice_has_no_personal_contact(self):
        self.setting.notify_group_id = 'G-staff'
        self.setting.save()
        services.create_reservation(self.f, self.a, self.day)
        n = ReservationNotice.objects.get(kind=ReservationNotice.KIND_GROUP)
        self.assertIn('【予約の増減】', n.body)
        self.assertIn('青木 子', n.body)
        self.assertIn('予約 1/2名・残り1枠', n.body)
        self.assertEqual(n.to_line_id, 'G-staff')

    def test_notice_without_line_is_kept_for_hand_delivery(self):
        Customer.objects.create(facility=self.f, name='未連携').children.add(self.a)
        _, notice = services.create_reservation(self.f, self.a, self.day)
        self.assertEqual(notice.status, ReservationNotice.STATUS_MANUAL)

    def test_reminders(self):
        c = Customer.objects.create(facility=self.f, name='母', line_user_id='U1')
        c.children.add(self.a)
        services.create_reservation(self.f, self.a, self.day)
        made = services.queue_reminders(self.f, self.day)
        self.assertEqual(len(made), 1)
        self.assertIn('ご利用日です', made[0].body)
        self.assertEqual(len(services.queue_reminders(self.f, self.day)), 0)   # 二重に積まない


class MessageParsingTests(TestCase):
    def setUp(self):
        self.f = Facility.objects.create(name='なゆた', use_reservation=True)
        self.today = datetime.date(2026, 10, 1)
        self.a = child(self.f, '青木', 'はると')

    def test_intent_and_date(self):
        p = services.parse_message('10/12 予約おねがいします', self.today)
        self.assertEqual((p['intent'], p['date']), ('reserve', datetime.date(2026, 10, 12)))
        p = services.parse_message('10月5日はキャンセルします', self.today)
        self.assertEqual((p['intent'], p['date']), ('cancel', datetime.date(2026, 10, 5)))
        p = services.parse_message('明日 予約', self.today)
        self.assertEqual(p['date'], datetime.date(2026, 10, 2))
        p = services.parse_message('こんにちは', self.today)
        self.assertEqual((p['intent'], p['date']), ('unknown', None))
        # 年をまたぐ指定は翌年として読む
        p = services.parse_message('1/5 予約', datetime.date(2026, 12, 20))
        self.assertEqual(p['date'], datetime.date(2027, 1, 5))

    def test_register_form(self):
        p = services.parse_message('【登録】\nお名前: 青木 花子\nふりがな: あおき はなこ\n電話番号: 090-0000-0000')
        self.assertEqual(p['intent'], 'register')
        self.assertEqual(p['name'], '青木 花子')
        self.assertEqual(p['fields']['電話番号'], '090-0000-0000')

    def test_guess_beneficiary(self):
        self.assertEqual(services.guess_beneficiary(self.f, '青木はると 10/1 予約'), self.a)
        c = Customer.objects.create(facility=self.f, name='母')
        c.children.add(self.a)
        self.assertEqual(services.guess_beneficiary(self.f, '10/1 予約', c), self.a)   # 担当が1人なら決まる
        c.children.add(child(self.f, '井上'))
        self.assertIsNone(services.guess_beneficiary(self.f, '10/1 予約', c))          # 2人なら決めない


class ReservationScreenTests(TestCase):
    def setUp(self):
        self.f = Facility.objects.create(name='なゆた', use_reservation=True)
        self.other = Facility.objects.create(name='標準の事業所')
        self.user = StaffAccount.objects.create_user('u', password='pass12345', facility=self.f,
                                                     role=StaffAccount.ROLE_ADMIN)
        self.a = child(self.f, '青木')
        self.day = datetime.date(2026, 10, 1)
        self.client.force_login(self.user)

    def test_hidden_when_feature_is_off(self):
        u = StaffAccount.objects.create_user('o', password='pass12345', facility=self.other)
        self.client.force_login(u)
        res = self.client.get(reverse('facilities:dashboard'))
        self.assertNotContains(res, 'reservations')
        self.assertRedirects(self.client.get(reverse('reservations:calendar')), reverse('facilities:dashboard'))

    def test_calendar_and_sidebar(self):
        res = self.client.get(reverse('facilities:dashboard'))
        self.assertContains(res, reverse('reservations:calendar'))
        res = self.client.get(reverse('reservations:calendar_month', args=[2026, 10]))
        self.assertContains(res, '残 10')
        self.assertContains(res, '1日の枠')

    def test_settings_admin_only(self):
        staff = StaffAccount.objects.create_user('s', password='pass12345', facility=self.f)
        self.client.force_login(staff)
        self.client.post(reverse('reservations:settings'), {'capacity': 5})
        self.assertEqual(services.get_setting(self.f).capacity, 10)
        self.client.force_login(self.user)
        self.client.post(reverse('reservations:settings'), {'capacity': 5, 'allow_waitlist': 'on',
                                                            'closed_weekdays': ['6'], 'signature': 'なゆた'})
        setting = services.get_setting(self.f)
        self.assertEqual((setting.capacity, setting.closed_weekdays, setting.signature), (5, [6], 'なゆた'))
        self.client.post(reverse('reservations:settings'), {'capacity': 0})
        self.assertEqual(services.get_setting(self.f).capacity, 5)

    def test_day_add_cancel_close(self):
        url = reverse('reservations:day', args=[2026, 10, 1])
        res = self.client.post(url, {'action': 'add', 'beneficiary': self.a.pk, 'note': 'きょうだいと'})
        self.assertEqual(res.status_code, 302)
        r = Reservation.objects.get(beneficiary=self.a, date=self.day)
        self.assertEqual((r.status, r.note), (Reservation.STATUS_CONFIRMED, 'きょうだいと'))
        res = self.client.get(url)
        self.assertContains(res, '青木 子')
        self.assertContains(res, '残り 9 枠')
        self.client.post(url, {'action': 'close', 'reason': '研修'})
        self.assertTrue(ClosedDate.objects.filter(facility=self.f, date=self.day).exists())
        res = self.client.get(url)
        self.assertContains(res, '休業日')
        self.client.post(url, {'action': 'open'})
        self.client.post(url, {'action': 'cancel', 'reservation': r.pk})
        r.refresh_from_db()
        self.assertEqual(r.status, Reservation.STATUS_CANCELLED)

    def test_other_facility_reservation_is_404(self):
        other_child = child(self.other, '別')
        res = Reservation.objects.create(facility=self.other, beneficiary=other_child, date=self.day)
        url = reverse('reservations:day', args=[2026, 10, 1])
        self.assertEqual(self.client.post(url, {'action': 'cancel', 'reservation': res.pk}).status_code, 404)
        self.client.post(url, {'action': 'add', 'beneficiary': other_child.pk})
        self.assertFalse(Reservation.objects.filter(beneficiary=other_child, source='staff',
                                                    date=self.day, status='confirmed').exclude(pk=res.pk).exists())

    def test_customers(self):
        res = self.client.post(reverse('reservations:customers'), {
            'name': '青木 花子', 'kana': 'あおき', 'phone': '090', 'line_user_id': 'U1',
            'notify_enabled': 'on', 'children': [self.a.pk]})
        self.assertRedirects(res, reverse('reservations:customers'))
        c = Customer.objects.get(name='青木 花子')
        self.assertEqual(list(c.children.all()), [self.a])
        self.assertTrue(c.can_notify)
        # 同じ LINE ID は二人目に付けられない
        self.client.post(reverse('reservations:customers'), {'name': '別の人', 'line_user_id': 'U1'})
        self.assertFalse(Customer.objects.filter(name='別の人').exists())
        res = self.client.get(reverse('reservations:customers'))
        self.assertContains(res, '青木 花子')
        self.client.post(reverse('reservations:customer_delete', args=[c.pk]))
        self.assertFalse(Customer.objects.filter(pk=c.pk).exists())

    def test_csv(self):
        services.create_reservation(self.f, self.a, self.day)
        res = self.client.get(reverse('reservations:csv', args=[2026, 10]))
        body = res.content.decode('utf-8-sig')
        self.assertIn('青木 子', body)
        self.assertIn('2026-10-01', body)
        self.assertTrue(res['Content-Disposition'].endswith('reservations_202610.csv"'))


class LineInboxTests(TestCase):
    def setUp(self):
        self.f = Facility.objects.create(name='なゆた', use_reservation=True,
                                         line_channel_access_token='tok', line_channel_secret='sec')
        self.user = StaffAccount.objects.create_user('u', password='pass12345', facility=self.f,
                                                     role=StaffAccount.ROLE_ADMIN)
        self.a = child(self.f, '青木', 'はると')
        self.customer = Customer.objects.create(facility=self.f, name='青木 花子', line_user_id='U1')
        self.customer.children.add(self.a)
        self.client.force_login(self.user)
        self.day = datetime.date(2026, 10, 1)

    def _inbox(self, text, message_id='m1', **kw):
        entry, _ = services.receive(self.f, message_id, text, line_user_id=kw.pop('line_user_id', 'U1'), **kw)
        return entry

    def test_apply_creates_reservation_and_redacts(self):
        entry = self._inbox('10/1 予約おねがいします')
        res = self.client.get(reverse('reservations:line'))
        self.assertContains(res, '10/1 予約おねがいします')
        self.assertContains(res, '青木 はると')
        self.client.post(reverse('reservations:line'), {
            'action': 'apply', 'entry': entry.pk, 'date': '2026-10-01',
            'beneficiary': self.a.pk, 'intent': 'reserve'})
        r = Reservation.objects.get(beneficiary=self.a, date=self.day)
        self.assertEqual((r.status, r.source, r.customer), (Reservation.STATUS_CONFIRMED, 'line', self.customer))
        entry.refresh_from_db()
        self.assertEqual(entry.status, LineInbox.STATUS_DONE)
        self.assertEqual(entry.text, '')          # 本文はその場で消す
        self.assertIn('青木 はると', entry.result_note)

    def test_apply_without_date_or_child_does_nothing(self):
        entry = self._inbox('予約おねがいします')
        self.client.post(reverse('reservations:line'), {'action': 'apply', 'entry': entry.pk,
                                                        'beneficiary': self.a.pk})
        self.assertFalse(Reservation.objects.exists())
        entry.refresh_from_db()
        self.assertEqual(entry.status, LineInbox.STATUS_PENDING)

    def test_register_customer_from_form(self):
        entry = self._inbox('【登録】\nお名前: 井上 母\n電話番号: 090-1111', line_user_id='U9')
        self.client.post(reverse('reservations:line'), {'action': 'register', 'entry': entry.pk, 'name': '井上 母'})
        c = Customer.objects.get(line_user_id='U9')
        self.assertEqual((c.name, c.phone), ('井上 母', '090-1111'))
        self.assertEqual(list(c.children.all()), [])   # 担当は自動で結びつけない

    def test_ignore_and_group_target(self):
        entry = self._inbox('よろしくおねがいします', 'm2')
        self.client.post(reverse('reservations:line'), {'action': 'ignore', 'entry': entry.pk})
        entry.refresh_from_db()
        self.assertEqual((entry.status, entry.text), (LineInbox.STATUS_IGNORED, ''))
        g = self._inbox('明日は雨ですね', 'm3', source_type=LineInbox.SOURCE_GROUP, group_id='G1', line_user_id='U5')
        self.client.post(reverse('reservations:line'), {'action': 'set_group', 'entry': g.pk})
        self.assertEqual(services.get_setting(self.f).notify_group_id, 'G1')

    def test_send_pending_notices(self):
        services.create_reservation(self.f, self.a, self.day)
        with mock.patch('linebot.v3.messaging.MessagingApi.push_message') as push:
            self.client.post(reverse('reservations:line'), {'action': 'send'})
        self.assertEqual(push.call_count, 1)
        n = ReservationNotice.objects.get(kind=ReservationNotice.KIND_ACCEPTED)
        self.assertEqual(n.status, ReservationNotice.STATUS_SENT)
        self.assertIsNotNone(n.sent_at)

    def test_receive_is_idempotent_and_scoped(self):
        self._inbox('10/1 予約', 'same')
        self._inbox('10/1 予約', 'same')
        self.assertEqual(LineInbox.objects.filter(facility=self.f).count(), 1)
        other = Facility.objects.create(name='別', use_reservation=True)
        u = StaffAccount.objects.create_user('o', password='pass12345', facility=other)
        self.client.force_login(u)
        res = self.client.get(reverse('reservations:line'))
        self.assertNotContains(res, '10/1 予約')

"""予約管理（なゆた由来）のテスト"""
import datetime
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import StaffAccount
from beneficiaries.models import Beneficiary
from facilities.models import Facility

from . import services
from .models import (BookingRequest, ClosedDate, Customer, LineInbox, Reservation, ReservationNotice,
                     ReservationSetting)

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


class ReservationEditTests(TestCase):
    """職員が予約を「変える・消す」"""

    def setUp(self):
        self.f = Facility.objects.create(name='なゆた', use_reservation=True)
        self.setting = services.get_setting(self.f)
        self.setting.capacity = 1
        self.setting.save()
        self.a, self.b = child(self.f, '青木'), child(self.f, '井上')
        self.day = datetime.date(2026, 10, 1)
        self.user = StaffAccount.objects.create_user('u', password='pass12345', facility=self.f,
                                                     role=StaffAccount.ROLE_ADMIN)
        self.client.force_login(self.user)

    def test_move_changes_date_and_promotes_the_old_day(self):
        res, _ = services.create_reservation(self.f, self.a, self.day)
        waiting, _ = services.create_reservation(self.f, self.b, self.day)
        self.assertEqual(waiting.status, Reservation.STATUS_WAITLIST)

        services.move_reservation(res, self.day + D(days=1))
        res.refresh_from_db(); waiting.refresh_from_db()
        self.assertEqual(res.date, self.day + D(days=1))
        self.assertEqual(res.status, Reservation.STATUS_CONFIRMED)
        self.assertEqual(waiting.status, Reservation.STATUS_CONFIRMED)   # もとの日は繰り上がる
        self.assertTrue(ReservationNotice.objects.filter(kind=ReservationNotice.KIND_MOVED).exists())

    def test_move_to_a_full_day_becomes_waitlist(self):
        res, _ = services.create_reservation(self.f, self.a, self.day)
        services.create_reservation(self.f, self.b, self.day + D(days=1))
        services.move_reservation(res, self.day + D(days=1))
        res.refresh_from_db()
        self.assertEqual(res.status, Reservation.STATUS_WAITLIST)

    def test_move_rejects_a_closed_day_and_a_duplicate(self):
        res, _ = services.create_reservation(self.f, self.a, self.day)
        ClosedDate.objects.create(facility=self.f, date=self.day + D(days=2))
        with self.assertRaises(services.ReservationError):
            services.move_reservation(res, self.day + D(days=2))
        services.create_reservation(self.f, self.a, self.day + D(days=3))
        with self.assertRaises(services.ReservationError):
            services.move_reservation(res, self.day + D(days=3))

    def test_delete_removes_the_row_and_promotes(self):
        res, _ = services.create_reservation(self.f, self.a, self.day)
        waiting, _ = services.create_reservation(self.f, self.b, self.day)
        services.delete_reservation(res)
        waiting.refresh_from_db()
        self.assertFalse(Reservation.objects.filter(pk=res.pk).exists())
        self.assertEqual(waiting.status, Reservation.STATUS_CONFIRMED)
        # 消した予約ぶんの「取消」通知は残さない
        self.assertFalse(ReservationNotice.objects.filter(kind=ReservationNotice.KIND_CANCELLED).exists())

    def test_day_screen_can_move_and_delete(self):
        res, _ = services.create_reservation(self.f, self.a, self.day)
        url = reverse('reservations:day', args=[2026, 10, 1])
        moved = self.client.post(url, {'action': 'edit', 'reservation': res.pk,
                                       'date': '2026-10-05', 'note': 'ならしの日'})
        self.assertRedirects(moved, reverse('reservations:day', args=[2026, 10, 5]))
        res.refresh_from_db()
        self.assertEqual((res.date, res.note), (datetime.date(2026, 10, 5), 'ならしの日'))

        self.client.post(reverse('reservations:day', args=[2026, 10, 5]),
                         {'action': 'edit', 'do_delete': '1', 'reservation': res.pk, 'date': '2026-10-05'})
        self.assertFalse(Reservation.objects.filter(pk=res.pk).exists())

    def test_other_facility_reservation_is_not_reachable(self):
        other = Facility.objects.create(name='よその事業所', use_reservation=True)
        theirs, _ = services.create_reservation(other, child(other, '他所'), self.day)
        url = reverse('reservations:day', args=[2026, 10, 1])
        self.assertEqual(self.client.post(url, {'action': 'edit', 'reservation': theirs.pk,
                                                'date': '2026-10-05'}).status_code, 404)
        self.assertEqual(self.client.post(url, {'action': 'edit', 'do_delete': '1',
                                                'reservation': theirs.pk}).status_code, 404)


class PublicPageTests(TestCase):
    """顧客向けの予定表（ログインなし）"""

    def setUp(self):
        self.f = Facility.objects.create(name='なゆた', use_reservation=True)
        self.setting = services.get_setting(self.f)
        self.setting.capacity = 2
        self.setting.save()
        self.a = child(self.f, '青木')
        self.customer = Customer.objects.create(facility=self.f, name='青木 母')
        self.customer.children.add(self.a)
        self.today = datetime.date.today()
        self.day = self.today + D(days=7)

    def url(self, name, token):
        return reverse(f'reservations_public:{name}', args=[token])

    def test_public_calendar_shows_counts_but_no_names(self):
        services.create_reservation(self.f, self.a, self.day)
        res = self.client.get(self.url('calendar', self.setting.public_token))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'なゆた')
        self.assertNotContains(res, '青木')

    def test_public_calendar_can_be_turned_off(self):
        self.setting.public_calendar = False
        self.setting.save()
        self.assertEqual(self.client.get(self.url('calendar', self.setting.public_token)).status_code, 404)

    def test_unknown_token_is_not_found(self):
        self.assertEqual(self.client.get(self.url('calendar', 'not-a-token')).status_code, 404)
        self.assertEqual(self.client.get(self.url('customer', 'not-a-token')).status_code, 404)

    def test_customer_can_book_and_cancel(self):
        page = self.url('customer', self.customer.token)
        self.assertContains(self.client.get(page), '青木 母')

        self.client.post(page, {'action': 'book', 'beneficiary': self.a.pk, 'date': self.day.isoformat()})
        res = Reservation.objects.get(facility=self.f, beneficiary=self.a, date=self.day)
        self.assertEqual((res.status, res.source, res.customer), (Reservation.STATUS_CONFIRMED,
                                                                  Reservation.SOURCE_WEB, self.customer))

        self.client.post(page, {'action': 'cancel', 'reservation': res.pk})
        res.refresh_from_db()
        self.assertEqual(res.status, Reservation.STATUS_CANCELLED)

    def test_customer_cannot_book_outside_the_window_or_on_a_closed_day(self):
        page = self.url('customer', self.customer.token)
        self.client.post(page, {'action': 'book', 'beneficiary': self.a.pk,
                                'date': (self.today - D(days=1)).isoformat()})
        self.client.post(page, {'action': 'book', 'beneficiary': self.a.pk,
                                'date': (self.today + D(days=365)).isoformat()})
        ClosedDate.objects.create(facility=self.f, date=self.day)
        self.client.post(page, {'action': 'book', 'beneficiary': self.a.pk, 'date': self.day.isoformat()})
        self.assertEqual(Reservation.objects.count(), 0)

    def test_customer_cannot_touch_another_familys_reservation(self):
        other_child = child(self.f, '井上')
        other_customer = Customer.objects.create(facility=self.f, name='井上 母')
        other_customer.children.add(other_child)
        theirs, _ = services.create_reservation(self.f, other_child, self.day, customer=other_customer)

        page = self.url('customer', self.customer.token)
        self.client.post(page, {'action': 'cancel', 'reservation': theirs.pk})
        theirs.refresh_from_db()
        self.assertEqual(theirs.status, Reservation.STATUS_CONFIRMED)
        # よその子の名前でも申し込めない
        self.client.post(page, {'action': 'book', 'beneficiary': other_child.pk,
                                'date': (self.day + D(days=1)).isoformat()})
        self.assertFalse(Reservation.objects.filter(beneficiary=other_child, date=self.day + D(days=1)).exists())

    def test_booking_can_be_turned_off(self):
        self.setting.public_booking = False
        self.setting.save()
        page = self.url('customer', self.customer.token)
        self.client.post(page, {'action': 'book', 'beneficiary': self.a.pk, 'date': self.day.isoformat()})
        self.assertEqual(Reservation.objects.count(), 0)

    def test_reissued_token_closes_the_old_address(self):
        old = self.customer.token
        self.customer.reissue_token()
        self.customer.save()
        self.assertEqual(self.client.get(self.url('customer', old)).status_code, 404)
        self.assertEqual(self.client.get(self.url('customer', self.customer.token)).status_code, 200)


class ApplyMessageTests(TestCase):
    """LINE に届いた文を、その場で予約にする"""

    def setUp(self):
        self.f = Facility.objects.create(name='なゆた', use_reservation=True)
        self.setting = services.get_setting(self.f)
        self.setting.capacity = 1
        self.setting.save()
        self.a, self.b = child(self.f, '青木', 'はると'), child(self.f, '井上', 'みなと')
        self.customer = Customer.objects.create(facility=self.f, name='青木 花子', line_user_id='U1')
        self.customer.children.add(self.a)
        self.today = datetime.date.today()
        self.day = self.today + D(days=7)

    def md(self, day):
        return f'{day.month}/{day.day}'

    def test_finds_several_dates_in_one_message(self):
        d1, d2 = self.today + D(days=3), self.today + D(days=4)
        parsed = services.parse_message(f'{self.md(d1)} と {self.md(d2)} をお願いします', self.today)
        self.assertEqual((parsed['intent'], parsed['dates']), ('reserve', [d1, d2]))
        self.assertEqual(parsed['date'], d1)

    def test_reads_tomorrow_and_a_full_date(self):
        self.assertEqual(services.parse_message('明日お願いします', self.today)['date'], self.today + D(days=1))
        parsed = services.parse_message('2026-10-01 予約', datetime.date(2026, 9, 16))
        self.assertEqual(parsed['dates'], [datetime.date(2026, 10, 1)])   # 年つきを月日として二重に拾わない

    def test_customer_books_two_days_at_once(self):
        d1, d2 = self.today + D(days=3), self.today + D(days=4)
        handled, reply = services.apply_message(
            self.f, f'{self.md(d1)} {self.md(d2)} 予約おねがいします', customer=self.customer, today=self.today)
        self.assertTrue(handled)
        self.assertEqual(Reservation.objects.filter(beneficiary=self.a).count(), 2)
        self.assertIn('承りました', reply)
        self.assertIn('なゆた', reply)
        # その場で返事をしたぶんは、送信待ちに残さない
        self.assertFalse(ReservationNotice.objects.filter(customer=self.customer,
                                                          status=ReservationNotice.STATUS_PENDING).exists())

    def test_customer_message_outside_the_window_is_explained(self):
        handled, reply = services.apply_message(self.f, f'{self.md(self.today)} 予約',
                                                customer=self.customer, today=self.today)
        self.assertTrue(handled)
        self.assertEqual(Reservation.objects.count(), 0)
        self.assertIn('締め切り', reply)

    def test_customer_cannot_book_a_child_they_do_not_look_after(self):
        handled, _ = services.apply_message(self.f, f'{self.md(self.day)} 井上みなと 予約',
                                            customer=self.customer, today=self.today)
        self.assertFalse(handled)   # 職員が確かめる
        self.assertEqual(Reservation.objects.count(), 0)

    def test_message_without_a_date_is_left_to_staff(self):
        handled, _ = services.apply_message(self.f, '来週あたり予約したいです',
                                            customer=self.customer, today=self.today)
        self.assertFalse(handled)

    def test_staff_group_needs_a_name(self):
        handled, _ = services.apply_message(self.f, f'{self.md(self.day)} 予約', staff=True, today=self.today)
        self.assertFalse(handled)
        handled, reply = services.apply_message(self.f, f'{self.md(self.day)} 青木はると 予約',
                                                staff=True, today=self.today)
        self.assertTrue(handled)
        res = Reservation.objects.get(beneficiary=self.a, date=self.day)
        self.assertEqual((res.source, res.customer), (Reservation.SOURCE_GROUP, self.customer))

    def test_staff_can_book_past_the_customer_window(self):
        handled, _ = services.apply_message(self.f, f'{self.md(self.today)} 青木はると 予約',
                                            staff=True, today=self.today)
        self.assertTrue(handled)
        self.assertTrue(Reservation.objects.filter(date=self.today).exists())

    def test_cancel_by_message(self):
        services.create_reservation(self.f, self.a, self.day, customer=self.customer)
        handled, reply = services.apply_message(self.f, f'{self.md(self.day)} キャンセルします',
                                                customer=self.customer, today=self.today)
        self.assertTrue(handled)
        self.assertIn('取り消しました', reply)
        self.assertEqual(Reservation.objects.get(beneficiary=self.a, date=self.day).status,
                         Reservation.STATUS_CANCELLED)

    def test_check_asks_for_the_vacancies(self):
        handled, reply = services.apply_message(self.f, '空いてますか？', customer=self.customer, today=self.today)
        self.assertTrue(handled)
        self.assertIn('空き状況', reply)

    def test_full_day_becomes_waitlist(self):
        services.create_reservation(self.f, self.b, self.day)
        handled, reply = services.apply_message(self.f, f'{self.md(self.day)} 予約',
                                                customer=self.customer, today=self.today)
        self.assertTrue(handled)
        self.assertEqual(Reservation.objects.get(beneficiary=self.a, date=self.day).status,
                         Reservation.STATUS_WAITLIST)
        self.assertIn('キャンセル待ち', reply)


@override_settings(RESERVATION_ONLY=True, ROOT_URLCONF='config.urls_reservation')
class StandaloneModeTests(TestCase):
    """予約管理だけを別サーバーで動かす構成（横展開用）"""

    def setUp(self):
        self.f = Facility.objects.create(name='なゆた')   # 施設設定の「使う機能」は見ない
        self.user = StaffAccount.objects.create_user('u', password='pass12345', facility=self.f,
                                                     role=StaffAccount.ROLE_ADMIN)
        self.client.force_login(self.user)

    def test_home_goes_to_the_reservation_calendar(self):
        self.assertRedirects(self.client.get('/'), reverse('reservations:calendar'))

    def test_calendar_renders_with_a_reservation_only_sidebar(self):
        res = self.client.get(reverse('reservations:calendar'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, '予約カレンダー')
        self.assertContains(res, reverse('reservations:customers'))
        self.assertNotContains(res, '支援計画')
        self.assertNotContains(res, '帳票出力')

    def test_the_feature_switch_is_not_required(self):
        self.assertEqual(self.client.get(reverse('reservations:day', args=[2026, 10, 1])).status_code, 200)
        self.assertEqual(self.client.get(reverse('reservations:line')).status_code, 200)

    def test_customer_page_works(self):
        customer = Customer.objects.create(facility=self.f, name='青木 花子')
        customer.children.add(child(self.f, '青木'))
        self.client.logout()
        res = self.client.get(reverse('reservations_public:customer', args=[customer.token]))
        self.assertEqual(res.status_code, 200)

    def test_records_screens_are_not_published(self):
        self.assertEqual(self.client.get('/records/').status_code, 404)
        self.assertEqual(self.client.get('/billing/').status_code, 404)


class PublicTokenSafetyTests(TestCase):
    """アドレスを1字でも書き換えたら開けない（打ち間違い・書き換え・別ページへの貼り付け）"""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.f = Facility.objects.create(name='なゆた', use_reservation=True)
        self.setting = services.get_setting(self.f)
        self.customer = Customer.objects.create(facility=self.f, name='青木 母')
        self.customer.children.add(child(self.f, '青木'))

    def calendar_url(self, token):
        return reverse('reservations_public:calendar', args=[token])

    def customer_url(self, token):
        return reverse('reservations_public:customer', args=[token])

    def test_the_real_addresses_open(self):
        self.assertEqual(self.client.get(self.calendar_url(self.setting.public_token)).status_code, 200)
        self.assertEqual(self.client.get(self.customer_url(self.customer.token)).status_code, 200)

    def test_one_character_off_never_opens(self):
        """どの1字を変えても、増やしても、減らしても 404"""
        for token, url in ((self.setting.public_token, self.calendar_url),
                           (self.customer.token, self.customer_url)):
            for i in range(len(token)):
                swapped = 'B' if token[i] == 'A' else 'A'
                changed = token[:i] + swapped + token[i + 1:]
                dropped = token[:i] + token[i + 1:]
                added = token[:i] + 'A' + token[i:]
                for bad in (changed, dropped, added):
                    if bad == token:
                        continue
                    self.assertEqual(self.client.get(url(bad)).status_code, 404,
                                     f'{bad} が開けてしまった')

    def test_the_two_pages_do_not_cross(self):
        """空き状況のアドレスを顧客ページに貼っても、その逆でも開けない"""
        self.assertEqual(self.client.get(self.customer_url(self.setting.public_token)).status_code, 404)
        self.assertEqual(self.client.get(self.calendar_url(self.customer.token)).status_code, 404)

    def test_another_customers_address_only_shows_that_customer(self):
        other = Customer.objects.create(facility=self.f, name='井上 母')
        res = self.client.get(self.customer_url(other.token))
        self.assertEqual(res.status_code, 200)
        self.assertNotContains(res, '青木 母')

    def test_a_made_up_signature_does_not_open(self):
        body = self.customer.token.split(':')[0]
        self.assertEqual(self.client.get(self.customer_url(f'{body}:abcdefghij')).status_code, 404)
        self.assertEqual(self.client.get(self.customer_url(body)).status_code, 404)
        self.assertEqual(self.client.get(self.customer_url('a' * 200)).status_code, 404)

    def test_a_broken_address_never_touches_the_database(self):
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        bad = self.customer.token[:-1] + ('A' if self.customer.token[-1] != 'A' else 'B')
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.client.get(self.customer_url(bad)).status_code, 404)
        self.assertEqual([q for q in queries.captured_queries if 'reservations_customer' in q['sql']], [])

    def test_month_pages_check_the_address_too(self):
        bad = self.customer.token[:-1] + ('A' if self.customer.token[-1] != 'A' else 'B')
        self.assertEqual(
            self.client.get(reverse('reservations_public:customer_month', args=[bad, 2026, 10])).status_code, 404)
        self.assertEqual(
            self.client.get(reverse('reservations_public:calendar_month', args=[bad, 2026, 10])).status_code, 404)

    def test_posting_to_a_broken_address_changes_nothing(self):
        bad = self.customer.token[:-1] + ('A' if self.customer.token[-1] != 'A' else 'B')
        res = self.client.post(self.customer_url(bad),
                               {'action': 'book', 'beneficiary': self.f.beneficiaries.first().pk,
                                'date': (datetime.date.today() + D(days=7)).isoformat()})
        self.assertEqual(res.status_code, 404)
        self.assertEqual(Reservation.objects.count(), 0)

    def test_reissued_address_is_signed_and_the_old_one_is_dead(self):
        from reservations import tokens
        old = self.customer.token
        self.customer.reissue_token()
        self.customer.save()
        self.assertTrue(tokens.is_valid(tokens.CUSTOMER, self.customer.token))
        self.assertEqual(self.client.get(self.customer_url(old)).status_code, 404)
        self.assertEqual(self.client.get(self.customer_url(self.customer.token)).status_code, 200)

    def test_an_address_signed_with_another_key_does_not_open(self):
        """別の SECRET_KEY で作られたアドレスは通らない"""
        from django.core import signing
        from reservations import tokens
        forged = signing.Signer(key='someone-elses-key', salt=tokens.CUSTOMER).sign('abcdefghij')
        self.assertEqual(self.client.get(self.customer_url(forged)).status_code, 404)


class BookingRequestTests(TestCase):
    """空き状況のページからの申し込み（職員が確かめて予約にする）"""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.f = Facility.objects.create(name='なゆた', use_reservation=True)
        self.setting = services.get_setting(self.f)
        self.setting.capacity = 1
        self.setting.save()
        self.a = child(self.f, '青木', 'はると')
        self.today = datetime.date.today()
        self.day = self.today + D(days=7)
        self.user = StaffAccount.objects.create_user('u', password='pass12345', facility=self.f,
                                                     role=StaffAccount.ROLE_ADMIN)

    def page(self):
        return reverse('reservations_public:calendar', args=[self.setting.public_token])

    def approve_mode(self):
        """職員が確認してから確定する受け方に切り替える"""
        self.setting.booking_mode = ReservationSetting.MODE_APPROVE
        self.setting.save()

    def send(self, **kw):
        data = {'date': self.day.isoformat(), 'name': '青木 花子', 'child_name': '青木はると',
                'kana': 'あおき はなこ', 'phone': '090-0000-0000', 'note': '15時ごろ'}
        data.update(kw)
        return self.client.post(self.page(), data)

    # -- 顧客側 ----------------------------------------------------
    def test_the_form_is_shown_and_the_request_is_stored(self):
        self.approve_mode()
        self.assertContains(self.client.get(self.page()), 'ご利用のお申し込み')
        self.send()
        req = BookingRequest.objects.get(facility=self.f)
        self.assertEqual((req.date, req.name, req.child_name, req.status),
                         (self.day, '青木 花子', '青木はると', BookingRequest.STATUS_PENDING))
        self.assertEqual(Reservation.objects.count(), 0)   # まだ予約にはしない

    def test_the_reply_says_it_is_not_confirmed_yet(self):
        self.approve_mode()
        res = self.client.post(self.page(), {'date': self.day.isoformat(), 'name': '青木 花子',
                                             'child_name': '青木はると'}, follow=True)
        self.assertContains(res, 'お申し込みを承りました')
        self.assertContains(res, '確認のうえ')

    def test_missing_fields_are_refused(self):
        self.send(name='')
        self.send(child_name='')
        self.send(date='')
        self.assertEqual(BookingRequest.objects.count(), 0)

    def test_days_outside_the_window_and_closed_days_are_refused(self):
        self.send(date=(self.today - D(days=1)).isoformat())
        self.send(date=(self.today + D(days=400)).isoformat())
        ClosedDate.objects.create(facility=self.f, date=self.day)
        self.send()
        self.assertEqual(BookingRequest.objects.count(), 0)

    def test_the_hidden_field_stops_robots(self):
        self.send(website='http://spam.example')
        self.assertEqual(BookingRequest.objects.count(), 0)

    def test_too_many_requests_are_refused(self):
        for i in range(8):
            self.send(date=(self.today + D(days=7 + i)).isoformat())
        self.assertEqual(BookingRequest.objects.count(), 5)   # 1時間に5件まで

    def test_requests_can_be_turned_off(self):
        self.setting.public_request = False
        self.setting.save()
        self.assertNotContains(self.client.get(self.page()), 'ご利用のお申し込み')
        self.assertEqual(self.send().status_code, 404)
        self.assertEqual(BookingRequest.objects.count(), 0)

    def test_auto_books_when_the_name_matches_one_child(self):
        self.send()
        req = BookingRequest.objects.get(facility=self.f)
        self.assertEqual(req.status, BookingRequest.STATUS_DONE)
        self.assertEqual(req.reservation.beneficiary, self.a)
        self.assertEqual(req.reservation.source, Reservation.SOURCE_WEB)

    def test_auto_books_an_unknown_name_as_a_guest(self):
        """台帳にいない方でも、席は押さえる（職員があとから結びつける）"""
        self.send(child_name='山口 そら')
        res = Reservation.objects.get(facility=self.f)
        self.assertEqual((res.beneficiary, res.guest_name, res.status),
                         (None, '山口 そら', Reservation.STATUS_CONFIRMED))
        self.assertEqual(res.display_name, '山口 そら')
        self.assertTrue(res.is_guest)

    def test_approve_mode_keeps_the_request_pending(self):
        self.approve_mode()
        self.send(child_name='知らない 名前')
        self.assertEqual(BookingRequest.objects.get(facility=self.f).status, BookingRequest.STATUS_PENDING)
        self.assertEqual(Reservation.objects.count(), 0)

    # -- 職員側 ----------------------------------------------------
    def test_staff_screen_lists_and_applies(self):
        self.approve_mode()
        self.send()
        req = BookingRequest.objects.get(facility=self.f)
        self.client.force_login(self.user)

        page = self.client.get(reverse('reservations:requests'))
        self.assertContains(page, '青木はると')
        self.assertContains(page, '090-0000-0000')

        self.client.post(reverse('reservations:request_action', args=[req.pk]),
                         {'action': 'apply', 'beneficiary': self.a.pk})
        req.refresh_from_db()
        self.assertEqual(req.status, BookingRequest.STATUS_DONE)
        res = Reservation.objects.get(facility=self.f, date=self.day)
        self.assertEqual((res.beneficiary, res.status, res.source),
                         (self.a, Reservation.STATUS_CONFIRMED, Reservation.SOURCE_WEB))
        self.assertTrue(ReservationNotice.objects.filter(reservation=res).exists())

    def test_applying_without_choosing_a_child_does_nothing(self):
        self.approve_mode()
        self.send()
        req = BookingRequest.objects.get(facility=self.f)
        self.client.force_login(self.user)
        self.client.post(reverse('reservations:request_action', args=[req.pk]), {'action': 'apply'})
        req.refresh_from_db()
        self.assertEqual(req.status, BookingRequest.STATUS_PENDING)
        self.assertEqual(Reservation.objects.count(), 0)

    def test_decline_register_and_delete(self):
        self.approve_mode()
        self.send()
        req = BookingRequest.objects.get(facility=self.f)
        self.client.force_login(self.user)

        self.client.post(reverse('reservations:request_action', args=[req.pk]),
                         {'action': 'register'})
        customer = Customer.objects.get(facility=self.f)
        self.assertEqual((customer.name, customer.phone), ('青木 花子', '090-0000-0000'))

        self.client.post(reverse('reservations:request_action', args=[req.pk]),
                         {'action': 'decline', 'reason': '定員のため'})
        req.refresh_from_db()
        self.assertEqual((req.status, req.result_note), (BookingRequest.STATUS_DECLINED, '定員のため'))
        self.assertEqual(Reservation.objects.count(), 0)

        self.client.post(reverse('reservations:request_action', args=[req.pk]), {'action': 'delete'})
        self.assertEqual(BookingRequest.objects.count(), 0)

    def test_a_handled_request_cannot_be_applied_twice(self):
        self.approve_mode()
        self.send()
        req = BookingRequest.objects.get(facility=self.f)
        self.client.force_login(self.user)
        url = reverse('reservations:request_action', args=[req.pk])
        self.client.post(url, {'action': 'apply', 'beneficiary': self.a.pk})
        self.client.post(url, {'action': 'apply', 'beneficiary': self.a.pk})
        self.assertEqual(Reservation.objects.count(), 1)

    def test_another_facility_request_is_not_reachable(self):
        other = Facility.objects.create(name='よその事業所', use_reservation=True)
        theirs = BookingRequest.objects.create(facility=other, date=self.day, name='よその方')
        self.client.force_login(self.user)
        res = self.client.post(reverse('reservations:request_action', args=[theirs.pk]),
                               {'action': 'delete'})
        self.assertEqual(res.status_code, 404)
        self.assertTrue(BookingRequest.objects.filter(pk=theirs.pk).exists())

    def test_the_full_day_is_taken_as_a_waitlist(self):
        self.approve_mode()
        services.create_reservation(self.f, child(self.f, '井上'), self.day)
        self.send()
        req = BookingRequest.objects.get(facility=self.f)
        self.client.force_login(self.user)
        self.client.post(reverse('reservations:request_action', args=[req.pk]),
                         {'action': 'apply', 'beneficiary': self.a.pk})
        self.assertEqual(Reservation.objects.get(beneficiary=self.a).status, Reservation.STATUS_WAITLIST)


class SeedReservationTests(TestCase):
    """seed_demo：予約管理を使う施設には、予約のサンプルも入る"""

    def seed(self, facility, **kw):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        call_command('seed_demo', '--facility', facility.pk, stdout=out, **kw)
        return out.getvalue()

    def test_seed_creates_reservation_samples(self):
        f = Facility.objects.create(name='なゆた', use_reservation=True)
        StaffAccount.objects.create_user('n', password='pass12345', facility=f)
        out = self.seed(f)

        self.assertIn('顧客（予約の連絡先）', out)
        self.assertEqual(Customer.objects.filter(facility=f).count(), 6)
        self.assertTrue(Reservation.objects.filter(facility=f, status=Reservation.STATUS_CONFIRMED).exists())
        self.assertTrue(Reservation.objects.filter(facility=f, status=Reservation.STATUS_WAITLIST).exists())
        self.assertEqual(BookingRequest.objects.filter(facility=f).count(), 2)
        self.assertEqual(LineInbox.objects.filter(facility=f).count(), 1)
        self.assertTrue(ClosedDate.objects.filter(facility=f).exists())
        # 送信待ちの通知は見本として少しだけ
        self.assertLessEqual(ReservationNotice.objects.filter(facility=f).count(), 3)
        # すべて これからの日（過去には入れない）
        self.assertFalse(Reservation.objects.filter(facility=f, date__lt=datetime.date.today()).exists())

    def test_one_request_matches_a_child_and_one_does_not(self):
        f = Facility.objects.create(name='なゆた', use_reservation=True)
        self.seed(f)
        matched = [services.request_matches(f, r) for r in BookingRequest.objects.filter(facility=f)]
        self.assertEqual(sum(1 for m in matched if m is not None), 1)

    def test_reset_clears_the_reservation_samples(self):
        f = Facility.objects.create(name='なゆた', use_reservation=True)
        self.seed(f)
        self.seed(f, reset=True)
        self.assertEqual(Customer.objects.filter(facility=f).count(), 6)
        self.assertEqual(BookingRequest.objects.filter(facility=f).count(), 2)
        self.assertEqual(LineInbox.objects.filter(facility=f).count(), 1)
        self.assertEqual(ClosedDate.objects.filter(facility=f).count(), 1)

    def test_facilities_without_the_feature_get_none(self):
        g = Facility.objects.create(name='標準の事業所')
        self.seed(g)
        self.assertEqual(Customer.objects.filter(facility=g).count(), 0)
        self.assertEqual(Reservation.objects.filter(facility=g).count(), 0)
        self.assertEqual(BookingRequest.objects.filter(facility=g).count(), 0)

    def test_an_existing_capacity_is_kept(self):
        f = Facility.objects.create(name='なゆた', use_reservation=True)
        setting = services.get_setting(f)
        setting.capacity = 8
        setting.save()
        self.seed(f)
        setting.refresh_from_db()
        self.assertEqual(setting.capacity, 8)


class SettingScreenPermissionTests(TestCase):
    """予約の決まりごとを、画面から実際に変えられるか"""

    def setUp(self):
        self.f = Facility.objects.create(name='なゆた', use_reservation=True)
        self.setting = services.get_setting(self.f)

    def login(self, username, **kw):
        user = StaffAccount.objects.create_user(username, password='pass12345', facility=self.f, **kw)
        self.client.force_login(user)
        return user

    def save(self, **kw):
        data = {'capacity': 6, 'allow_waitlist': 'on', 'signature': 'なゆた',
                'booking_from_days': 1, 'booking_until_days': 60}
        data.update(kw)
        return self.client.post(reverse('reservations:settings'), data)

    def capacity_is_editable(self):
        """1日の枠の入力欄が使える状態か（disabled になっていないか）"""
        html = self.client.get(reverse('reservations:calendar')).content.decode()
        start = html.index('name="capacity"')
        return 'disabled' not in html[start:start + 200]

    def test_the_form_is_not_disabled_for_an_admin(self):
        self.login('a', role=StaffAccount.ROLE_ADMIN)
        self.assertTrue(self.capacity_is_editable())
        self.assertNotContains(self.client.get(reverse('reservations:calendar')),
                               'この設定を変えられるのは管理者だけです。')

    def test_an_admin_can_save(self):
        self.login('a', role=StaffAccount.ROLE_ADMIN)
        self.save()
        self.setting.refresh_from_db()
        self.assertEqual(self.setting.capacity, 6)

    def test_a_developer_can_save(self):
        """開発向けユーザーは、事業所を切り替えて設定を整える役なので変えられる"""
        self.login('d', is_developer=True)
        self.save(capacity=7)
        self.setting.refresh_from_db()
        self.assertEqual(self.setting.capacity, 7)
        self.assertTrue(self.capacity_is_editable())

    def test_a_superuser_can_save(self):
        user = self.login('s')
        user.is_superuser = True
        user.save(update_fields=['is_superuser'])
        self.save(capacity=8)
        self.setting.refresh_from_db()
        self.assertEqual(self.setting.capacity, 8)

    def test_an_ordinary_staff_cannot(self):
        self.login('u')
        self.save(capacity=9)
        self.setting.refresh_from_db()
        self.assertEqual(self.setting.capacity, 10)
        self.assertFalse(self.capacity_is_editable())
        self.assertContains(self.client.get(reverse('reservations:calendar')),
                            'この設定を変えられるのは管理者だけです。')

    def test_the_line_channel_form_follows_the_same_rule(self):
        self.login('d', is_developer=True)
        self.client.post(reverse('reservations:line'),
                         {'action': 'channel', 'facility_name': 'なゆた', 'line_channel_secret': 'sec'})
        self.f.refresh_from_db()
        self.assertEqual(self.f.line_channel_secret, 'sec')


class VacancyAnnounceTests(TestCase):
    """満席 → 空きになったときのお知らせ"""

    def setUp(self):
        self.f = Facility.objects.create(name='なゆた', use_reservation=True)
        self.setting = services.get_setting(self.f)
        self.setting.capacity = 1
        self.setting.save()
        self.a, self.b = child(self.f, '青木'), child(self.f, '井上')
        self.day = datetime.date.today() + D(days=7)
        # 連絡先：LINE連携ずみ（お知らせが届く）と、未連携（届かない）
        self.linked = Customer.objects.create(facility=self.f, name='井上 母', line_user_id='U-inoue')
        self.linked.children.add(self.b)
        self.plain = Customer.objects.create(facility=self.f, name='上田 母')
        self.booked = Customer.objects.create(facility=self.f, name='青木 母', line_user_id='U-aoki')
        self.booked.children.add(self.a)

    def vacancies(self):
        return ReservationNotice.objects.filter(facility=self.f, kind=ReservationNotice.KIND_VACANCY)

    def test_cancelling_the_last_seat_tells_the_others(self):
        res, _ = services.create_reservation(self.f, self.a, self.day, customer=self.booked)
        services.cancel_reservation(res)
        self.assertEqual([n.customer for n in self.vacancies()], [self.linked])   # 取り消した本人と未連携は除く

    def test_deleting_the_last_seat_tells_the_others(self):
        res, _ = services.create_reservation(self.f, self.a, self.day, customer=self.booked)
        services.delete_reservation(res)
        self.assertEqual([n.customer for n in self.vacancies()], [self.linked])

    def test_moving_a_reservation_away_tells_the_others(self):
        res, _ = services.create_reservation(self.f, self.a, self.day, customer=self.booked)
        services.move_reservation(res, self.day + D(days=1))
        self.assertEqual([n.date for n in self.vacancies()], [self.day])

    def test_nothing_when_a_waitlisted_child_takes_the_seat(self):
        res, _ = services.create_reservation(self.f, self.a, self.day, customer=self.booked)
        services.create_reservation(self.f, self.b, self.day, customer=self.linked)   # キャンセル待ち
        services.cancel_reservation(res)
        self.assertEqual(self.vacancies().count(), 0)   # 繰り上げで埋まったので、お知らせしない

    def test_nothing_when_the_day_was_not_full(self):
        self.setting.capacity = 5
        self.setting.save()
        res, _ = services.create_reservation(self.f, self.a, self.day, customer=self.booked)
        services.cancel_reservation(res)
        self.assertEqual(self.vacancies().count(), 0)

    def test_raising_the_capacity_tells_the_full_days(self):
        services.create_reservation(self.f, self.a, self.day, customer=self.booked)
        self.setting.capacity = 3
        self.setting.save()
        made = services.announce_after_capacity_change(self.f, 1, self.setting)
        self.assertEqual([n.customer for n in made], [self.linked])

    def test_raising_the_capacity_promotes_the_waitlist_first(self):
        services.create_reservation(self.f, self.a, self.day, customer=self.booked)
        waiting, _ = services.create_reservation(self.f, self.b, self.day, customer=self.linked)
        self.setting.capacity = 2
        self.setting.save()
        services.announce_after_capacity_change(self.f, 1, self.setting)
        waiting.refresh_from_db()
        self.assertEqual(waiting.status, Reservation.STATUS_CONFIRMED)
        self.assertEqual(self.vacancies().count(), 0)   # 繰り上げで埋まったので、お知らせしない

    def test_the_setting_can_turn_the_automatic_notice_off(self):
        self.setting.notify_vacancy = False
        self.setting.save()
        res, _ = services.create_reservation(self.f, self.a, self.day, customer=self.booked)
        services.cancel_reservation(res)
        self.assertEqual(self.vacancies().count(), 0)
        # 職員が画面から押したときは、設定に関わらず積む
        self.assertEqual(len(services.offer_vacancy(self.f, self.day, force=True)), 2)

    def test_the_notice_carries_the_customer_page_link(self):
        res, _ = services.create_reservation(self.f, self.a, self.day, customer=self.booked)
        services.cancel_reservation(res, base='https://yoyaku.example.jp/')
        notice = self.vacancies().first()
        self.assertIn(f'/yoyaku/mypage/{self.linked.token}/', notice.body)
        self.assertIn('残り1枠です。', notice.body)

    def test_the_same_customer_is_not_told_twice_for_one_day(self):
        res, _ = services.create_reservation(self.f, self.a, self.day, customer=self.booked)
        services.cancel_reservation(res)                      # 井上 母へ1件
        services.offer_vacancy(self.f, self.day, force=True)  # 井上 母は重ねず、取り消した青木 母にだけ足す
        self.assertEqual(sorted(n.customer.name for n in self.vacancies()), ['井上 母', '青木 母'])
        services.offer_vacancy(self.f, self.day, force=True)
        self.assertEqual(self.vacancies().count(), 2)


class VacancyScreenTests(TestCase):
    """その日の画面から「空きのお知らせ」を出す"""

    def setUp(self):
        self.f = Facility.objects.create(name='なゆた', use_reservation=True)
        self.setting = services.get_setting(self.f)
        self.setting.capacity = 2
        self.setting.save()
        self.a = child(self.f, '青木')
        self.customer = Customer.objects.create(facility=self.f, name='井上 母', line_user_id='U-inoue')
        self.day = datetime.date.today() + D(days=7)
        self.user = StaffAccount.objects.create_user('u', password='pass12345', facility=self.f,
                                                     role=StaffAccount.ROLE_ADMIN)
        self.client.force_login(self.user)

    def day_url(self):
        return reverse('reservations:day', args=[self.day.year, self.day.month, self.day.day])

    def test_the_button_queues_the_notices(self):
        res = self.client.post(self.day_url(), {'action': 'vacancy'}, follow=True)
        self.assertContains(res, '空きのお知らせを 1 件')
        self.assertEqual(ReservationNotice.objects.filter(
            facility=self.f, kind=ReservationNotice.KIND_VACANCY).count(), 1)

    def test_the_button_is_hidden_on_a_closed_day(self):
        ClosedDate.objects.create(facility=self.f, date=self.day)
        page = self.client.get(self.day_url())
        self.assertNotContains(page, '空きのお知らせを送信待ちに入れる')

    def test_it_says_when_there_is_nobody_to_tell(self):
        self.customer.delete()
        res = self.client.post(self.day_url(), {'action': 'vacancy'}, follow=True)
        self.assertContains(res, 'お知らせを入れる相手がいませんでした')

    @mock.patch('line_integration.sending.push_text', return_value=(True, ''))
    def test_auto_send_sends_them_at_once(self, push):
        self.setting.auto_send = True
        self.setting.save()
        res = self.client.post(self.day_url(), {'action': 'vacancy'}, follow=True)
        self.assertContains(res, '送信待ちの通知を 1 件送りました')
        self.assertEqual(ReservationNotice.objects.get(
            facility=self.f, kind=ReservationNotice.KIND_VACANCY).status,
            ReservationNotice.STATUS_SENT)


class FirstComeFirstServedTests(TestCase):
    """来た順に確定し、間に合わなかった方にはその場で伝える"""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.f = Facility.objects.create(name='なゆた', use_reservation=True)
        self.setting = services.get_setting(self.f)
        self.setting.capacity = 1
        self.setting.save()
        self.a, self.b = child(self.f, '青木', 'はると'), child(self.f, '井上', 'みなと')
        self.day = datetime.date.today() + D(days=7)

    def page(self):
        return reverse('reservations_public:calendar', args=[self.setting.public_token])

    def apply(self, child_name, name='保護者'):
        return self.client.post(self.page(), {'date': self.day.isoformat(), 'name': name,
                                              'child_name': child_name}, follow=True)

    # -- 空き状況ページ ------------------------------------------------
    def test_the_first_application_takes_the_last_seat(self):
        first = self.apply('青木はると')
        self.assertContains(first, 'ご予約を承りました')
        self.assertEqual(Reservation.objects.get(beneficiary=self.a).status, Reservation.STATUS_CONFIRMED)

    def test_the_second_one_is_told_it_is_a_waitlist(self):
        self.apply('青木はると')
        second = self.apply('井上みなと', name='井上 母')
        self.assertContains(second, 'ちょうど満席になりました')
        self.assertContains(second, 'キャンセル待ちでお預かりします')
        self.assertEqual(Reservation.objects.get(beneficiary=self.b).status, Reservation.STATUS_WAITLIST)

    def test_the_second_one_is_refused_when_the_waitlist_is_off(self):
        self.setting.allow_waitlist = False
        self.setting.save()
        self.apply('青木はると')
        second = self.apply('井上みなと', name='井上 母')
        self.assertContains(second, 'お受けできませんでした')
        self.assertFalse(Reservation.objects.filter(beneficiary=self.b,
                                                    status__in=Reservation.ACTIVE_STATUSES).exists())

    def test_the_same_person_cannot_take_two_seats_on_one_day(self):
        self.apply('青木はると')
        again = self.apply('青木はると')
        self.assertContains(again, 'すでにあります')
        self.assertEqual(Reservation.objects.filter(beneficiary=self.a,
                                                    status__in=Reservation.ACTIVE_STATUSES).count(), 1)

    def test_two_different_guests_cannot_share_one_seat(self):
        self.apply('山口そら')
        second = self.apply('中村ひかり', name='中村 母')
        self.assertContains(second, 'ちょうど満席になりました')
        self.assertEqual(Reservation.objects.filter(date=self.day,
                                                    status=Reservation.STATUS_CONFIRMED).count(), 1)

    # -- 顧客ページ ----------------------------------------------------
    def test_the_customer_page_says_when_the_seat_has_gone(self):
        customer = Customer.objects.create(facility=self.f, name='井上 母')
        customer.children.add(self.b)
        services.create_reservation(self.f, self.a, self.day)      # 先に1枠が埋まる
        self.setting.allow_waitlist = False
        self.setting.save()
        res = self.client.post(reverse('reservations_public:customer', args=[customer.token]),
                               {'action': 'book', 'beneficiary': self.b.pk, 'date': self.day.isoformat()},
                               follow=True)
        self.assertContains(res, '満席')
        self.assertFalse(Reservation.objects.filter(beneficiary=self.b,
                                                    status__in=Reservation.ACTIVE_STATUSES).exists())

    # -- 席を押さえたあとの後始末 ---------------------------------------
    def test_a_guest_seat_can_be_linked_to_a_child_later(self):
        self.apply('山口そら')
        res = Reservation.objects.get(facility=self.f)
        newcomer = child(self.f, '山口', 'そら')
        services.link_reservation(res, newcomer)
        res.refresh_from_db()
        self.assertEqual((res.beneficiary, res.guest_name, res.display_name),
                         (newcomer, '', '山口 そら'))

    def test_linking_is_refused_when_that_child_already_has_a_seat(self):
        services.create_reservation(self.f, self.a, self.day)
        self.setting.capacity = 2
        self.setting.save()
        self.apply('山口そら')
        guest = Reservation.objects.get(facility=self.f, beneficiary__isnull=True)
        with self.assertRaises(services.ReservationError):
            services.link_reservation(guest, self.a)

    def test_the_day_screen_can_link_a_guest_seat(self):
        self.apply('山口そら')
        res = Reservation.objects.get(facility=self.f)
        newcomer = child(self.f, '山口', 'そら')
        user = StaffAccount.objects.create_user('u', password='pass12345', facility=self.f,
                                                role=StaffAccount.ROLE_ADMIN)
        self.client.force_login(user)
        url = reverse('reservations:day', args=[self.day.year, self.day.month, self.day.day])
        self.assertContains(self.client.get(url), '台帳に未登録')
        self.client.post(url, {'action': 'link', 'reservation': res.pk, 'beneficiary': newcomer.pk})
        res.refresh_from_db()
        self.assertEqual(res.beneficiary, newcomer)

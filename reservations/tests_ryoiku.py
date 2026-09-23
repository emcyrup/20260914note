"""時間枠の予約・月予約利用希望・月間予定表（りょういく）のテスト"""
import datetime
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import StaffAccount
from beneficiaries.models import Beneficiary
from config.jp_holidays import holidays, is_weekend_or_holiday
from facilities.models import Facility

from . import monthly, services
from .models import Customer, MonthlyRequest, RequestScan, Reservation, ReservationNotice


def child(facility, last, first='子'):
    return Beneficiary.objects.create(facility=facility, last_name=last, first_name=first,
                                      last_name_kana=last, date_of_birth=datetime.date(2019, 4, 1))


def ryoiku():
    f = Facility.objects.create(name='りょういく', use_reservation=True, use_therapy_record=True)
    s = services.get_setting(f)
    s.slot_mode, s.slot_capacity = True, 3
    s.weekday_first_hour, s.weekday_last_hour = 10, 18
    s.holiday_first_hour, s.holiday_last_hour = 9, 17
    s.break_hours, s.closed_weekdays = [12], [0, 3]
    s.save()
    return f, s


class HolidayTests(TestCase):
    def test_known_holidays(self):
        h = holidays(2026)
        self.assertEqual(h[datetime.date(2026, 1, 1)], '元日')
        self.assertEqual(h[datetime.date(2026, 1, 12)], '成人の日')      # 第2月曜
        self.assertEqual(h[datetime.date(2026, 3, 20)], '春分の日')
        self.assertEqual(h[datetime.date(2026, 9, 21)], '敬老の日')
        self.assertEqual(h[datetime.date(2026, 9, 23)], '秋分の日')
        self.assertEqual(h[datetime.date(2026, 9, 22)], '国民の休日')     # 敬老の日と秋分の日の間
        self.assertEqual(h[datetime.date(2026, 5, 6)], '振替休日')        # 5/3 が日曜
        self.assertTrue(is_weekend_or_holiday(datetime.date(2026, 10, 3)))    # 土
        self.assertTrue(is_weekend_or_holiday(datetime.date(2026, 11, 3)))    # 文化の日（火）
        self.assertFalse(is_weekend_or_holiday(datetime.date(2026, 10, 2)))   # 金


class SlotSettingTests(TestCase):
    def setUp(self):
        self.f, self.s = ryoiku()

    def test_slot_hours_by_day_type(self):
        self.assertEqual(self.s.slot_hours(datetime.date(2026, 10, 2)), [10, 11, 13, 14, 15, 16, 17, 18])   # 金
        self.assertEqual(self.s.slot_hours(datetime.date(2026, 10, 3)), [9, 10, 11, 13, 14, 15, 16, 17])    # 土
        self.assertEqual(self.s.slot_hours(datetime.date(2026, 11, 3)), [9, 10, 11, 13, 14, 15, 16, 17])    # 祝（火）
        self.assertEqual(self.s.slot_hours(datetime.date(2026, 10, 5)), [])     # 月曜はお休み
        self.assertEqual(self.s.all_slot_hours(), [9, 10, 11, 13, 14, 15, 16, 17, 18])
        self.assertEqual(self.s.slot_capacity_of(datetime.date(2026, 10, 2)), 24)
        self.assertIn('平日 10:00〜18:00枠', self.s.hours_text)
        self.assertEqual(self.s.closed_weekdays_text, '月曜日・木曜日')


class SlotReservationTests(TestCase):
    def setUp(self):
        self.f, self.s = ryoiku()
        self.day = datetime.date(2026, 10, 2)    # 金
        self.kids = [child(self.f, n) for n in ('青木', '井上', '上田', '江口')]

    def test_requires_time_and_counts_per_slot(self):
        with self.assertRaises(services.ReservationError):
            services.create_reservation(self.f, self.kids[0], self.day)
        with self.assertRaises(services.ReservationError):
            services.create_reservation(self.f, self.kids[0], self.day, start_time='9')    # 平日に9時の枠は無い
        for k in self.kids[:3]:
            res, notice = services.create_reservation(self.f, k, self.day, start_time='10')
            self.assertEqual((res.status, res.start_time), (Reservation.STATUS_CONFIRMED, datetime.time(10, 0)))
        self.assertIn('10:00', notice.body)
        st = services.day_state(self.f, self.day)
        slot10 = next(x for x in st['slots'] if x['hour'] == 10)
        self.assertEqual((slot10['confirmed'], slot10['full'], st['confirmed'], st['capacity']), (3, True, 3, 24))
        # 4人目は同じ枠ではキャンセル待ち、別の枠なら確定
        res4, _ = services.create_reservation(self.f, self.kids[3], self.day, start_time='10:00')
        self.assertEqual(res4.status, Reservation.STATUS_WAITLIST)
        services.cancel_reservation(res4, notify=False)
        res4b, _ = services.create_reservation(self.f, self.kids[3], self.day, start_time=11)
        self.assertEqual(res4b.status, Reservation.STATUS_CONFIRMED)

    def test_waitlist_promotes_within_same_slot(self):
        rs = [services.create_reservation(self.f, k, self.day, start_time=10)[0] for k in self.kids[:3]]
        waiting, _ = services.create_reservation(self.f, self.kids[3], self.day, start_time=10)
        self.assertEqual(waiting.status, Reservation.STATUS_WAITLIST)
        # 別の枠が空いても繰り上げない（同じ枠が空いたとき）
        promoted = services.cancel_reservation(rs[0])
        self.assertEqual([p.pk for p in promoted], [waiting.pk])

    def test_move_to_other_slot_same_day(self):
        res, _ = services.create_reservation(self.f, self.kids[0], self.day, start_time=10)
        services.move_reservation(res, self.day, start_time='14')
        res.refresh_from_db()
        self.assertEqual((res.date, res.start_time, res.status), (self.day, datetime.time(14, 0), 'confirmed'))
        # 満枠の枠には移せない → キャンセル待ち
        for k in self.kids[1:4]:
            services.create_reservation(self.f, k, self.day, start_time=15)
        services.move_reservation(res, self.day, start_time=15)
        res.refresh_from_db()
        self.assertEqual(res.status, Reservation.STATUS_WAITLIST)

    def test_closed_weekday_has_no_capacity(self):
        monday = datetime.date(2026, 10, 5)
        self.assertEqual(services.day_state(self.f, monday)['capacity'], 0)
        with self.assertRaises(services.ReservationError):
            services.create_reservation(self.f, self.kids[0], monday, start_time=10)


class MonthlyRequestTests(TestCase):
    def setUp(self):
        self.f, self.s = ryoiku()
        self.kids = [child(self.f, n) for n in ('青木', '井上', '上田', '江口', '大野')]
        self.customer = Customer.objects.create(facility=self.f, name='青木 母', line_user_id='U-aoki')
        self.customer.children.add(self.kids[0])

    def test_wish_helpers(self):
        req = monthly.save_request(self.f, self.kids[0], 2026, 10, 4,
                                   {'2026-10-02': 'all', '2026-10-03': [9, 10, 12], '2026-10-05': 'all'})
        self.assertEqual(req.wish_hours(datetime.date(2026, 10, 2), self.s), [10, 11, 13, 14, 15, 16, 17, 18])
        self.assertEqual(req.wish_hours(datetime.date(2026, 10, 3), self.s), [9, 10])   # 12時は枠なし
        self.assertEqual(req.wish_hours(datetime.date(2026, 10, 5), self.s), [])        # 月曜はお休み
        self.assertEqual(req.slot_count(self.s), 10)
        grid = monthly.request_grid(self.f, 2026, 10, self.s, request=req)
        self.assertEqual(grid['hours'], [9, 10, 11, 13, 14, 15, 16, 17, 18])
        row2 = next(r for r in grid['rows'] if r['date'] == datetime.date(2026, 10, 2))
        self.assertTrue(row2['all_wished'])
        self.assertFalse(row2['cells'][0]['active'])          # 平日の9時は網掛け
        row5 = next(r for r in grid['rows'] if r['date'] == datetime.date(2026, 10, 5))
        self.assertTrue(row5['closed'])

    def test_assign_respects_capacity_and_spreads(self):
        # 5人全員が「土曜の 9・10 時」だけ希望、各3回 → 土曜は 10/3,10,17,24,31 の5日 × 2枠 × 3人
        saturdays = ['2026-10-03', '2026-10-10', '2026-10-17', '2026-10-24', '2026-10-31']
        for k in self.kids:
            monthly.save_request(self.f, k, 2026, 10, 3, {d: [9, 10] for d in saturdays})
        result = monthly.assign_month(self.f, 2026, 10, self.s)
        self.assertEqual(len(result.made), 15)
        self.assertEqual(result.short, [])
        for k in self.kids:
            mine = Reservation.objects.filter(beneficiary=k, status='confirmed')
            self.assertEqual(mine.count(), 3)
            self.assertEqual(len({r.date for r in mine}), 3)          # 同じ日に2回は入れない
        # どの枠も3人まで
        from collections import Counter
        c = Counter((r.date, r.start_time) for r in Reservation.objects.filter(status='confirmed'))
        self.assertTrue(all(n <= 3 for n in c.values()))
        # 通知は利用者（の連絡先）ごとに1通、対象日をまとめて
        notes = ReservationNotice.objects.filter(customer=self.customer)
        self.assertEqual(notes.count(), 1)
        self.assertIn('ご利用日が決まりました', notes.first().body)
        # もう一度回しても増えない（希望回数に達している）
        again = monthly.assign_month(self.f, 2026, 10, self.s)
        self.assertEqual(len(again.made), 0)

    def test_assign_reports_shortage(self):
        # 4人が同じ1枠だけ希望 → 3人しか入らない
        for k in self.kids[:4]:
            monthly.save_request(self.f, k, 2026, 10, 1, {'2026-10-03': [9]})
        result = monthly.assign_month(self.f, 2026, 10, self.s)
        self.assertEqual(len(result.made), 3)
        self.assertEqual(len(result.short), 1)
        self.assertIn('あと1回', result.summary)

    def test_month_schedule_grid(self):
        monthly.save_request(self.f, self.kids[0], 2026, 10, 1, {'2026-10-03': [9]})
        monthly.assign_month(self.f, 2026, 10, self.s)
        sched = monthly.month_schedule(self.f, 2026, 10, self.s)
        self.assertEqual(sched['weeks'][0]['days'][0]['date'].weekday(), 6)      # 日曜はじまり
        sat = next(d for w in sched['weeks'] for d in w['days'] if d['date'] == datetime.date(2026, 10, 3))
        slot9 = sat['slots'][0]
        self.assertEqual((slot9['hour'], slot9['active'], len(slot9['boxes'])), (9, True, 3))
        self.assertEqual(slot9['boxes'][0].beneficiary, self.kids[0])
        self.assertIsNone(slot9['boxes'][1])
        mon = next(d for w in sched['weeks'] for d in w['days'] if d['date'] == datetime.date(2026, 10, 5))
        self.assertTrue(mon['closed'])
        rows = monthly.request_rows(self.f, 2026, 10, self.s)
        aoki = next(r for r in rows if r['beneficiary'] == self.kids[0])
        self.assertEqual((aoki['desired'], aoki['confirmed'], aoki['remaining']), (1, 1, 0))


    def test_all_day_wishes_fill_from_morning(self):
        # 4人が 10/3（土）を「終日」で1回 → 9時に3人、あふれた1人は次に早い10時
        for k in self.kids[:4]:
            monthly.save_request(self.f, k, 2026, 10, 1, {'2026-10-03': 'all'})
        monthly.assign_month(self.f, 2026, 10, self.s)
        hours = sorted(r.start_time.hour for r in Reservation.objects.filter(status='confirmed'))
        self.assertEqual(hours, [9, 9, 9, 10])

    def test_time_specified_wishes_go_before_all_day(self):
        # 井上・上田・江口は 9時だけ希望（ほかの日に1回確定ずみ）、青木は終日。
        # 回数の少ない青木が先に回っても、9時の枠を先に取らない
        sat, sun = datetime.date(2026, 10, 3), datetime.date(2026, 10, 18)
        for k in self.kids[1:4]:
            services.create_reservation(self.f, k, sun, start_time=datetime.time(9, 0), notify=False)
            monthly.save_request(self.f, k, 2026, 10, 2, {'2026-10-03': [9], '2026-10-18': [9]})
        monthly.save_request(self.f, self.kids[0], 2026, 10, 1, {'2026-10-03': 'all'})
        result = monthly.assign_month(self.f, 2026, 10, self.s)
        self.assertEqual(result.short, [])
        at9 = set(Reservation.objects.filter(date=sat, start_time=datetime.time(9, 0)).values_list('beneficiary', flat=True))
        self.assertEqual(at9, {k.pk for k in self.kids[1:4]})
        self.assertEqual(Reservation.objects.get(beneficiary=self.kids[0]).start_time, datetime.time(10, 0))

    def test_time_specified_wish_never_placed_outside_marked_hours(self):
        monthly.save_request(self.f, self.kids[0], 2026, 10, 1, {'2026-10-03': [14]})
        monthly.assign_month(self.f, 2026, 10, self.s)
        self.assertEqual(Reservation.objects.get(beneficiary=self.kids[0]).start_time, datetime.time(14, 0))

    def _res(self, kid, day, hour):
        res, _ = services.create_reservation(self.f, kid, day, start_time=datetime.time(hour, 0), notify=False)
        return res

    def test_swap_two_children(self):
        d1, d2 = datetime.date(2026, 10, 3), datetime.date(2026, 10, 10)
        a, b = self._res(self.kids[0], d1, 9), self._res(self.kids[1], d2, 14)
        before = ReservationNotice.objects.count()
        monthly.swap_reservations(a, b)
        a.refresh_from_db(), b.refresh_from_db()
        self.assertEqual((a.date, a.start_time.hour), (d2, 14))
        self.assertEqual((b.date, b.start_time.hour), (d1, 9))
        self.assertEqual(ReservationNotice.objects.count(), before)      # お知らせは積まない
        # 同じ日の中での入れ替え（時刻だけ交換）
        c = self._res(self.kids[2], d1, 10)
        monthly.swap_reservations(b, c)
        b.refresh_from_db(), c.refresh_from_db()
        self.assertEqual((b.start_time.hour, c.start_time.hour), (10, 9))

    def test_swap_refuses_double_booking_and_waitlist(self):
        d1, d2 = datetime.date(2026, 10, 3), datetime.date(2026, 10, 10)
        a, b = self._res(self.kids[0], d1, 9), self._res(self.kids[1], d2, 9)
        self._res(self.kids[0], d2, 14)                     # 青木は 10/10 にも予約がある
        with self.assertRaises(services.ReservationError):
            monthly.swap_reservations(a, b)
        a.refresh_from_db()
        self.assertEqual(a.date, d1)
        b.status = Reservation.STATUS_WAITLIST
        b.save()
        with self.assertRaises(services.ReservationError):
            monthly.swap_reservations(a, b)

    def test_move_on_schedule(self):
        d1, d2 = datetime.date(2026, 10, 3), datetime.date(2026, 10, 10)
        a = self._res(self.kids[0], d1, 9)
        for k in self.kids[1:4]:
            self._res(k, d2, 9)
        with self.assertRaises(services.ReservationError):   # 満員の枠へは移さない
            monthly.move_on_schedule(a, d2, 9)
        with self.assertRaises(services.ReservationError):   # 枠の無い時刻（休憩）
            monthly.move_on_schedule(a, d2, 12)
        before = ReservationNotice.objects.count()
        a = monthly.move_on_schedule(a, d2, 10)
        self.assertEqual((a.date, a.start_time.hour, a.status), (d2, 10, 'confirmed'))
        self.assertEqual(ReservationNotice.objects.count(), before)

    def test_swap_rewrites_pending_month_notice(self):
        monthly.save_request(self.f, self.kids[0], 2026, 10, 1, {'2026-10-03': [9]})
        monthly.assign_month(self.f, 2026, 10, self.s)
        notice = ReservationNotice.objects.get(customer=self.customer)
        self.assertIn('10月3日(土) 9:00', notice.body)
        a = Reservation.objects.get(beneficiary=self.kids[0])
        b = self._res(self.kids[1], datetime.date(2026, 10, 10), 14)
        monthly.swap_reservations(a, b)
        monthly.refresh_month_notice(self.f, self.kids[0], 2026, 10, self.s)
        notice.refresh_from_db()
        self.assertIn('10月10日(土) 14:00', notice.body)
        self.assertNotIn('10月3日', notice.body)
        # 送信ずみなら書き直さない
        notice.status = ReservationNotice.STATUS_SENT
        notice.save()
        monthly.swap_reservations(a, b)
        self.assertIsNone(monthly.refresh_month_notice(self.f, self.kids[0], 2026, 10, self.s))


class RyoikuScreenTests(TestCase):
    def setUp(self):
        self.f, self.s = ryoiku()
        self.user = StaffAccount.objects.create_user('ryo', password='pw12345678', facility=self.f,
                                                     role=StaffAccount.ROLE_ADMIN)
        self.client.login(username='ryo', password='pw12345678')
        self.kid = child(self.f, '青木')

    def test_request_edit_and_list(self):
        url = reverse('reservations:monthly_request_edit', args=[2026, 10, self.kid.pk])
        res = self.client.get(url)
        self.assertContains(res, '終日')
        res = self.client.post(url, {'desired_count': '2', 'note': '午前がよい',
                                     'all_2026-10-03': '1', 'h_2026-10-02': ['10', '11'], 'h_2026-10-05': ['10']})
        self.assertRedirects(res, reverse('reservations:monthly_requests', args=[2026, 10]))
        req = MonthlyRequest.objects.get(beneficiary=self.kid)
        self.assertEqual(req.wishes, {'2026-10-03': 'all', '2026-10-02': [10, 11]})    # 月曜は落ちる
        self.assertEqual((req.desired_count, req.note), (2, '午前がよい'))
        res = self.client.get(reverse('reservations:monthly_requests', args=[2026, 10]))
        self.assertContains(res, '青木 子')
        self.assertContains(res, '2 回')
        # 一覧の「月間予定表を作る」で割り当て
        res = self.client.post(reverse('reservations:monthly_requests', args=[2026, 10]))
        self.assertRedirects(res, reverse('reservations:monthly_schedule', args=[2026, 10]))
        self.assertEqual(Reservation.objects.filter(beneficiary=self.kid, status='confirmed').count(), 2)
        res = self.client.get(reverse('reservations:monthly_schedule', args=[2026, 10]))
        self.assertContains(res, '青木 子')
        res = self.client.get(reverse('reservations:monthly_schedule_pdf', args=[2026, 10]) + '?fmt=html')
        self.assertContains(res, '月間予定表')
        res = self.client.get(reverse('reservations:monthly_request_form', args=[2026, 10]) + f'?fmt=html&b={self.kid.pk}')
        self.assertContains(res, '10月予約利用希望')
        self.assertContains(res, '月曜日・木曜日はお休み')

    def test_day_screen_add_with_time(self):
        day = datetime.date(2026, 10, 2)
        url = reverse('reservations:day', args=[2026, 10, 2])
        res = self.client.get(url + '?hour=14')
        self.assertContains(res, '時間枠')
        res = self.client.post(url, {'action': 'add', 'beneficiary': self.kid.pk, 'start_time': '14'})
        self.assertRedirects(res, url)
        r = Reservation.objects.get(beneficiary=self.kid)
        self.assertEqual((r.date, r.start_time), (day, datetime.time(14, 0)))
        res = self.client.get(url)
        self.assertContains(res, '14:00')
        self.assertContains(res, reverse('therapy:child', args=[self.kid.pk]))

    def test_slot_mode_required_for_monthly_pages(self):
        self.s.slot_mode = False
        self.s.save()
        res = self.client.get(reverse('reservations:monthly_requests', args=[2026, 10]))
        self.assertRedirects(res, reverse('reservations:calendar'))

    def test_settings_save_slot_fields(self):
        res = self.client.post(reverse('reservations:settings'), {
            'capacity': '10', 'booking_mode': 'auto', 'booking_from_days': '1', 'booking_until_days': '60',
            'slot_mode': 'on', 'slot_capacity': '2', 'slot_minutes': '45',
            'weekday_first_hour': '9', 'weekday_last_hour': '17', 'holiday_first_hour': '9', 'holiday_last_hour': '16',
            'break_hours': '12, 13', 'closed_weekdays': ['0'],
        })
        self.assertRedirects(res, reverse('reservations:calendar'))
        self.s.refresh_from_db()
        self.assertEqual((self.s.slot_capacity, self.s.weekday_first_hour, self.s.break_hours, self.s.closed_weekdays),
                         (2, 9, [12, 13], [0]))


    def test_schedule_swap_and_move_screen(self):
        other = child(self.f, '井上')
        d1, d2 = datetime.date(2026, 10, 3), datetime.date(2026, 10, 10)
        a, _ = services.create_reservation(self.f, self.kid, d1, start_time=datetime.time(9, 0), notify=False)
        b, _ = services.create_reservation(self.f, other, d2, start_time=datetime.time(10, 0), notify=False)
        page = self.client.get(reverse('reservations:monthly_schedule', args=[2026, 10]))
        self.assertContains(page, '入れ替え・移動')
        self.assertContains(page, f'data-res="{a.pk}"')
        url = reverse('reservations:monthly_schedule_swap', args=[2026, 10])
        res = self.client.post(url, {'action': 'swap', 'a': a.pk, 'b': b.pk}, follow=True)
        self.assertContains(res, '入れ替えました')
        a.refresh_from_db()
        self.assertEqual((a.date, a.start_time.hour), (d2, 10))
        res = self.client.post(url, {'action': 'move', 'a': a.pk, 'date': '2026-10-17', 'hour': '11'}, follow=True)
        self.assertContains(res, '移しました')
        a.refresh_from_db()
        self.assertEqual((a.date, a.start_time.hour), (datetime.date(2026, 10, 17), 11))
        res = self.client.post(url, {'action': 'move', 'a': a.pk, 'date': '2026-10-19', 'hour': '11'}, follow=True)
        self.assertContains(res, '休業日')                              # 月曜はお休み
        # ほかの事業所の予約は触れない
        f2 = Facility.objects.create(name='ほか', use_reservation=True)
        x, _ = services.create_reservation(f2, child(f2, '他'), d1, notify=False)
        self.assertEqual(self.client.post(url, {'action': 'swap', 'a': x.pk, 'b': a.pk}).status_code, 404)

class CustomerWishPageTests(TestCase):
    def setUp(self):
        self.f, self.s = ryoiku()
        self.kid = child(self.f, '青木')
        self.customer = Customer.objects.create(facility=self.f, name='青木 母')
        self.customer.children.add(self.kid)
        self.url = reverse('reservations_public:customer', args=[self.customer.token])

    def test_wish_form_shown_and_saved(self):
        res = self.client.get(self.url)
        self.assertContains(res, '月の利用希望を送る')
        today = datetime.date.today()
        y, m = monthly.next_month(today.year, today.month)
        first = datetime.date(y, m, 1)
        # 来月の最初の「お休みでない日」
        day = next(d for d in monthly.month_days(y, m) if self.s.slot_hours(d))
        hour = self.s.slot_hours(day)[0]
        res = self.client.post(self.url, {'action': 'wish', 'beneficiary': self.kid.pk, 'wish_year': y, 'wish_month': m,
                                          'desired_count': '3', f'h_{day.isoformat()}': [str(hour)]})
        self.assertEqual(res.status_code, 302)
        req = MonthlyRequest.objects.get(beneficiary=self.kid, year=y, month=m)
        self.assertEqual((req.source, req.customer, req.wishes), ('web', self.customer, {day.isoformat(): [hour]}))
        # 過ぎた月は受けない
        res = self.client.post(self.url, {'action': 'wish', 'beneficiary': self.kid.pk, 'wish_year': first.year - 1,
                                          'wish_month': 1, 'desired_count': '1'}, follow=True)
        self.assertContains(res, '受け付けられません')

    def test_booking_with_time(self):
        today = datetime.date.today()
        day = next(d for d in (today + datetime.timedelta(days=i) for i in range(1, 10)) if self.s.slot_hours(d))
        hour = self.s.slot_hours(day)[0]
        res = self.client.post(self.url, {'action': 'book', 'beneficiary': self.kid.pk, 'date': day.isoformat(),
                                          'start_time': str(hour)}, follow=True)
        self.assertContains(res, 'ご予約を承りました')
        self.assertEqual(Reservation.objects.get(beneficiary=self.kid).start_time, datetime.time(hour, 0))


class WishAskTests(TestCase):
    """保護者に入力ページの URL を配る"""

    def setUp(self):
        from beneficiaries.models import Guardian
        self.f, self.s = ryoiku()
        self.user = StaffAccount.objects.create_user('ryo', password='pw12345678', facility=self.f,
                                                     role=StaffAccount.ROLE_ADMIN)
        self.client.login(username='ryo', password='pw12345678')
        self.aoki, self.inoue, self.ueda, self.eguchi = (child(self.f, n) for n in ('青木', '井上', '上田', '江口'))
        self.line_mom = Customer.objects.create(facility=self.f, name='青木 母', line_user_id='U-aoki')
        self.line_mom.children.add(self.aoki)
        self.paper_mom = Customer.objects.create(facility=self.f, name='井上 母')
        self.paper_mom.children.add(self.inoue)
        # 上田・江口は顧客が無い。上田は保護者台帳に LINE つきの母、江口は台帳も無い
        Guardian.objects.create(beneficiary=self.ueda, last_name='上田', first_name='花子', phone='090-1111-2222',
                                is_primary=True)
        self.url = reverse('reservations:monthly_wish_ask', args=[2026, 10])

    def test_targets_and_ask(self):
        rows, no_contact = monthly.wish_targets(self.f, 2026, 10, 'http://t.example/')
        self.assertEqual({r['customer'] for r in rows}, {self.line_mom, self.paper_mom})
        self.assertEqual(set(no_contact), {self.ueda, self.eguchi})
        aoki_row = next(r for r in rows if r['customer'] == self.line_mom)
        self.assertEqual(aoki_row['url'], f'http://t.example/yoyaku/mypage/{self.line_mom.token}/?wish=2026-10#wishCard')
        # 井上は用紙が届いている → 「まだの方だけ」なら青木の母だけ
        monthly.save_request(self.f, self.inoue, 2026, 10, 2, {'2026-10-03': 'all'})
        made = monthly.ask_for_wishes(self.f, 2026, 10, self.s, base='http://t.example/',
                                      deadline=datetime.date(2026, 9, 20))
        self.assertEqual([n.customer for n in made], [self.line_mom])
        n = made[0]
        self.assertEqual((n.kind, n.status, n.to_line_id), ('wish', 'pending', 'U-aoki'))
        self.assertIn('10月のご利用希望の入力をお願いします（青木 子さん）', n.body)
        self.assertIn('9月20日(日)までに', n.body)
        self.assertIn(aoki_row['url'], n.body)
        # もう一度積むと、まだ送っていないお願いは置き換える（重ならない）
        monthly.ask_for_wishes(self.f, 2026, 10, self.s, base='http://t.example/', only_missing=False)
        self.assertEqual(ReservationNotice.objects.filter(kind='wish', customer=self.line_mom).count(), 1)
        paper = ReservationNotice.objects.get(kind='wish', customer=self.paper_mom)
        self.assertEqual(paper.status, 'manual')           # LINE が無い方は「コピーして送る」

    def test_make_contacts_from_guardians(self):
        from beneficiaries.models import Guardian
        # 江口のきょうだい（江口 弟）も同じ母。LINE でつながっている
        Guardian.objects.create(beneficiary=self.eguchi, last_name='江口', first_name='母', line_user_id='U-eguchi',
                                line_linked=True, is_primary=True)
        brother = child(self.f, '江口', '弟')
        Guardian.objects.create(beneficiary=brother, last_name='江口', first_name='母', line_user_id='U-eguchi',
                                line_linked=True, is_primary=True)
        _, no_contact = monthly.wish_targets(self.f, 2026, 10)
        made, joined = monthly.make_contacts(self.f, sorted(no_contact, key=lambda b: b.pk))
        self.assertEqual(len(made), 2)
        self.assertEqual(len(joined), 1)
        eguchi = Customer.objects.get(line_user_id='U-eguchi')
        self.assertEqual(set(eguchi.children.all()), {self.eguchi, brother})
        ueda = Customer.objects.get(children=self.ueda)
        self.assertEqual((ueda.name, ueda.phone, ueda.line_user_id), ('上田 花子', '090-1111-2222', ''))
        self.assertEqual(monthly.wish_targets(self.f, 2026, 10)[1], [])

    @mock.patch('line_integration.sending.push_text', return_value=(True, ''))
    def test_screen_ask_sends_line_only_for_wish(self, push):
        # ほかの送信待ち（ご利用日が決まりました など）は一緒に送らない
        other = services.queue_notice(self.f, ReservationNotice.KIND_ACCEPTED, self.s, customer=self.line_mom,
                                      day=datetime.date(2026, 10, 1), body='別の通知')
        page = self.client.get(reverse('reservations:monthly_requests', args=[2026, 10]))
        self.assertContains(page, '保護者に入力してもらう')
        self.assertContains(page, '上田 子')                # 連絡先の無い方
        self.assertContains(page, f'/yoyaku/mypage/{self.paper_mom.token}/?wish=2026-10')
        res = self.client.post(self.url, {'action': 'ask', 'deadline': '2026-09-20', 'scope': 'missing'}, follow=True)
        self.assertContains(res, 'LINE で 1 件送りました')
        self.assertContains(res, '文面をコピー')
        self.assertEqual(push.call_count, 1)
        self.assertEqual(push.call_args.args[1], 'U-aoki')
        other.refresh_from_db()
        self.assertEqual(other.status, 'pending')
        # 手渡しのお願いを「送った」にする
        paper = ReservationNotice.objects.get(kind='wish', customer=self.paper_mom)
        self.client.post(self.url, {'action': 'handed', 'notice': paper.pk})
        paper.refresh_from_db()
        self.assertEqual(paper.status, 'sent')
        # 連絡先を作る
        self.client.post(self.url, {'action': 'contacts'})
        self.assertTrue(Customer.objects.filter(children=self.eguchi).exists())

    def test_parent_input_reaches_list_and_staff_group(self):
        self.s.notify_group_id = 'G-staff'
        self.s.save()
        sister = child(self.f, '青木', '妹')
        self.line_mom.children.add(sister)
        today = datetime.date.today()
        y, m = monthly.next_month(today.year, today.month)
        page_url = reverse('reservations_public:customer', args=[self.line_mom.token])
        page = self.client.get(page_url + f'?wish={y}-{m:02d}')
        self.assertContains(page, '（まだ）')
        day = next(d for d in monthly.month_days(y, m) if self.s.slot_hours(d))
        res = self.client.post(page_url, {'action': 'wish', 'beneficiary': self.aoki.pk, 'wish_year': y, 'wish_month': m,
                                          'desired_count': '2', f'all_{day.isoformat()}': '1'})
        # 送ったあとは、まだのきょうだいの欄へ
        self.assertEqual(res['Location'], f'{page_url}?wish={y}-{m:02d}&child={sister.pk}#wishCard')
        req = MonthlyRequest.objects.get(beneficiary=self.aoki, year=y, month=m)
        self.assertEqual((req.source, req.desired_count, req.wishes), ('web', 2, {day.isoformat(): 'all'}))
        group = ReservationNotice.objects.get(kind='group', to_line_id='G-staff')
        self.assertIn('青木 子さんの利用希望が届きました', group.body)
        self.assertNotIn('母', group.body)
        # 職員の一覧に「保護者が入力」で出る
        lst = self.client.get(reverse('reservations:monthly_requests', args=[y, m]))
        self.assertContains(lst, '保護者が入力')
        # 予定を組んだあとに直すと、その旨を返す
        monthly.assign_month(self.f, y, m, self.s, notify=False)
        res = self.client.post(page_url, {'action': 'wish', 'beneficiary': self.aoki.pk, 'wish_year': y, 'wish_month': m,
                                          'desired_count': '1', f'all_{day.isoformat()}': '1'}, follow=True)
        self.assertContains(res, 'すでに組んでいる')
        self.assertTrue(ReservationNotice.objects.filter(kind='group', body__contains='直されました').exists())


_SCAN_TMP = __import__('tempfile').mkdtemp()


def _photo(name='form.png'):
    import io
    from PIL import Image
    from django.core.files.uploadedfile import SimpleUploadedFile
    buf = io.BytesIO()
    Image.new('RGB', (60, 80), (255, 255, 255)).save(buf, format='PNG')
    return SimpleUploadedFile(name, buf.getvalue(), content_type='image/png')


def _ai_reply(payload):
    from types import SimpleNamespace
    import json
    return SimpleNamespace(stop_reason='end_turn', content=[
        SimpleNamespace(type='thinking', thinking=''),
        SimpleNamespace(type='text', text=json.dumps(payload, ensure_ascii=False))])


READ = {'name': '青木 子', 'year': 2026, 'month': 10, 'desired_count': 4,
        'days': [{'day': 2, 'all_day': False, 'hours': [10, 11, 9]},
                 {'day': 3, 'all_day': True, 'hours': []},
                 {'day': 5, 'all_day': True, 'hours': []}],
        'note': '午前がよい', 'unreadable': '12日の行はかすれている'}


@override_settings(MEDIA_ROOT=_SCAN_TMP, ANTHROPIC_API_KEY='test-key')
class RequestScanTests(TestCase):
    """紙の利用希望の写真を読み取って反映する"""

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        import shutil
        shutil.rmtree(_SCAN_TMP, ignore_errors=True)

    def setUp(self):
        self.f, self.s = ryoiku()
        self.user = StaffAccount.objects.create_user('ryo', password='pw12345678', facility=self.f,
                                                     role=StaffAccount.ROLE_ADMIN)
        self.client.login(username='ryo', password='pw12345678')
        self.kid = child(self.f, '青木')
        self.other = child(self.f, '井上')

    def test_to_wishes_drops_closed_days_and_missing_hours(self):
        from . import scan
        out = scan.normalize(READ, self.f, 2026, 10, self.s)
        self.assertEqual(out['wishes'], {'2026-10-02': [10, 11], '2026-10-03': 'all'})
        self.assertEqual(out['ignored'], ['2日 9時（枠の無い時刻）', '5日（お休みの日）'])
        self.assertEqual((out['desired_count'], out['note'], out['month_mismatch']), (4, '午前がよい', False))
        self.assertEqual(out['slot_count'], 2 + len(self.s.slot_hours(datetime.date(2026, 10, 3))))
        unknown = scan.normalize(dict(READ, desired_count=-1, month=11, days=[]), self.f, 2026, 10, self.s)
        self.assertIsNone(unknown['desired_count'])
        self.assertTrue(unknown['month_mismatch'])
        cal = scan.calendar_text(self.f, 2026, 10, self.s).splitlines()
        self.assertEqual((cal[0], cal[2]), ('1日(木) お休み', '3日(土) 9時、10時、11時、13時、14時、15時、16時、17時'))

    @mock.patch('reservations.scan.anthropic.Anthropic')
    def test_upload_read_review_and_save(self, client_cls):
        client_cls.return_value.messages.create.return_value = _ai_reply(READ)
        lst = reverse('reservations:monthly_requests', args=[2026, 10])
        res = self.client.post(reverse('reservations:request_scan_upload', args=[2026, 10]),
                               {'images': [_photo('a.png'), _photo('b.png')]})
        self.assertRedirects(res, lst + '#scans', fetch_redirect_response=False)
        scans = list(RequestScan.objects.order_by('pk'))
        self.assertEqual(len(scans), 2)
        page = self.client.get(lst)
        self.assertContains(page, '紙の用紙を写真で取り込む')
        self.assertContains(page, 'AIで読み取る')
        # 写真はこの事業所の職員だけが見られる
        self.assertEqual(self.client.get(scans[0].image.url).status_code, 200)

        res = self.client.post(reverse('reservations:request_scan_extract', args=[2026, 10, scans[0].pk]))
        data = res.json()
        self.assertEqual(res.status_code, 200, data)
        self.assertEqual((data['beneficiary_id'], data['desired_count'], data['days']), (self.kid.pk, 4, 2))
        edit = reverse('reservations:monthly_request_edit', args=[2026, 10, self.kid.pk])
        self.assertEqual(data['review_url'], f'{edit}?scan={scans[0].pk}')
        # 画像と、どの月の用紙かを AI に渡している
        kwargs = client_cls.return_value.messages.create.call_args.kwargs
        content = kwargs['messages'][0]['content']
        self.assertEqual(content[0]['type'], 'image')
        self.assertIn('2026年10月', content[1]['text'])
        self.assertIn('5日(月) お休み', content[1]['text'])
        self.assertEqual(kwargs['output_config']['format']['type'], 'json_schema')

        # 確認画面：写真と、読み取った○が入った表（まだ保存しない）
        page = self.client.get(data['review_url'])
        self.assertContains(page, '写真から読み取りました')
        self.assertContains(page, '5日（お休みの日）')
        self.assertContains(page, 'name="all_2026-10-03" value="1" class="allday" checked')
        self.assertContains(page, 'value="4"')
        self.assertFalse(MonthlyRequest.objects.exists())

        # 保存すると利用希望に入り、写真は反映ずみ。次の写真（氏名が読めた）があればそこへ進む
        scans[1].beneficiary, scans[1].status, scans[1].extracted = self.other, RequestScan.STATUS_EXTRACTED, {'wishes': {}}
        scans[1].save()
        res = self.client.post(edit, {'scan': scans[0].pk, 'desired_count': '4', 'note': '午前がよい',
                                      'all_2026-10-03': '1', 'h_2026-10-02': ['10', '11']})
        self.assertRedirects(res, reverse('reservations:monthly_request_edit', args=[2026, 10, self.other.pk])
                             + f'?scan={scans[1].pk}', fetch_redirect_response=False)
        req = MonthlyRequest.objects.get(beneficiary=self.kid, year=2026, month=10)
        self.assertEqual((req.source, req.desired_count, req.wishes),
                         ('photo', 4, {'2026-10-02': [10, 11], '2026-10-03': 'all'}))
        scans[0].refresh_from_db()
        self.assertEqual((scans[0].status, scans[0].request), ('imported', req))
        self.assertContains(self.client.get(lst), '用紙の写真から')

    @mock.patch('reservations.scan.anthropic.Anthropic')
    def test_unknown_name_then_assign(self, client_cls):
        client_cls.return_value.messages.create.return_value = _ai_reply(dict(READ, name=''))
        self.client.post(reverse('reservations:request_scan_upload', args=[2026, 10]), {'images': [_photo()]})
        sc = RequestScan.objects.get()
        data = self.client.post(reverse('reservations:request_scan_extract', args=[2026, 10, sc.pk])).json()
        self.assertEqual((data['beneficiary_id'], data['review_url']), (None, ''))
        res = self.client.post(reverse('reservations:request_scan_action', args=[2026, 10, sc.pk]),
                               {'action': 'assign', 'beneficiary': self.other.pk})
        self.assertRedirects(res, reverse('reservations:monthly_request_edit', args=[2026, 10, self.other.pk])
                             + f'?scan={sc.pk}', fetch_redirect_response=False)
        sc.refresh_from_db()
        self.assertEqual(sc.beneficiary, self.other)
        # 氏名が別の子なら確認画面で知らせる
        sc.extracted = dict(sc.extracted, name='青木 子')
        sc.save()
        page = self.client.get(reverse('reservations:monthly_request_edit', args=[2026, 10, self.other.pk]) + f'?scan={sc.pk}')
        self.assertContains(page, '「青木 子」さんの用紙かもしれません')
        # 消す
        self.client.post(reverse('reservations:request_scan_action', args=[2026, 10, sc.pk]), {'action': 'delete'})
        self.assertFalse(RequestScan.objects.exists())

    def test_single_upload_from_edit_page_and_errors(self):
        edit = reverse('reservations:monthly_request_edit', args=[2026, 10, self.kid.pk])
        self.assertContains(self.client.get(edit), '用紙の写真から読み取る')
        res = self.client.post(reverse('reservations:request_scan_upload', args=[2026, 10]),
                               {'images': [_photo()], 'beneficiary': self.kid.pk, 'next': 'edit'})
        sc = RequestScan.objects.get()
        self.assertRedirects(res, f'{edit}?scan={sc.pk}', fetch_redirect_response=False)
        self.assertContains(self.client.get(f'{edit}?scan={sc.pk}'), 'AI が用紙を読み取っています')
        with mock.patch('reservations.scan.anthropic.Anthropic', side_effect=RuntimeError('boom')):
            res = self.client.post(reverse('reservations:request_scan_extract', args=[2026, 10, sc.pk]))
        self.assertEqual(res.status_code, 500)
        self.assertIn('読み取りに失敗', res.json()['error'])
        with override_settings(ANTHROPIC_API_KEY=''):
            res = self.client.post(reverse('reservations:request_scan_extract', args=[2026, 10, sc.pk]))
        self.assertIn('ANTHROPIC_API_KEY', res.json()['error'])
        # 画像でないファイルは受けない
        from django.core.files.uploadedfile import SimpleUploadedFile
        res = self.client.post(reverse('reservations:request_scan_upload', args=[2026, 10]),
                               {'images': [SimpleUploadedFile('a.txt', b'x', content_type='text/plain')]}, follow=True)
        self.assertContains(res, '写真（JPEG')
        # ほかの事業所の写真は触れない・見られない
        f2 = Facility.objects.create(name='ほか', use_reservation=True)
        other = RequestScan.objects.create(facility=f2, year=2026, month=10, image=_photo('x.png'))
        self.assertEqual(self.client.post(reverse('reservations:request_scan_extract', args=[2026, 10, other.pk])).status_code, 404)
        self.assertEqual(self.client.get(f'{edit}?scan={other.pk}').status_code, 404)
        self.assertEqual(self.client.get(other.image.url).status_code, 404)

class PresetAndSeedTests(TestCase):
    def test_create_facility_preset(self):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        call_command('create_facility', 'りょういく', '--admin', 'ryoiku', '--password', 'pw12345678',
                     '--preset', 'ryoiku', stdout=out)
        f = Facility.objects.get(name='りょういく')
        self.assertTrue(f.use_reservation and f.use_therapy_record)
        s = services.get_setting(f)
        self.assertTrue(s.slot_mode)
        self.assertEqual((s.slot_capacity, s.closed_weekdays, s.weekday_first_hour, s.holiday_first_hour), (3, [0, 3], 10, 9))

    def test_seed_demo_for_slot_facility(self):
        from io import StringIO
        from django.core.management import call_command
        f, s = ryoiku()
        StaffAccount.objects.create_user('ryo', password='pw12345678', facility=f, role=StaffAccount.ROLE_ADMIN)
        call_command('seed_demo', '--facility', str(f.pk), stdout=StringIO())
        today = datetime.date.today()
        self.assertTrue(MonthlyRequest.objects.filter(facility=f, year=today.year, month=today.month).exists())
        self.assertTrue(Reservation.objects.filter(facility=f, status='confirmed', start_time__isnull=False).exists())
        from therapy.models import TherapyProfile, TherapyRecord
        self.assertTrue(TherapyRecord.objects.filter(facility=f).exists())
        self.assertTrue(TherapyProfile.objects.filter(beneficiary__facility=f).exists())
        # --reset で入れ直せる
        call_command('seed_demo', '--facility', str(f.pk), '--reset', stdout=StringIO())

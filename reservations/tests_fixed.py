"""利用予定曜日から予定を作る（2026-11-09 オウル）：まとめて作成・カレンダーの人数・出欠入力・メニューの名前"""
import datetime

from django.test import TestCase
from django.urls import reverse

from accounts.models import StaffAccount
from beneficiaries.models import Beneficiary
from facilities.models import Facility
from transport.models import TransportAssignment, TransportProfile

from . import fixed, services
from .models import ClosedDate, Reservation


class FixedDaysTests(TestCase):
    def setUp(self):
        self.f = Facility.objects.create(name='児童発達支援センター　オウル', use_reservation=True, use_therapy_record=True,
                                         use_transport=True, layout=Facility.LAYOUT_RYOIKU)
        self.s = services.get_setting(self.f)
        self.s.slot_mode, self.s.slot_capacity, self.s.fixed_days = True, 3, True
        self.s.closed_weekdays = [6]
        self.s.save()
        self.user = StaffAccount.objects.create_user('owl', password='pw12345678', facility=self.f, role=StaffAccount.ROLE_ADMIN)
        self.client.login(username='owl', password='pw12345678')
        # 月水金の子・火木の子・曜日なしの子
        self.a = Beneficiary.objects.create(facility=self.f, last_name='青木', first_name='子', last_name_kana='あおき', date_of_birth=datetime.date(2019, 4, 1),
                                            weekday_mon=True, weekday_wed=True, weekday_fri=True)
        self.b = Beneficiary.objects.create(facility=self.f, last_name='井上', first_name='花', last_name_kana='いのうえ', date_of_birth=datetime.date(2019, 4, 1),
                                            weekday_tue=True, weekday_thu=True)
        self.c = Beneficiary.objects.create(facility=self.f, last_name='上田', first_name='空', last_name_kana='うえだ', date_of_birth=datetime.date(2019, 4, 1))
        TransportProfile.objects.create(beneficiary=self.a, pickup=True, dropoff=True)

    def test_bulk_create_by_weekdays(self):
        # 2026 年 11 月：月水金 13 日・火木 8 日。11/11（水）は休業日、青木さんの 11/2 は手で入れてある
        ClosedDate.objects.create(facility=self.f, date=datetime.date(2026, 11, 11), reason='研修')
        Reservation.objects.create(facility=self.f, beneficiary=self.a, date=datetime.date(2026, 11, 2), start_time=datetime.time(16, 0))
        res = self.client.post(reverse('reservations:bulk_create', args=[2026, 11]), follow=True)
        self.assertContains(res, '予定を 19 件作りました')          # 13 - 2（休業・既存）+ 8
        self.assertContains(res, '利用予定曜日が入っていない')     # 上田さん
        rows = Reservation.objects.filter(facility=self.f, date__month=11, status=Reservation.STATUS_CONFIRMED)
        self.assertEqual(rows.count(), 20)
        self.assertEqual(rows.filter(beneficiary=self.a).count(), 12)
        self.assertFalse(rows.filter(date=datetime.date(2026, 11, 11)).exists())
        self.assertEqual(rows.filter(beneficiary=self.a, date=datetime.date(2026, 11, 2)).get().start_time, datetime.time(16, 0))   # 既存はそのまま
        self.assertEqual(rows.filter(beneficiary=self.b).first().start_time, datetime.time(15, 0))                                  # 既定の時刻
        self.assertFalse(rows.filter(beneficiary=self.c).exists())
        # もう一度押しても増えない
        res = self.client.post(reverse('reservations:bulk_create', args=[2026, 11]), follow=True)
        self.assertContains(res, 'すでにそろっています')
        self.assertEqual(Reservation.objects.filter(facility=self.f, date__month=11).count(), 20)
        # 時刻の設定を変えると、次に作る分に効く
        self.s.fixed_days_time = datetime.time(14, 30)
        self.s.save()
        fixed.bulk_create(self.f, 2026, 12)
        self.assertEqual(Reservation.objects.filter(facility=self.f, date__month=12).first().start_time, datetime.time(14, 30))

    def test_calendar_counts_and_menu(self):
        d = datetime.date(2026, 11, 4)    # 水
        Reservation.objects.create(facility=self.f, beneficiary=self.a, date=d, start_time=datetime.time(15, 0))
        Reservation.objects.create(facility=self.f, beneficiary=self.b, date=d, start_time=datetime.time(15, 0), attendance=Reservation.ATT_ATTENDED)
        Reservation.objects.create(facility=self.f, beneficiary=self.c, date=d, start_time=datetime.time(15, 0), attendance=Reservation.ATT_ABSENT)
        res = self.client.get(reverse('reservations:calendar_month', args=[2026, 11]))
        page = res.content.decode()
        self.assertIn('予定 1', page)
        self.assertIn('来所 1', page)
        self.assertIn('欠席 1', page)
        self.assertIn('11月の予定をまとめて作成', page)
        self.assertIn('作成済み', page)
        self.assertIn(reverse('reservations:attendance_day', args=[2026, 11, 4]), page)
        self.assertIn('<span>予定</span>', page)           # メニューの名前
        self.assertNotIn('<span>予約</span>', page)
        # オフなら元の予約カレンダー（残枠）に戻る
        self.s.fixed_days = False
        self.s.save()
        page = self.client.get(reverse('reservations:calendar_month', args=[2026, 11])).content.decode()
        self.assertNotIn('11月の予定をまとめて作成', page)
        self.assertIn('<span>予約</span>', page)
        self.assertRedirects(self.client.get(reverse('reservations:attendance_day', args=[2026, 11, 4])), reverse('reservations:calendar'))

    def test_attendance_day_save(self):
        d = datetime.date(2026, 11, 4)
        url = reverse('reservations:attendance_day', args=[2026, 11, 4])
        ra = Reservation.objects.create(facility=self.f, beneficiary=self.a, date=d, start_time=datetime.time(15, 0))
        rb = Reservation.objects.create(facility=self.f, beneficiary=self.b, date=d, start_time=datetime.time(15, 0), status=Reservation.STATUS_WAITLIST)
        res = self.client.get(url)
        self.assertContains(res, '出欠入力（3 人）')
        self.assertContains(res, '青木 子')
        self.assertContains(res, 'name="b_%d_pickup"' % self.a.pk)
        self.assertContains(res, '予定の人を全員「来所」に')
        # 青木：来所・迎えを外す。井上（キャンセル待ち）：欠席。上田（予約なし）：予定にする → 予約が作られる。送りは「この日だけ」
        post = {f'b_{self.a.pk}_state': 'attended', f'b_{self.a.pk}_dropoff': '1', f'b_{self.a.pk}_note': '祖母が迎え',
                f'b_{self.b.pk}_state': 'absent', f'b_{self.b.pk}_note': '',
                f'b_{self.c.pk}_state': 'scheduled', f'b_{self.c.pk}_dropoff': '1', f'b_{self.c.pk}_note': ''}
        res = self.client.post(url, post, follow=True)
        self.assertContains(res, '出欠を保存しました')
        ra.refresh_from_db()
        rb.refresh_from_db()
        self.assertEqual((ra.attendance, ra.note), (Reservation.ATT_ATTENDED, '祖母が迎え'))
        self.assertEqual((rb.attendance, rb.status), (Reservation.ATT_ABSENT, Reservation.STATUS_CONFIRMED))
        rc = Reservation.objects.get(facility=self.f, beneficiary=self.c, date=d)
        self.assertEqual((rc.status, rc.attendance, rc.start_time), (Reservation.STATUS_CONFIRMED, '', datetime.time(15, 0)))
        # 配車表：青木の迎えは skip、送りはそのまま。上田の送りは「この日だけ」
        self.assertTrue(TransportAssignment.objects.get(reservation=ra, direction='pickup').skip)
        self.assertFalse(TransportAssignment.objects.filter(reservation=ra, direction='dropoff', skip=True).exists())
        self.assertTrue(TransportAssignment.objects.get(reservation=rc, direction='dropoff').added)
        from transport.views import build_board
        board = build_board(self.f, d)
        self.assertEqual({x['beneficiary'].pk for x in board['pickup'] if not x['skip']}, set())
        self.assertEqual({x['beneficiary'].pk for x in board['dropoff'] if not x['skip']}, {self.a.pk, self.c.pk})
        # 画面にも反映（迎えのチェックは外れ、送りが付く）
        rows = {r['beneficiary'].pk: r for r in fixed.day_rows(self.f, d)}
        self.assertEqual((rows[self.a.pk]['has_pickup'], rows[self.a.pk]['has_dropoff'], rows[self.c.pk]['has_dropoff']), (False, True, True))
        # × で予定なし → 取消。迎えを付け直すと skip が外れる。「この日だけ」の送りは外すと消える
        post = {f'b_{self.a.pk}_state': 'scheduled', f'b_{self.a.pk}_pickup': '1', f'b_{self.a.pk}_dropoff': '1', f'b_{self.a.pk}_note': '',
                f'b_{self.b.pk}_state': '', f'b_{self.c.pk}_state': 'scheduled', f'b_{self.c.pk}_note': ''}
        self.client.post(url, post)
        ra.refresh_from_db()
        rb.refresh_from_db()
        self.assertEqual(ra.attendance, '')
        self.assertEqual(rb.status, Reservation.STATUS_CANCELLED)
        self.assertFalse(TransportAssignment.objects.get(reservation=ra, direction='pickup').skip)
        self.assertFalse(TransportAssignment.objects.filter(reservation=rc, direction='dropoff').exists())
        # 日誌の週間カレンダー・配車表と同じ予約を見ているので、取消した井上は配車表に出ない
        self.assertNotIn(self.b.pk, {x['beneficiary'].pk for x in build_board(self.f, d)['pickup']})

    def test_setting_form_saves_fixed_days(self):
        res = self.client.post(reverse('reservations:settings'), {'capacity': '10', 'signature': '', 'booking_from_days': '1', 'booking_until_days': '60',
                                                                   'booking_mode': 'auto', 'slot_mode': '1', 'slot_capacity': '3', 'slot_minutes': '45',
                                                                   'weekday_first_hour': '10', 'weekday_last_hour': '18', 'holiday_first_hour': '9', 'holiday_last_hour': '17',
                                                                   'break_hours': '12', 'fixed_days_present': '1', 'fixed_days_time': '14:45'})
        self.assertRedirects(res, reverse('reservations:calendar'))
        self.s.refresh_from_db()
        self.assertEqual((self.s.fixed_days, self.s.fixed_days_time), (False, datetime.time(14, 45)))
        self.assertEqual(self.s.bulk_start_time, datetime.time(14, 45))
        # 時刻の形が違えば止める
        res = self.client.post(reverse('reservations:settings'), {'capacity': '10', 'booking_from_days': '1', 'booking_until_days': '60',
                                                                   'booking_mode': 'auto', 'fixed_days_present': '1', 'fixed_days': '1', 'fixed_days_time': 'abc'}, follow=True)
        self.assertContains(res, '15:00 のように')
        self.s.refresh_from_db()
        self.assertFalse(self.s.fixed_days)

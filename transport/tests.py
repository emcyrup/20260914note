"""送迎・配車のテスト"""
import datetime

from django.test import TestCase
from django.urls import reverse

from accounts.models import StaffAccount
from beneficiaries.models import Beneficiary
from facilities.models import Facility
from reservations.models import Reservation

from .models import Driver, TransportAssignment, TransportProfile, Vehicle


class TransportTests(TestCase):
    def setUp(self):
        self.f = Facility.objects.create(name='児童発達支援センター　オウル', layout=Facility.LAYOUT_RYOIKU,
                                         use_reservation=True, use_transport=True)
        self.user = StaffAccount.objects.create_user('owl', password='pw12345678', facility=self.f,
                                                     role=StaffAccount.ROLE_ADMIN, display_name='大和')
        self.client.login(username='owl', password='pw12345678')
        self.day = datetime.date(2026, 10, 7)
        self.kid = Beneficiary.objects.create(facility=self.f, last_name='青木', first_name='子', last_name_kana='あおき',
                                              date_of_birth=datetime.date(2019, 4, 1))
        self.kid2 = Beneficiary.objects.create(facility=self.f, last_name='伊藤', first_name='花', last_name_kana='いとう',
                                               date_of_birth=datetime.date(2018, 4, 1))
        self.van = Vehicle.objects.create(facility=self.f, name='ハイエース', capacity=1)
        self.drv = Driver.objects.create(facility=self.f, name='佐藤')
        self.van.default_driver = self.drv
        self.van.save()
        TransportProfile.objects.create(beneficiary=self.kid, pickup=True, pickup_place='南小学校', pickup_time=datetime.time(14, 30),
                                        dropoff=True, dropoff_place='自宅', default_vehicle=self.van, note='チャイルドシート')
        TransportProfile.objects.create(beneficiary=self.kid2, pickup=True, pickup_place='北小学校', default_vehicle=self.van)
        self.r1 = Reservation.objects.create(facility=self.f, beneficiary=self.kid, date=self.day, start_time=datetime.time(15, 0))
        self.r2 = Reservation.objects.create(facility=self.f, beneficiary=self.kid2, date=self.day, start_time=datetime.time(15, 0))
        self.url = reverse('transport:day') + f'?d={self.day.isoformat()}'

    def test_disabled_facility_redirects_and_hides_menu(self):
        self.f.use_transport = False
        self.f.save()
        res = self.client.get(self.url)
        self.assertRedirects(res, reverse('facilities:dashboard'))
        res = self.client.get(reverse('facilities:dashboard'))
        self.assertNotContains(res, '送迎・配車')
        res = self.client.get(reverse('beneficiaries:detail', args=[self.kid.pk]))
        self.assertNotContains(res, 'id="transport"')

    def test_menu_and_board_from_profiles(self):
        res = self.client.get(self.url)
        self.assertContains(res, '送迎・配車')
        self.assertContains(res, '10月7日')
        # 迎え：2 人（設定の場所・時刻・いつもの車両と、その運転手が入る）。送り：青木だけ
        self.assertContains(res, 'name="present_r%d_pickup"' % self.r1.pk)
        self.assertContains(res, 'name="present_r%d_pickup"' % self.r2.pk)
        self.assertContains(res, 'name="present_r%d_dropoff"' % self.r1.pk)
        self.assertNotContains(res, 'name="present_r%d_dropoff"' % self.r2.pk)
        self.assertContains(res, 'value="南小学校"')
        self.assertContains(res, 'value="14:30"')
        self.assertContains(res, 'チャイルドシート')
        # ハイエースは乗れる人数 1 なので、迎え 2 人で赤く出る
        self.assertContains(res, 'text-bg-danger')
        self.assertContains(res, 'ハイエース 2/1')
        # 送りの無い伊藤は「この日だけ送迎を足す」の送りの候補に出る
        self.assertContains(res, 'この日だけ送迎を足す')

    def test_save_board_creates_assignments(self):
        k1, k2 = f'r{self.r1.pk}_pickup', f'r{self.r2.pk}_pickup'
        res = self.client.post(reverse('transport:day'), {
            'action': 'save', 'd': self.day.isoformat(),
            f'present_{k1}': '1', f'time_{k1}': '14:40', f'place_{k1}': '南小学校 正門', f'vehicle_{k1}': self.van.pk,
            f'driver_{k1}': self.drv.pk, f'note_{k1}': '先生に声かけ',
            f'present_{k2}': '1', f'time_{k2}': '', f'vehicle_{k2}': '', f'driver_{k2}': '', f'skip_{k2}': 'on',
        })
        self.assertRedirects(res, self.url)
        a1 = TransportAssignment.objects.get(reservation=self.r1, direction='pickup')
        self.assertEqual((a1.time, a1.place, a1.vehicle, a1.driver, a1.note, a1.skip),
                         (datetime.time(14, 40), '南小学校 正門', self.van, self.drv, '先生に声かけ', False))
        a2 = TransportAssignment.objects.get(reservation=self.r2, direction='pickup')
        self.assertTrue(a2.skip)
        # 送りの行は画面に出ていたが POST に無い（present が無い）→ 触らない
        self.assertFalse(TransportAssignment.objects.filter(reservation=self.r1, direction='dropoff').exists())
        res = self.client.get(self.url)
        self.assertContains(res, 'value="南小学校 正門"')
        self.assertContains(res, 'ハイエース 1/1')          # なし にした伊藤は数えない
        self.assertNotContains(res, 'text-bg-danger')

    def test_add_one_day_transport_and_remove_by_skip(self):
        res = self.client.post(reverse('transport:day'), {'action': 'add', 'd': self.day.isoformat(),
                                                           'reservation': self.r2.pk, 'direction': 'dropoff'})
        self.assertRedirects(res, self.url)
        a = TransportAssignment.objects.get(reservation=self.r2, direction='dropoff')
        self.assertTrue(a.added)
        res = self.client.get(self.url)
        self.assertContains(res, 'name="present_r%d_dropoff"' % self.r2.pk)
        self.assertContains(res, 'この日だけ')
        k = f'r{self.r2.pk}_dropoff'
        self.client.post(reverse('transport:day'), {'action': 'save', 'd': self.day.isoformat(), f'present_{k}': '1', f'skip_{k}': 'on'})
        self.assertFalse(TransportAssignment.objects.filter(reservation=self.r2, direction='dropoff').exists())

    def test_pdf_html_lists_only_active_rows(self):
        TransportAssignment.objects.create(reservation=self.r2, direction='pickup', skip=True)
        res = self.client.get(reverse('transport:day_pdf') + f'?d={self.day.isoformat()}&fmt=html')
        self.assertContains(res, '配車表　2026年10月7日')
        self.assertContains(res, '青木 子')
        self.assertContains(res, '南小学校')
        self.assertContains(res, '佐藤')
        self.assertContains(res, 'チャイルドシート')
        body = res.content.decode()
        self.assertEqual(body.count('伊藤 花'), 0)        # なし の人は印刷に出ない
        self.assertContains(res, '迎え<span class="n">1 人')

    def test_vehicles_and_drivers_crud(self):
        url = reverse('transport:vehicles')
        res = self.client.post(url, {'action': 'driver_add', 'name': '鈴木', 'phone': '090-0000-0000', 'note': '月水金'})
        self.assertRedirects(res, url)
        d = Driver.objects.get(name='鈴木')
        res = self.client.post(url, {'action': 'vehicle_add', 'name': '軽ワゴン', 'capacity': '3', 'plate': '白', 'default_driver': d.pk})
        v = Vehicle.objects.get(name='軽ワゴン')
        self.assertEqual((v.capacity, v.plate, v.default_driver, v.is_active), (3, '白', d, True))
        res = self.client.post(url, {'action': 'vehicle_save', 'pk': v.pk, 'name': '軽ワゴン（白）', 'capacity': '4', 'order': '2'})
        v.refresh_from_db()
        self.assertEqual((v.name, v.capacity, v.order, v.is_active, v.default_driver), ('軽ワゴン（白）', 4, 2, False, None))
        res = self.client.get(url)
        self.assertContains(res, '軽ワゴン（白）')
        self.assertContains(res, '鈴木')
        # 使っていない車両は配車表の選択肢に出ない
        res = self.client.get(self.url)
        self.assertNotContains(res, '軽ワゴン（白）')
        self.client.post(url, {'action': 'vehicle_delete', 'pk': v.pk})
        self.client.post(url, {'action': 'driver_delete', 'pk': d.pk})
        self.assertFalse(Vehicle.objects.filter(pk=v.pk).exists())
        self.assertFalse(Driver.objects.filter(pk=d.pk).exists())
        # 空の名前は登録しない
        self.client.post(url, {'action': 'vehicle_add', 'name': '  '})
        self.assertEqual(Vehicle.objects.count(), 1)

    def test_other_facility_cannot_touch(self):
        g = Facility.objects.create(name='ほか', layout=Facility.LAYOUT_RYOIKU, use_transport=True)
        other = Vehicle.objects.create(facility=g, name='よその車')
        res = self.client.post(reverse('transport:vehicles'), {'action': 'vehicle_delete', 'pk': other.pk})
        self.assertEqual(res.status_code, 404)
        self.assertTrue(Vehicle.objects.filter(pk=other.pk).exists())
        res = self.client.get(self.url)
        self.assertNotContains(res, 'よその車')

    def test_profile_from_beneficiary_detail(self):
        detail = reverse('beneficiaries:detail', args=[self.kid.pk])
        res = self.client.get(detail)
        self.assertContains(res, 'id="transport"')
        self.assertContains(res, 'value="南小学校"')
        self.assertContains(res, 'きょうの配車表')
        res = self.client.post(reverse('transport:profile', args=[self.kid.pk]), {
            'pickup': 'on', 'pickup_place': '南小学校 北門', 'pickup_time': '14:35', 'dropoff_place': '自宅', 'dropoff_time': '',
            'default_vehicle': '', 'note': '',
        })
        self.assertRedirects(res, detail + '#transport')
        p = TransportProfile.objects.get(beneficiary=self.kid)
        self.assertEqual((p.pickup, p.pickup_place, p.pickup_time, p.dropoff, p.default_vehicle, p.note),
                         (True, '南小学校 北門', datetime.time(14, 35), False, None, ''))
        # 送りを外したので、配車表の送りから消える
        res = self.client.get(self.url)
        self.assertNotContains(res, 'name="present_r%d_dropoff"' % self.r1.pk)
        self.assertContains(res, 'value="南小学校 北門"')
        # 送迎の設定が無い利用者でも欄は出る（新しく作れる）
        kid3 = Beneficiary.objects.create(facility=self.f, last_name='上田', first_name='空', date_of_birth=datetime.date(2019, 4, 1))
        res = self.client.get(reverse('beneficiaries:detail', args=[kid3.pk]))
        self.assertContains(res, '送迎の設定を保存')

    def test_settings_toggle_and_copy_fields(self):
        from facilities.services import COPY_FIELDS
        self.assertIn('use_transport', COPY_FIELDS)
        self.assertIn('use_dev_assessment', COPY_FIELDS)
        res = self.client.get(reverse('facilities:settings'))
        self.assertContains(res, 'name="use_transport"')
        self.assertContains(res, 'name="use_dev_assessment"')

    def test_ride_ng_bulk_buttons_and_save_all(self):
        """同乗NG の欄に「全員チェック」「全部外す」と人数。全員を送って保存すれば全員に付き、空で送れば全部外れる"""
        res = self.client.get(reverse('beneficiaries:detail', args=[self.kid.pk]))
        self.assertContains(res, '全員チェック')
        self.assertContains(res, '全部外す')
        self.assertContains(res, 'id="ride-ng-count"')
        others = list(Beneficiary.objects.filter(facility=self.f).exclude(pk=self.kid.pk))
        self.client.post(reverse('transport:profile', args=[self.kid.pk]), {'pickup': '1', 'dropoff': '1', 'pickup_place': '', 'pickup_time': '',
                                                                              'dropoff_place': '', 'dropoff_time': '', 'default_vehicle': '', 'note': '',
                                                                              'ride_ng_present': '1', 'no_ride_with': [str(o.pk) for o in others]})
        self.assertEqual(set(self.kid.no_ride_with.values_list('pk', flat=True)), {o.pk for o in others})
        self.client.post(reverse('transport:profile', args=[self.kid.pk]), {'pickup': '1', 'dropoff': '1', 'pickup_place': '', 'pickup_time': '',
                                                                              'dropoff_place': '', 'dropoff_time': '', 'default_vehicle': '', 'note': '',
                                                                              'ride_ng_present': '1'})
        self.assertEqual(self.kid.no_ride_with.count(), 0)

    def test_ride_ng_pairs_on_board(self):
        """同乗NG：送迎の設定で付け（相手にも付く）、同じ車・近い時刻（30 分以内）なら配車表で赤く知らせる"""
        res = self.client.post(reverse('transport:profile', args=[self.kid2.pk]), {
            'pickup': 'on', 'pickup_place': '北小学校', 'default_vehicle': self.van.pk,
            'ride_ng_present': '1', 'no_ride_with': [self.kid.pk]})
        self.assertEqual(res.status_code, 302)
        self.assertEqual(list(self.kid.no_ride_with.all()), [self.kid2])
        self.assertContains(self.client.get(reverse('beneficiaries:detail', args=[self.kid.pk])), '同じ車に乗せない')
        res = self.client.get(self.url)
        self.assertContains(res, '同乗NG：伊藤 花')
        self.assertContains(res, '同乗NG：青木 子')
        # 保存すると注意も出る
        k1, k2 = f'r{self.r1.pk}_pickup', f'r{self.r2.pk}_pickup'
        post = {'action': 'save', 'd': self.day.isoformat(), f'present_{k1}': '1', f'present_{k2}': '1',
                f'vehicle_{k1}': self.van.pk, f'vehicle_{k2}': self.van.pk, f'time_{k1}': '14:30', f'time_{k2}': '16:00'}
        res = self.client.post(reverse('transport:day'), dict(post, **{f'time_{k2}': '14:45'}), follow=True)
        self.assertContains(res, '同じ車に乗せない組み合わせ')
        # 時刻が離れていれば別の便
        res = self.client.post(reverse('transport:day'), post, follow=True)
        self.assertNotContains(res, '同乗NG：')
        # 別の車なら出ない
        van2 = Vehicle.objects.create(facility=self.f, name='軽')
        res = self.client.post(reverse('transport:day'), dict(post, **{f'time_{k2}': '14:30', f'vehicle_{k2}': van2.pk}), follow=True)
        self.assertNotContains(res, '同乗NG：')


class DriverVehicleAndAutoAssignTests(TestCase):
    """運転できる車両・自動の割り当て・学校の住所・配車担当のホーム（2026-11-09 オウル）"""

    def setUp(self):
        self.f = Facility.objects.create(name='児童発達支援センター　オウル', layout=Facility.LAYOUT_RYOIKU,
                                         use_reservation=True, use_transport=True)
        self.user = StaffAccount.objects.create_user('owl', password='pw12345678', facility=self.f, role=StaffAccount.ROLE_ADMIN, display_name='大和')
        self.driver_user = StaffAccount.objects.create_user('drv', password='pw12345678', facility=self.f, role=StaffAccount.ROLE_STAFF, display_name='佐藤')
        self.client.login(username='owl', password='pw12345678')
        self.day = datetime.date.today()
        from beneficiaries.models import School
        self.school = School.objects.create(facility=self.f, name='南小学校', address='見本市南町 1-1')
        self.k1 = Beneficiary.objects.create(facility=self.f, last_name='青木', first_name='子', last_name_kana='あおき', date_of_birth=datetime.date(2019, 4, 1),
                                             school_name='南小学校', school=self.school, address='見本市北町 9-9')
        self.k2 = Beneficiary.objects.create(facility=self.f, last_name='伊藤', first_name='花', last_name_kana='いとう', date_of_birth=datetime.date(2018, 4, 1))
        self.k3 = Beneficiary.objects.create(facility=self.f, last_name='上田', first_name='空', last_name_kana='うえだ', date_of_birth=datetime.date(2018, 4, 1))
        self.van = Vehicle.objects.create(facility=self.f, name='ハイエース', capacity=2, order=1)
        self.kei = Vehicle.objects.create(facility=self.f, name='軽', capacity=1, order=2)
        self.drv = Driver.objects.create(facility=self.f, name='佐藤', staff=self.driver_user)
        self.drv2 = Driver.objects.create(facility=self.f, name='鈴木')
        self.van.default_driver = self.drv
        self.van.save()
        self.kei.default_driver = self.drv2
        self.kei.save()
        for k in (self.k1, self.k2, self.k3):
            TransportProfile.objects.create(beneficiary=k, pickup=True, dropoff=(k is self.k1), pickup_time=datetime.time(14, 30))
        self.rs = [Reservation.objects.create(facility=self.f, beneficiary=k, date=self.day, start_time=datetime.time(15, 0)) for k in (self.k1, self.k2, self.k3)]
        self.url = reverse('transport:day') + f'?d={self.day.isoformat()}'

    def test_pickup_time_defaults_to_school_dismissal(self):
        # 迎えの時刻が設定に無い子は、学校のその曜日の下校時刻が配車表の時刻に入り「下校 ○:○○」が出る
        self.school.dismissal_time = datetime.time(14, 50)
        setattr(self.school, self.school.WEEKDAY_FIELDS[self.day.weekday()] if self.day.weekday() < 5 else 'dismissal_time', datetime.time(13, 40))
        self.school.save()
        TransportProfile.objects.filter(beneficiary=self.k1).update(pickup_time=None)
        from .views import build_board
        board = build_board(self.f, self.day)
        row = next(x for x in board['pickup'] if x['beneficiary'] == self.k1)
        self.assertEqual(row['time'], datetime.time(13, 40))
        self.assertEqual(row['dismissal'], datetime.time(13, 40))
        res = self.client.get(self.url)
        self.assertContains(res, '下校 13:40')
        # 設定に時刻があればそちら
        TransportProfile.objects.filter(beneficiary=self.k1).update(pickup_time=datetime.time(14, 30))
        self.assertEqual(next(x for x in build_board(self.f, self.day)['pickup'] if x['beneficiary'] == self.k1)['time'], datetime.time(14, 30))

    def test_driver_vehicles_saved_and_flagged(self):
        # 佐藤は軽しか運転できない → ハイエースの運転手になっている行が赤く出る
        self.client.post(reverse('transport:vehicles'), {'action': 'driver_save', 'pk': self.drv.pk, 'name': '佐藤', 'is_active': '1',
                                                         'vehicles_present': '1', 'vehicles': [str(self.kei.pk)], 'staff': str(self.driver_user.pk)})
        self.drv.refresh_from_db()
        self.assertTrue(self.drv.can_drive(self.kei))
        self.assertFalse(self.drv.can_drive(self.van))
        self.assertEqual(self.drv.staff, self.driver_user)
        self.assertTrue(Driver.objects.create(facility=self.f, name='だれでも').can_drive(self.van))
        page = self.client.get(reverse('transport:vehicles')).content.decode()
        self.assertIn('運転できる車両', page)
        self.assertIn('ログインする職員', page)
        k = f'r{self.rs[0].pk}_pickup'
        self.client.post(reverse('transport:day'), {'action': 'save', 'd': self.day.isoformat(), f'present_{k}': '1',
                                                    f'vehicle_{k}': self.van.pk, f'driver_{k}': self.drv.pk, f'time_{k}': '14:30'})
        res = self.client.get(self.url)
        self.assertContains(res, '運転不可')
        self.assertContains(res, 'が 1 件あります')
        # 印刷にも
        res = self.client.get(reverse('transport:day_pdf') + f'?d={self.day.isoformat()}&fmt=html')
        self.assertContains(res, '（運転不可）')

    def test_default_place_from_school_and_home(self):
        res = self.client.get(self.url)
        # 迎えの場所が空 → 学校（住所つき）が既定で出る。送りは自宅の住所
        self.assertContains(res, '南小学校（見本市南町 1-1）')
        self.assertContains(res, '見本市北町 9-9')
        res = self.client.get(reverse('transport:day_pdf') + f'?d={self.day.isoformat()}&fmt=html')
        self.assertContains(res, '南小学校（見本市南町 1-1）')

    def test_auto_assign_respects_capacity_and_ride_ng(self):
        # 青木と伊藤は同乗NG。車両未定が 3 件（迎え）＋1 件（送り）
        self.k1.no_ride_with.add(self.k2)
        res = self.client.get(self.url)
        self.assertContains(res, '車両未定 3')
        self.assertContains(res, '自動で割り当てる')
        res = self.client.post(reverse('transport:day'), {'action': 'auto', 'd': self.day.isoformat()}, follow=True)
        self.assertContains(res, '割り当てました')
        a = {(x.reservation.beneficiary_id, x.direction): x for x in TransportAssignment.objects.select_related('reservation')}
        v1 = a[(self.k1.pk, 'pickup')].vehicle
        v2 = a[(self.k2.pk, 'pickup')].vehicle
        v3 = a[(self.k3.pk, 'pickup')].vehicle
        self.assertEqual(v1, self.van)                    # 並び順の先頭
        self.assertEqual(v2, self.kei)                    # 青木と同じ車に乗せない → 軽
        self.assertEqual(v3, self.van)                    # ハイエースは 2 人まで
        self.assertEqual(a[(self.k1.pk, 'pickup')].driver, self.drv)     # いつもの運転手
        self.assertEqual(a[(self.k1.pk, 'dropoff')].vehicle, self.van)
        res = self.client.get(self.url)
        self.assertNotContains(res, '車両未定')
        self.assertNotContains(res, '同乗NG：')
        # 4 人目（迎え）は 2 台とも満席で決められない
        k4 = Beneficiary.objects.create(facility=self.f, last_name='江口', first_name='海', last_name_kana='えぐち', date_of_birth=datetime.date(2018, 4, 1))
        TransportProfile.objects.create(beneficiary=k4, pickup=True, pickup_time=datetime.time(14, 30))
        Reservation.objects.create(facility=self.f, beneficiary=k4, date=self.day, start_time=datetime.time(15, 0))
        res = self.client.post(reverse('transport:day'), {'action': 'auto', 'd': self.day.isoformat()}, follow=True)
        self.assertContains(res, '決められ')

    def test_driver_home_card(self):
        # 配車担当（運転手に結びついた職員）のホームには、きょうの自分の送迎が出る。ほかの人には出ない
        k = f'r{self.rs[0].pk}_pickup'
        self.client.post(reverse('transport:day'), {'action': 'save', 'd': self.day.isoformat(), f'present_{k}': '1',
                                                    f'vehicle_{k}': self.van.pk, f'driver_{k}': self.drv.pk, f'time_{k}': '14:30'})
        res = self.client.get(reverse('facilities:dashboard'))
        self.assertNotContains(res, 'きょうの送迎')
        self.client.logout()
        self.client.login(username='drv', password='pw12345678')
        res = self.client.get(reverse('facilities:dashboard'))
        self.assertContains(res, 'きょうの送迎（佐藤）')
        self.assertContains(res, '青木 子')
        self.assertContains(res, '南小学校（見本市南町 1-1）')
        self.assertContains(res, '14:30')
        # 送迎を使わない事業所なら出ない
        self.f.use_transport = False
        self.f.save()
        res = self.client.get(reverse('facilities:dashboard'))
        self.assertNotContains(res, 'きょうの送迎')


class RouteEstimateTests(TestCase):
    """配車表の所要時間（2026-11-09 オウル）：住所→座標→経路、控え、Google への切り替え、画面のボタンとホーム"""

    def setUp(self):
        from beneficiaries.models import School
        self.f = Facility.objects.create(name='児童発達支援センター　オウル', layout=Facility.LAYOUT_RYOIKU, use_reservation=True, use_transport=True,
                                         address='京都市伏見区深草見本町1-1')
        self.user = StaffAccount.objects.create_user('owl', password='pw12345678', facility=self.f, role=StaffAccount.ROLE_ADMIN)
        self.client.login(username='owl', password='pw12345678')
        self.day = datetime.date.today()
        self.school = School.objects.create(facility=self.f, name='南小学校', address='〒612-0000 京都市伏見区深草見本町3-1')
        self.k1 = Beneficiary.objects.create(facility=self.f, last_name='青木', first_name='子', last_name_kana='あおき', date_of_birth=datetime.date(2019, 4, 1),
                                             school_name='南小学校', school=self.school, address='京都市伏見区深草見本町10-1')
        self.k2 = Beneficiary.objects.create(facility=self.f, last_name='伊藤', first_name='花', last_name_kana='いとう', date_of_birth=datetime.date(2018, 4, 1))
        self.van = Vehicle.objects.create(facility=self.f, name='ハイエース', capacity=4)
        self.drv = Driver.objects.create(facility=self.f, name='佐藤', staff=self.user)
        self.van.default_driver = self.drv
        self.van.save()
        for k in (self.k1, self.k2):
            TransportProfile.objects.create(beneficiary=k, pickup=True, dropoff=True, pickup_time=datetime.time(14, 30), default_vehicle=self.van)
        self.rs = [Reservation.objects.create(facility=self.f, beneficiary=k, date=self.day, start_time=datetime.time(15, 0)) for k in (self.k1, self.k2)]
        self.url = reverse('transport:day') + f'?d={self.day.isoformat()}'
        self.calls = []

    def fake_get(self, url, params):
        """国土地理院：住所ごとに少しずつ違う座標。OSRM：距離 4.2km・11 分。Google：9 分・3.9km"""
        from transport import routing
        self.calls.append(url)
        if url == routing.GSI_URL:
            q = params['q']
            if '知らない' in q:
                return []
            n = sum(ord(c) for c in q) % 50
            return [{'geometry': {'coordinates': [135.77 + n / 1000, 34.95 + n / 1000]}, 'properties': {'title': q}}]
        if url.startswith(routing.OSRM_URL):
            if getattr(self, 'osrm_down', False):
                raise OSError('osrm down')
            return {'code': 'Ok', 'routes': [{'duration': 660, 'distance': 4200}]}
        if url == routing.GOOGLE_URL:
            return {'rows': [{'elements': [{'status': 'OK', 'duration': {'value': 540}, 'distance': {'value': 3900}}]}]}
        raise AssertionError(url)

    def test_estimate_caches_and_falls_back(self):
        from unittest import mock
        from transport import routing
        from transport.models import GeoPoint, RouteEstimate
        with mock.patch.object(routing, '_get', side_effect=self.fake_get):
            est = routing.estimate('〒612-0000 京都市伏見区深草見本町3-1', '京都市伏見区深草見本町1-1')
            self.assertEqual((est.minutes, est.distance_m, est.source, est.label), (11, 4200, 'osrm', '約11分・4.2km'))
            self.assertEqual(est.origin, '京都市伏見区深草見本町3-1')       # 〒と郵便番号は外れる
            self.assertEqual(GeoPoint.objects.count(), 2)
            n = len(self.calls)
            # 2 回目は控えから（外部に出ない）
            self.assertEqual(routing.estimate('京都市伏見区深草見本町3-1', '京都市伏見区深草見本町1-1').pk, est.pk)
            self.assertEqual(len(self.calls), n)
            # 住所が見つからなければ None（控えない）
            self.assertIsNone(routing.estimate('知らない町 1', '京都市伏見区深草見本町1-1'))
            self.assertEqual(RouteEstimate.objects.count(), 1)
            # OSRM に届かなければ直線距離からの目安
            self.osrm_down = True
            est2 = routing.estimate('京都市伏見区深草見本町1-1', '京都市伏見区深草見本町10-1')
            self.assertEqual(est2.source, 'direct')
            self.assertTrue(est2.label.endswith('（目安）'))
            self.assertGreaterEqual(est2.minutes, 1)
        # 同じ住所どうし・空は None
        self.assertIsNone(routing.estimate('京都市伏見区深草見本町1-1', '京都市伏見区深草見本町1-1'))
        self.assertIsNone(routing.estimate('', '京都市伏見区深草見本町1-1'))

    def test_google_when_key_is_set(self):
        from unittest import mock
        from django.test import override_settings
        from transport import routing
        with override_settings(GOOGLE_MAPS_API_KEY='dummy'), mock.patch.object(routing, '_get', side_effect=self.fake_get):
            est = routing.estimate('京都市伏見区深草見本町3-1', '京都市伏見区深草見本町1-1')
        self.assertEqual((est.minutes, est.source), (9, 'google'))
        self.assertEqual(self.calls, [routing.GOOGLE_URL])

    def test_board_button_and_home(self):
        from unittest import mock
        from transport import routing
        from .views import build_board
        board = build_board(self.f, self.day)
        rows = {(d, x['beneficiary'].pk): x for d in ('pickup', 'dropoff') for x in board[d]}
        self.assertEqual(rows[('pickup', self.k1.pk)]['route_pair'], ('京都市伏見区深草見本町3-1', '京都市伏見区深草見本町1-1'))
        self.assertEqual(rows[('dropoff', self.k1.pk)]['route_pair'], ('京都市伏見区深草見本町1-1', '京都市伏見区深草見本町10-1'))
        self.assertIsNone(rows[('pickup', self.k2.pk)]['route_pair'])      # 学校・住所が無い子は求めない
        self.assertEqual(board['routes_missing'], 2)
        res = self.client.get(self.url)
        self.assertContains(res, '所要時間を計算（未計算 2 件）')
        with mock.patch.object(routing, '_get', side_effect=self.fake_get):
            res = self.client.post(reverse('transport:day'), {'action': 'routes', 'd': self.day.isoformat()}, follow=True)
        self.assertContains(res, '所要時間を 2 件求めました')
        self.assertContains(res, '約11分・4.2km')
        self.assertContains(res, 'この車の行の所要時間の合計')   # 車両の合計（迎え・送りそれぞれ 11 分）
        self.assertEqual(build_board(self.f, self.day)['routes_missing'], 0)
        # もう一度押しても外部には出ない
        n = len(self.calls)
        res = self.client.post(reverse('transport:day'), {'action': 'routes', 'd': self.day.isoformat()}, follow=True)
        self.assertContains(res, '所要時間を求める行はありません')
        self.assertEqual(len(self.calls), n)
        # ホームの「きょうの送迎」と印刷にも出る
        res = self.client.get(reverse('facilities:dashboard'))
        self.assertContains(res, '約11分・4.2km')
        res = self.client.get(reverse('transport:day_pdf') + f'?d={self.day.isoformat()}&fmt=html')
        self.assertContains(res, '約11分・4.2km')

    def test_no_facility_address(self):
        from .views import build_board
        self.f.address = ''
        self.f.save()
        board = build_board(self.f, self.day)
        self.assertEqual(board['routes_missing'], 0)
        res = self.client.get(self.url)
        self.assertContains(res, '施設設定に施設の住所を入れると')

    def test_command(self):
        from io import StringIO
        from unittest import mock
        from django.core.management import call_command
        from transport import routing
        out = StringIO()
        with mock.patch.object(routing, '_get', side_effect=self.fake_get):
            call_command('route_estimate', '--quiet', stdout=out)
        self.assertIn('所要時間: 約11分・4.2km', out.getvalue())
        self.assertNotIn('京都市', out.getvalue())

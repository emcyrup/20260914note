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

"""学校のマスタ（2026-11-09 オウル）：学校名から住所を呼び出す・無ければ登録・一覧で直す"""
import datetime

from django.test import TestCase
from django.urls import reverse

from accounts.models import StaffAccount
from facilities.models import Facility

from .models import Beneficiary, School


class SchoolTests(TestCase):
    def setUp(self):
        self.f = Facility.objects.create(name='児童発達支援センター　テスト', layout=Facility.LAYOUT_RYOIKU, use_reservation=True)
        self.user = StaffAccount.objects.create_user('owl', password='pw12345678', facility=self.f, role=StaffAccount.ROLE_ADMIN)
        self.client.force_login(self.user)
        self.base = {'last_name': '見本', 'first_name': '太郎', 'last_name_kana': 'みほん', 'first_name_kana': 'たろう',
                     'date_of_birth': '2019-04-02', 'gender': 'male', 'status': 'active'}

    def test_create_registers_school_and_reuses_address(self):
        # 1 人目：学校名と学校の住所を入れる → 学校が登録される
        res = self.client.post(reverse('beneficiaries:create'), dict(self.base, postal_code='600-0001', address='見本市見本町 1-1',
                                                                      school_name='見本小学校', grade='e1', school_postal_code='600-0002',
                                                                      school_address='見本市学校町 2-2', school_phone='075-000-0000'))
        self.assertEqual(res.status_code, 302)
        b1 = Beneficiary.objects.get(first_name='太郎')
        school = School.objects.get(facility=self.f, name='見本小学校')
        self.assertEqual(b1.school, school)
        self.assertEqual((school.postal_code, school.address, school.phone), ('600-0002', '見本市学校町 2-2', '075-000-0000'))
        self.assertEqual(b1.address, '見本市見本町 1-1')
        # 呼び出し（JSON）
        res = self.client.get(reverse('beneficiaries:school_lookup'), {'name': '見本小学校'})
        self.assertEqual(res.json()['address'], '見本市学校町 2-2')
        self.assertEqual(res.json()['students'], 1)
        self.assertFalse(self.client.get(reverse('beneficiaries:school_lookup'), {'name': '知らない学校'}).json()['found'])
        # 2 人目：同じ学校名で住所を空のまま保存 → 学校の住所はそのまま、同じ学校に結びつく
        res = self.client.post(reverse('beneficiaries:create'), dict(self.base, first_name='次郎', first_name_kana='じろう',
                                                                      school_name='見本小学校', school_address=''))
        self.assertEqual(res.status_code, 302)
        b2 = Beneficiary.objects.get(first_name='次郎')
        self.assertEqual(b2.school, school)
        self.assertEqual(School.objects.filter(facility=self.f).count(), 1)
        school.refresh_from_db()
        self.assertEqual(school.address, '見本市学校町 2-2')
        self.assertEqual(b2.school_address, '見本市学校町 2-2')
        # 詳細に住所と学校（住所つき）が出る
        res = self.client.get(reverse('beneficiaries:detail', args=[b2.pk]))
        self.assertContains(res, '見本小学校')
        self.assertContains(res, '見本市学校町 2-2')
        self.assertContains(res, '学校の一覧')
        # 編集画面の学校の欄に、学校の住所が初期値で入る。学校の一覧（datalist）も出る
        res = self.client.get(reverse('beneficiaries:update', args=[b2.pk]))
        self.assertContains(res, 'value="見本市学校町 2-2"')
        self.assertContains(res, '<option value="見本小学校">')

    def test_update_changes_school_and_edit_window_keeps_fields(self):
        school = School.objects.create(facility=self.f, name='北小学校', address='北町 1')
        b = Beneficiary.objects.create(facility=self.f, last_name='見本', first_name='花', date_of_birth=datetime.date(2019, 4, 2),
                                       school_name='北小学校', school=school, address='見本町 9')
        # 別の学校名（登録なし）に変えて住所も入れる → 新しい学校が登録され、北小学校はそのまま残る
        res = self.client.post(reverse('beneficiaries:update', args=[b.pk]), dict(self.base, first_name='花', school_name='南小学校',
                                                                                    school_address='南町 5', address='見本町 9'))
        self.assertEqual(res.status_code, 302)
        b.refresh_from_db()
        self.assertEqual(b.school.name, '南小学校')
        self.assertEqual(b.school.address, '南町 5')
        self.assertTrue(School.objects.filter(name='北小学校').exists())
        # 編集の窓（_partial）が住所の欄を送らないときは今の値のまま
        res = self.client.post(reverse('beneficiaries:update', args=[b.pk]), dict(self.base, first_name='花', _partial='1'))
        self.assertEqual(res.status_code, 302)
        b.refresh_from_db()
        self.assertEqual((b.school_name, b.address), ('南小学校', '見本町 9'))
        # 窓が住所の欄を送ってくれば、その値になる（学校名を空にすると学校の結びつきも外れる）
        res = self.client.post(reverse('beneficiaries:update', args=[b.pk]), dict(self.base, first_name='花', _partial='1',
                                                                                    address='新町 1', postal_code='', mobile_phone='', home_phone='',
                                                                                    school_name='', grade='', school_postal_code='', school_address='', school_phone=''))
        self.assertEqual(res.status_code, 302)
        b.refresh_from_db()
        self.assertEqual((b.school_name, b.school, b.address), ('', None, '新町 1'))

    def test_school_list_page(self):
        school = School.objects.create(facility=self.f, name='北小学校', address='北町 1')
        other = Facility.objects.create(name='別の事業所')
        School.objects.create(facility=other, name='よその学校')
        res = self.client.get(reverse('beneficiaries:schools'))
        self.assertContains(res, '北小学校')
        self.assertNotContains(res, 'よその学校')
        # 直す → 通っている子の学校名もそろう
        b = Beneficiary.objects.create(facility=self.f, last_name='見本', first_name='花', date_of_birth=datetime.date(2019, 4, 2),
                                       school_name='北小学校', school=school)
        res = self.client.post(reverse('beneficiaries:schools'), {'action': 'save', 'pk': school.pk, 'name': '北第一小学校', 'postal_code': '600-0003',
                                                                   'address': '北町 1-1', 'phone': '', 'note': '東門で待つ'})
        self.assertRedirects(res, reverse('beneficiaries:schools'))
        school.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual((school.name, school.address, school.note, b.school_name), ('北第一小学校', '北町 1-1', '東門で待つ', '北第一小学校'))
        # 通っている子がいる学校は消せない。いなければ消せる
        res = self.client.post(reverse('beneficiaries:schools'), {'action': 'delete', 'pk': school.pk}, follow=True)
        self.assertContains(res, '消せません')
        empty = School.objects.create(facility=self.f, name='だれもいない園')
        self.client.post(reverse('beneficiaries:schools'), {'action': 'delete', 'pk': empty.pk})
        self.assertFalse(School.objects.filter(pk=empty.pk).exists())
        # 追加
        self.client.post(reverse('beneficiaries:schools'), {'action': 'add', 'name': '見本保育園', 'address': '園町 3', 'postal_code': '', 'phone': '', 'note': ''})
        self.assertTrue(School.objects.filter(facility=self.f, name='見本保育園', address='園町 3').exists())
        # 他事業所の学校は触れない
        res = self.client.post(reverse('beneficiaries:schools'), {'action': 'delete', 'pk': School.objects.get(name='よその学校').pk})
        self.assertEqual(res.status_code, 404)


class SchoolDismissalTests(TestCase):
    """学校の下校時刻（既定・曜日ごと）：学校の一覧と利用者情報から登録、呼び出し、その日の時刻（2026-11-09）"""

    def setUp(self):
        self.f = Facility.objects.create(name='児童発達支援センター　テスト', layout=Facility.LAYOUT_RYOIKU, use_reservation=True)
        self.user = StaffAccount.objects.create_user('owl', password='pw12345678', facility=self.f, role=StaffAccount.ROLE_ADMIN)
        self.client.force_login(self.user)

    def test_schools_page_saves_dismissal_and_lookup_returns_it(self):
        school = School.objects.create(facility=self.f, name='北小学校', address='北町 1')
        res = self.client.post(reverse('beneficiaries:schools'), {'action': 'save', 'pk': school.pk, 'name': '北小学校', 'postal_code': '', 'address': '北町 1',
                                                                   'phone': '', 'note': '', 'dismissal_time': '14:30', 'dismissal_mon': '', 'dismissal_tue': '',
                                                                   'dismissal_wed': '13:30', 'dismissal_thu': '', 'dismissal_fri': 'abc',
                                                                   'dismissal_note': '1 年生は 14:00'})
        self.assertRedirects(res, reverse('beneficiaries:schools'))
        school.refresh_from_db()
        self.assertEqual((school.dismissal_time, school.dismissal_wed, school.dismissal_fri, school.dismissal_note),
                         (datetime.time(14, 30), datetime.time(13, 30), None, '1 年生は 14:00'))
        self.assertEqual(school.dismissal_for(datetime.date(2026, 11, 11)), datetime.time(13, 30))   # 水
        self.assertEqual(school.dismissal_for(datetime.date(2026, 11, 12)), datetime.time(14, 30))   # 木 → 既定
        self.assertEqual(school.dismissal_display, '14:30（水 13:30）')
        d = self.client.get(reverse('beneficiaries:school_lookup'), {'name': '北小学校'}).json()
        self.assertEqual((d['dismissal_time'], d['dismissal_display']), ('14:30', '14:30（水 13:30）'))
        page = self.client.get(reverse('beneficiaries:schools')).content.decode()
        self.assertIn('value="14:30"', page)
        self.assertIn('下校時刻', page)
        # 追加のときも下校時刻が入る
        self.client.post(reverse('beneficiaries:schools'), {'action': 'add', 'name': '東こども園', 'postal_code': '', 'address': '', 'phone': '', 'note': '',
                                                             'dismissal_time': '14:00', 'dismissal_note': ''})
        self.assertEqual(School.objects.get(name='東こども園').dismissal_time, datetime.time(14, 0))

    def test_beneficiary_form_sets_default_dismissal(self):
        base = {'last_name': '見本', 'first_name': '太郎', 'last_name_kana': 'みほん', 'first_name_kana': 'たろう',
                'date_of_birth': '2019-04-02', 'gender': 'male', 'status': 'active'}
        res = self.client.post(reverse('beneficiaries:create'), dict(base, school_name='西小学校', school_address='西町 2', school_dismissal='15:10'))
        self.assertEqual(res.status_code, 302)
        school = School.objects.get(facility=self.f, name='西小学校')
        self.assertEqual(school.dismissal_time, datetime.time(15, 10))
        b = Beneficiary.objects.get(first_name='太郎')
        res = self.client.get(reverse('beneficiaries:detail', args=[b.pk]))
        self.assertContains(res, '下校 15:10')
        res = self.client.get(reverse('beneficiaries:update', args=[b.pk]))
        self.assertContains(res, 'name="school_dismissal"')
        self.assertContains(res, 'value="15:10"')

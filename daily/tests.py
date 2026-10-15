"""毎日の運営のテスト（クラスの活動・健康の記録）"""
import datetime

from django.test import TestCase
from django.urls import reverse

from accounts.models import StaffAccount
from beneficiaries.models import Beneficiary
from facilities.models import Facility
from reservations.models import Reservation
from therapy.models import TherapyRecord

from .models import ClassGroup, GroupSession, HealthLog, HealthProfile


class DailyTests(TestCase):
    def setUp(self):
        self.f = Facility.objects.create(name='児童発達支援センター　オウル', layout=Facility.LAYOUT_RYOIKU, use_daily_ops=True,
                                         use_reservation=True, use_therapy_record=True)
        self.user = StaffAccount.objects.create_user('owl', password='pw12345678', facility=self.f,
                                                     role=StaffAccount.ROLE_ADMIN, display_name='大和')
        self.client.login(username='owl', password='pw12345678')
        self.a = Beneficiary.objects.create(facility=self.f, last_name='青木', first_name='そら', last_name_kana='あおき', date_of_birth=datetime.date(2021, 4, 1))
        self.b = Beneficiary.objects.create(facility=self.f, last_name='伊藤', first_name='はな', last_name_kana='いとう', date_of_birth=datetime.date(2021, 6, 1))
        self.day = datetime.date(2026, 10, 7)          # 水曜
        Reservation.objects.create(facility=self.f, beneficiary=self.a, date=self.day, start_time=datetime.time(10, 0))

    def test_disabled_redirects_and_hides_menu(self):
        self.f.use_daily_ops = False
        self.f.save()
        self.assertRedirects(self.client.get(reverse('daily:groups')), reverse('facilities:dashboard'))
        res = self.client.get(reverse('facilities:dashboard'))
        self.assertNotContains(res, 'クラスの活動')
        self.assertNotContains(res, '健康の記録')
        self.assertNotContains(self.client.get(reverse('beneficiaries:detail', args=[self.a.pk])), 'id="dev-tests"')

    def test_group_week_session_apply(self):
        res = self.client.get(reverse('daily:groups'))
        self.assertContains(res, 'クラスの活動')
        self.client.post(reverse('daily:groups'), {'name': 'ひよこ組', 'color': '#c0703b', 'weekdays': ['0', '2'], 'start_time': '10:00',
                                                   'members': [self.a.pk, self.b.pk]})
        g = ClassGroup.objects.get()
        self.assertEqual((g.weekdays, g.start_time, g.members.count(), g.weekdays_label), ([0, 2], datetime.time(10, 0), 2, '月・水'))
        # 週間の計画
        week = reverse('daily:group_week', args=[g.pk])
        res = self.client.get(week + f'?d={self.day.isoformat()}')
        self.assertContains(res, '10月5日〜の週')
        key = self.day.isoformat()
        self.client.post(week, {'d': key, f'present_{key}': '1', f'aim_{key}': '順番を守る', f'act_{key}': ['朝の会', 'サーキット', ''],
                                f'materials_{key}': 'マット', f'staff_{key}': '大和',
                                'present_2026-10-05': '1', 'aim_2026-10-05': '', 'materials_2026-10-05': ''})
        s = GroupSession.objects.get()
        self.assertEqual((s.date, s.aim, s.activity_list, s.materials, s.start_time), (self.day, '順番を守る', ['朝の会', 'サーキット'], 'マット', datetime.time(10, 0)))
        res = self.client.get(reverse('daily:group_week_pdf', args=[g.pk]) + f'?d={key}&fmt=html')
        self.assertContains(res, '週間の活動計画　ひよこ組')
        self.assertContains(res, 'サーキット')
        # その日の記録：予約のある青木だけが参加の候補に印
        url = reverse('daily:session', args=[g.pk, key])
        res = self.client.get(url)
        self.assertContains(res, f'name="present" value="{self.a.pk}" checked')
        self.assertNotContains(res, f'name="present" value="{self.b.pk}" checked')
        self.assertContains(res, 'この日の予約なし')
        post = {'start_time': '10:00', 'aim': '順番を守る', 'act': ['朝の会', 'サーキット'], 'materials': 'マット', 'staff_name': '大和',
                'body': '平均台を渡れた', 'present': [str(self.a.pk), str(self.b.pk)], f'note_{self.a.pk}': 'ボールを渡せた', 'action': 'apply'}
        res = self.client.post(url, post, follow=True)
        self.assertContains(res, '2 人の療育記録に写しました')
        recs = {r.beneficiary_id: r for r in TherapyRecord.objects.all()}
        self.assertEqual(len(recs), 2)
        ra = recs[self.a.pk]
        self.assertEqual((ra.date, ra.time, ra.activities, ra.staff_name), (self.day, datetime.time(10, 0), ['朝の会', 'サーキット'], '大和'))
        self.assertIn('【ひよこ組】ねらい：順番を守る', ra.body)
        self.assertIn('平均台を渡れた', ra.body)
        self.assertIn('（そらさん）ボールを渡せた', ra.body)
        self.assertNotIn('ボールを渡せた', recs[self.b.pk].body)
        # 写し直すと上書き（増えない）
        s.refresh_from_db()
        post['body'] = '直した'
        post['version'] = s.updated_at.isoformat()
        self.client.post(url, post)
        self.assertEqual(TherapyRecord.objects.count(), 2)
        self.assertIn('直した', TherapyRecord.objects.get(beneficiary=self.a).body)
        res = self.client.get(week + f'?d={key}')
        self.assertContains(res, '写した')

    def test_health_day_child_card(self):
        HealthProfile.objects.create(beneficiary=self.a, allergies='卵')
        url = reverse('daily:health') + f'?d={self.day.isoformat()}'
        res = self.client.get(url)
        self.assertContains(res, '青木 そら')
        self.assertNotContains(res, '伊藤 はな')           # 予約が無い
        self.assertContains(res, 'アレルギー：卵')
        p = f'b{self.a.pk}_'
        res = self.client.post(reverse('daily:health'), {'d': self.day.isoformat(), f'{p}present': '1', f'{p}temp_arrival': '37.8',
                                                         f'{p}meal': 'half', f'{p}urine': '2', f'{p}stool': '', f'{p}nap_minutes': '45',
                                                         f'{p}medication_given': 'on', f'{p}medication_note': '昼食後', f'{p}mood': 'tired',
                                                         f'{p}note': '少し鼻水', f'{p}handed_to': '母', f'{p}handed_at': '15:30'}, follow=True)
        self.assertContains(res, '（1 人）')
        log = HealthLog.objects.get()
        self.assertEqual((str(log.temp_arrival), log.meal, log.urine, log.stool, log.nap_minutes, log.medication_given, log.handed_at),
                         ('37.8', 'half', 2, None, 45, True, datetime.time(15, 30)))
        self.assertTrue(log.fever)
        self.assertContains(res, '発熱')
        # 読めない体温は空にする
        self.client.post(reverse('daily:health'), {'d': self.day.isoformat(), f'{p}present': '1', f'{p}temp_arrival': 'abc', f'{p}meal': 'all'})
        log.refresh_from_db()
        self.assertIsNone(log.temp_arrival)
        # 子どもごと・健康の注意
        res = self.client.get(reverse('daily:health_child', args=[self.a.pk]))
        self.assertContains(res, '卵')
        self.assertContains(res, '完食')
        self.client.post(reverse('daily:health_child', args=[self.a.pk]), {'allergies': '卵・乳', 'medications': '', 'seizure': '', 'other': ''})
        self.assertEqual(HealthProfile.objects.get(beneficiary=self.a).allergies, '卵・乳')
        # 引き渡しカード（療育記録も出る）
        TherapyRecord.objects.create(facility=self.f, beneficiary=self.a, date=self.day, body='ブロックで遊んだ')
        res = self.client.get(reverse('daily:health_card', args=[self.a.pk]) + f'?d={self.day.isoformat()}')
        self.assertContains(res, '青木 そら さん')
        self.assertContains(res, '⚠ アレルギー：卵・乳')
        self.assertContains(res, 'ブロックで遊んだ')
        self.assertContains(res, '完食')

    def test_without_reservation_all_active_children(self):
        self.f.use_reservation = False
        self.f.save()
        res = self.client.get(reverse('daily:health') + f'?d={self.day.isoformat()}')
        self.assertContains(res, '伊藤 はな')

    def test_other_facility(self):
        g = Facility.objects.create(name='ほか', layout=Facility.LAYOUT_RYOIKU, use_daily_ops=True)
        other = ClassGroup.objects.create(facility=g, name='よそ組')
        kid = Beneficiary.objects.create(facility=g, last_name='他', first_name='人', date_of_birth=datetime.date(2021, 1, 1))
        self.assertEqual(self.client.get(reverse('daily:group_week', args=[other.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('daily:health_card', args=[kid.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse('daily:groups')), 'よそ組')

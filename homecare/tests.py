import datetime
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import StaffAccount
from beneficiaries.models import Beneficiary
from facilities.models import Facility

from .models import CarePlan, HomeCareProfile, Visit

DAY = datetime.date(2026, 10, 7)          # 水曜日


class HomeCareBase(TestCase):
    def setUp(self):
        from facilities.management.commands.create_facility import apply_kyotaku_preset
        self.f = Facility.objects.create(name='居宅介護（仮）')
        apply_kyotaku_preset(self.f)
        self.user = StaffAccount.objects.create_user('kyo', password='pw12345678', facility=self.f, role=StaffAccount.ROLE_ADMIN)
        self.client.force_login(self.user)
        self.b = Beneficiary.objects.create(facility=self.f, last_name='山本', first_name='和子', last_name_kana='やまもと',
                                            date_of_birth=datetime.date(1958, 5, 12))

    def visit(self, day=DAY, start='10:00', end='11:00', **kw):
        return Visit.objects.create(facility=self.f, beneficiary=self.b, date=day, start_time=datetime.time.fromisoformat(start),
                                    end_time=datetime.time.fromisoformat(end), **kw)


class LayoutTests(HomeCareBase):
    def test_preset_menu_and_home(self):
        self.f.refresh_from_db()
        self.assertTrue(self.f.is_kyotaku)
        self.assertEqual((self.f.use_billing, self.f.use_schedule, self.f.use_reservation, self.f.use_line), (False,) * 4)
        self.assertEqual(self.f.term_staff, 'ヘルパー')
        res = self.client.get(reverse('facilities:dashboard'))
        self.assertRedirects(res, reverse('beneficiaries:list'))      # ホームは利用者管理（2026-10-20）
        page = self.client.get(reverse('homecare:day'))
        # メニューは利用者管理と議事録だけ（2026-10-20）。ほかの画面は URL では開ける
        nav = page.content.decode().split('<nav', 1)[1].split('</nav>', 1)[0]
        self.assertIn('利用者管理', nav)
        self.assertIn(reverse('minutes:index'), nav)
        for label in ('きょうの訪問', '訪問予定', 'サービス提供記録', '居宅介護計画', '実績記録票', '施設設定'):
            self.assertNotIn(f'<span>{label}</span>', nav)
        self.assertEqual(page.status_code, 200)
        self.assertEqual(self.client.get(reverse('facilities:settings')).status_code, 200)
        self.assertNotContains(page, '運用管理')              # 職員は 1 人：職員・運用管理は出さない
        self.assertNotContains(page, '療育記録')
        self.assertNotContains(page, 'id="aiChatFab"')          # 放デイ向けの AI 相談とメモ（日誌）は出さない
        self.assertNotContains(page, 'data-bs-target="#globalMemoModal"')
        self.assertContains(page, '— 居宅介護業務支援</title>')
        self.assertContains(page, '<div class="sidebar-brand-sub">居宅介護</div>')

    def test_other_facilities_cannot_open(self):
        other = Facility.objects.create(name='ほか')
        u = StaffAccount.objects.create_user('o', password='pw12345678', facility=other)
        self.client.force_login(u)
        self.assertEqual(self.client.get(reverse('homecare:day')).status_code, 404)
        self.assertNotContains(self.client.get(reverse('facilities:dashboard'), follow=True), 'きょうの訪問')

    def test_beneficiary_pages(self):
        res = self.client.get(reverse('beneficiaries:detail', args=[self.b.pk]))
        self.assertContains(res, reverse('homecare:person', args=[self.b.pk]))
        self.assertNotContains(self.client.get(reverse('beneficiaries:update', args=[self.b.pk])), '重症心身障害児（重身）')

    def test_create_facility_command(self):
        call_command('create_facility', '居宅介護テスト', '--admin', 'kyo2', '--password', 'pw12345678', '--preset', 'kyotaku',
                     '--demo', stdout=open('/dev/null', 'w'))
        f = Facility.objects.get(name='居宅介護テスト')
        self.assertTrue(f.is_kyotaku)
        self.assertEqual(Beneficiary.objects.filter(facility=f).count(), 3)
        self.assertTrue(Visit.objects.filter(facility=f).exists())
        self.assertEqual(CarePlan.objects.filter(facility=f).count(), 3)


class VisitTests(HomeCareBase):
    def test_add_weekly_and_overlap_warning(self):
        res = self.client.post(reverse('homecare:visit_new'), {
            'beneficiary': self.b.pk, 'date': '2026-10-07', 'start_time': '10:00', 'end_time': '11:00',
            'service_type': 'body', 'repeat': '1'}, follow=True)
        self.assertContains(res, '訪問を 4 件入れました（7日・14日・21日・28日）')
        self.assertEqual(list(Visit.objects.values_list('date', flat=True)),
                         [datetime.date(2026, 10, d) for d in (7, 14, 21, 28)])
        other = Beneficiary.objects.create(facility=self.f, last_name='佐々木', first_name='誠', date_of_birth=datetime.date(1975, 1, 1))
        res = self.client.post(reverse('homecare:visit_new'), {
            'beneficiary': other.pk, 'date': '2026-10-14', 'start_time': '10:30', 'end_time': '11:30', 'service_type': 'house'}, follow=True)
        self.assertContains(res, 'ほかの訪問と時間が重なっています（ヘルパーは 1 人です）')
        self.assertContains(res, '10:00〜11:00 山本 和子さん')
        # 終了が開始より前は入れない
        res = self.client.post(reverse('homecare:visit_new'), {
            'beneficiary': other.pk, 'date': '2026-10-15', 'start_time': '12:00', 'end_time': '11:00', 'service_type': 'house'})
        self.assertContains(res, '終了は開始より後の時刻にしてください')
        self.assertEqual(Visit.objects.filter(date=datetime.date(2026, 10, 15)).count(), 0)

    def test_month_calendar_and_day(self):
        self.visit(plan_note='入浴あり')
        res = self.client.get(reverse('homecare:month', args=[2026, 10]))
        self.assertContains(res, '10:00</span> 山本')
        res = self.client.get(reverse('homecare:day') + '?d=2026-10-07')
        self.assertContains(res, '山本 和子')
        self.assertContains(res, '入浴あり')
        self.assertContains(res, '記録する')

    def test_record_and_cancel(self):
        HomeCareProfile.objects.create(beneficiary=self.b, cautions='右半身にまひがある')
        v = self.visit()
        url = reverse('homecare:record', args=[v.pk])
        page = self.client.get(url)
        self.assertContains(page, '右半身にまひがある')            # 援助の留意点
        self.assertContains(page, 'value="10:00"')                # 実際の時刻は予定から
        res = self.client.post(url, {'actual_start': '10:05', 'actual_end': '10:50', 'tasks': ['bath', 'dress', 'nope'],
                                     'temperature': '36.6', 'bp_high': '120', 'bp_low': '70', 'condition': 'good',
                                     'body': '「さっぱりした」と話していた。'}, follow=True)
        self.assertContains(res, 'サービス提供記録を保存しました')
        v.refresh_from_db()
        self.assertEqual((v.status, v.tasks, str(v.temperature), v.staff), ('done', ['bath', 'dress'], '36.6', self.user))
        self.assertEqual(v.billing_hours, 0.5)                    # 実際が短い（45 分）→ 30 分単位で 0.5
        v2 = self.visit(day=DAY + datetime.timedelta(days=7))
        self.client.post(reverse('homecare:record', args=[v2.pk]), {'action': 'cancel', 'cancel_reason': '入院のため'})
        v2.refresh_from_db()
        self.assertEqual((v2.status, v2.cancel_reason), ('cancelled', '入院のため'))

    def test_billing_hours(self):
        self.assertEqual(self.visit(start='09:30', end='11:00').billing_hours, 1.5)
        self.assertEqual(self.visit(start='09:00', end='09:20').billing_hours, 0.5)
        v = self.visit(start='13:00', end='14:00', actual_start=datetime.time(13, 0), actual_end=datetime.time(14, 30))
        self.assertEqual(v.billing_hours, 1.0)                    # 実際が長くても計画の時間

    @override_settings(ANTHROPIC_API_KEY='test-key')
    def test_tidy(self):
        with mock.patch('ai_assist.quick.ask_ai', return_value=('入浴を手伝った。', None)):
            res = self.client.post(reverse('homecare:tidy'), '{"text": "えーと入浴手伝った"}', content_type='application/json')
        self.assertEqual(res.json()['text'], '入浴を手伝った。')


class PrintTests(HomeCareBase):
    def test_person_plan_and_prints(self):
        url = reverse('homecare:person', args=[self.b.pk])
        self.client.post(url, {'support_level': '3', 'grant_body': '20', 'grant_house': '7.3', 'cautions': '湯温 40 度まで'})
        p = HomeCareProfile.objects.get(beneficiary=self.b)
        self.assertEqual((p.support_level, p.grants), ('3', {'body': 20.0, 'house': 7.5}))
        # 計画：作って、次は前の計画を写して始める
        res = self.client.post(reverse('homecare:plan_new'), {
            'beneficiary': self.b.pk, 'created_on': '2026-10-01', 'period_start': '2026-10-01', 'period_end': '2027-03-31',
            'wishes': '自宅で暮らしたい', 's0_days': '月・木', 's0_start': '10:00', 's0_end': '11:00', 's0_type': 'body',
            's0_content': '入浴介助', 'explained_on': '2026-10-02', 'agreed_by': '本人'})
        plan = CarePlan.objects.get()
        self.assertRedirects(res, reverse('homecare:plan_edit', args=[plan.pk]))
        self.assertEqual(plan.services, [{'days': '月・木', 'start': '10:00', 'end': '11:00', 'type': 'body', 'content': '入浴介助'}])
        page = self.client.get(reverse('homecare:plan_new') + f'?b={self.b.pk}')
        self.assertContains(page, '前の計画の内容を写しています')
        self.assertContains(page, '自宅で暮らしたい')
        html = self.client.get(reverse('homecare:plan_print', args=[plan.pk]) + '?fmt=html')
        self.assertContains(html, '居宅介護計画書')
        self.assertContains(html, '区分3')
        # 記録と実績記録票
        self.visit(status='done', actual_start=datetime.time(10, 0), actual_end=datetime.time(11, 0), tasks=['bath'], body='入浴した')
        self.visit(day=DAY + datetime.timedelta(days=1), status='cancelled', cancel_reason='体調不良')
        page = self.client.get(reverse('homecare:jisseki', args=[2026, 10]))
        self.assertContains(page, '身体介護 1.0 ／ 支給量 20.0 時間')
        html = self.client.get(reverse('homecare:jisseki_print', args=[2026, 10, self.b.pk]) + '?fmt=html')
        self.assertContains(html, '居宅介護サービス提供実績記録票（2026年10月分）')
        self.assertContains(html, '中止')
        html = self.client.get(reverse('homecare:records_print', args=[2026, 10, self.b.pk]) + '?fmt=html')
        self.assertContains(html, '入浴した')
        self.assertContains(html, '入浴介助')
        for name in ('jisseki_print', 'records_print'):
            res = self.client.get(reverse(f'homecare:{name}', args=[2026, 10, self.b.pk]))
            self.assertEqual(res['Content-Type'], 'application/pdf')

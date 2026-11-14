"""ライト版（LITE_MODE=True：サーバー全体を、利用者台帳・予定と出欠・日誌・療育記録だけにする）のテスト"""
import datetime

from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import StaffAccount
from beneficiaries.models import Beneficiary
from records.models import DailyRecord

from .models import Facility


class LiteBase(TestCase):
    def setUp(self):
        # フル版として作った事業所（請求・LINE・予約・療育記録を使う、療育の型）
        with override_settings(LITE_MODE=False):
            self.f = Facility.objects.create(
                name='みほんデイ', use_billing=True, use_line=True, use_reservation=True, use_schedule=True,
                use_therapy_record=True, use_transport=True, use_reports=True)
        self.user = StaffAccount.objects.create_user('lite', password='pw12345678', facility=self.f,
                                                     role=StaffAccount.ROLE_ADMIN)
        self.kid = Beneficiary.objects.create(facility=self.f, last_name='青木', first_name='子',
                                              date_of_birth=datetime.date(2018, 4, 2))
        self.rec = DailyRecord.objects.create(facility=self.f, beneficiary=self.kid, date=datetime.date(2026, 10, 2))
        self.client.login(username='lite', password='pw12345678')

    def pages(self):
        return [reverse('facilities:dashboard'), reverse('beneficiaries:list'),
                reverse('beneficiaries:detail', args=[self.kid.pk]), reverse('schedules:calendar'),
                reverse('records:dashboard'), reverse('records:list', args=[self.kid.pk]) + f'?selected={self.rec.pk}',
                reverse('records:simple_home'), reverse('therapy:index'), reverse('therapy:child', args=[self.kid.pk]),
                reverse('facilities:settings'), reverse('accounts:staff'), reverse('accounts:theme')]


@override_settings(LITE_MODE=True)
class LiteModeTests(LiteBase):
    def test_lite_pages_open(self):
        for url in self.pages():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_menu_shows_only_lite_features(self):
        html = self.client.get(reverse('facilities:dashboard')).content.decode()
        self.assertIn('ライト版', html)
        for url in (reverse('beneficiaries:list'), reverse('schedules:calendar'), reverse('records:dashboard'),
                    reverse('therapy:index'), reverse('facilities:settings')):
            self.assertIn(f'href="{url}"', html)
        for name in ('support_plans:list', 'reports:index', 'minutes:index', 'billing:matrix',
                     'reservations:calendar', 'line_integration:delivery_log', 'transport:day'):
            self.assertNotIn(f'href="{reverse(name)}"', html)
        self.assertNotIn('chat-widget', html)

    def test_other_features_go_home(self):
        for name in ('support_plans:list', 'billing:matrix', 'reports:index', 'minutes:index', 'reservations:calendar',
                     'line_integration:delivery_log', 'transport:day', 'surveys:index', 'consultation:index',
                     'custom_forms:index', 'planbook:students'):
            with self.subTest(name=name):
                res = self.client.get(reverse(name))
                self.assertRedirects(res, reverse('facilities:dashboard'), fetch_redirect_response=False)
        res = self.client.get(reverse('support_plans:list'), follow=True)
        self.assertContains(res, 'ライト版では使えません')

    def test_posts_and_ai_are_refused(self):
        self.assertEqual(self.client.post(reverse('ai_assist:chat'), {'message': 'x'}).status_code, 403)
        self.assertEqual(self.client.post(reverse('facilities:addon_setting')).status_code, 403)
        self.assertEqual(self.client.post(reverse('line_integration:send', args=[self.rec.pk])).status_code, 403)
        # 記録の入力に要るもの（言いかえの確認）は閉じない
        self.assertNotEqual(self.client.post(reverse('ai_assist:wording_check'), {'text': 'x'}).status_code, 403)

    def test_public_pages_are_404_without_login(self):
        self.client.logout()
        self.assertEqual(self.client.get('/yoyaku/abc/').status_code, 404)
        self.assertEqual(self.client.get(reverse('healthz')).status_code, 200)
        self.assertEqual(self.client.get(reverse('accounts:login')).status_code, 200)

    def test_flags_are_off_but_database_is_kept(self):
        f = Facility.objects.get(pk=self.f.pk)
        self.assertEqual((f.use_billing, f.use_line, f.use_reservation, f.use_transport, f.use_reports),
                         (False, False, False, False, False))
        self.assertEqual((f.use_schedule, f.use_therapy_record), (True, True))    # ライト版の機能は設定のまま
        # 設定を保存しても、フル版の設定は消えない
        f.name = 'なまえを変えた'
        f.save()
        res = self.client.post(reverse('facilities:feature_settings'),
                               {'use_schedule': 'on', 'journal_sections': ['observation']})
        self.assertEqual(res.status_code, 302)
        with override_settings(LITE_MODE=False):
            f = Facility.objects.get(pk=self.f.pk)
            self.assertEqual((f.name, f.use_billing, f.use_line, f.use_reservation, f.use_transport, f.use_reports),
                             ('なまえを変えた', True, True, True, True, True))
            self.assertEqual((f.use_schedule, f.use_therapy_record), (True, False))  # ライト版で外した療育記録は反映

    def test_layout_is_standard(self):
        Facility.objects.filter(pk=self.f.pk).update(layout=Facility.LAYOUT_PLANBOOK, form_set=Facility.FORM_SET_HAPPINESS)
        self.assertEqual(self.client.get(reverse('facilities:dashboard')).status_code, 200)   # 計画書中心の画面へ飛ばない
        with override_settings(LITE_MODE=False):
            self.assertEqual(Facility.objects.get(pk=self.f.pk).layout, Facility.LAYOUT_PLANBOOK)

    def test_settings_hides_full_features(self):
        html = self.client.get(reverse('facilities:settings')).content.decode()
        self.assertIn('name="use_schedule"', html)
        self.assertIn('name="use_therapy_record"', html)
        for name in ('use_billing', 'use_line', 'use_reservation', 'use_transport', 'use_consultation'):
            self.assertNotIn(f'name="{name}"', html)
        self.assertNotIn('算定要件資料をアップロード', html)

    def test_new_facility_and_mobile_api(self):
        from mobile_api.models import MobileToken
        _, key = MobileToken.issue(self.user)
        res = self.client.get('/api/mobile/v1/me/', HTTP_AUTHORIZATION=f'Bearer {key}')
        self.assertEqual(res.json()['features']['today'], 'visit')
        self.assertFalse(Facility.objects.create(name='あたらしい', use_billing=True).use_billing)


class FullModeTests(LiteBase):
    """LITE_MODE を付けないサーバーは今までどおり"""

    def test_everything_as_before(self):
        for url in self.pages() + [reverse('support_plans:list'), reverse('billing:matrix'), reverse('minutes:index')]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        html = self.client.get(reverse('facilities:dashboard')).content.decode()
        self.assertNotIn('ライト版', html)
        self.assertIn(f'href="{reverse("support_plans:list")}"', html)
        self.assertTrue(Facility.objects.get(pk=self.f.pk).use_billing)
        html = self.client.get(reverse('facilities:settings')).content.decode()
        self.assertIn('name="use_billing"', html)

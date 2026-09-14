import datetime
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse

from accounts.models import StaffAccount
from beneficiaries.models import Beneficiary, Guardian, RecipientCertificate
from billing.models import BillingMatrixEntry
from facilities.models import Facility, SupportContentTag
from records.models import ActivityTag, DailyRecord, StaffMemo
from schedules.models import ScheduledVisit


class SeedDemoCommandTests(TestCase):
    """seed_demo: サンプルデータの投入・二重投入の防止・--reset"""

    def setUp(self):
        self.facility = Facility.objects.create(name='テスト事業所')
        self.staff = StaffAccount.objects.create_user(
            username='admin', password='pw12345678', facility=self.facility,
        )

    def test_seeds_related_data(self):
        out = StringIO()
        call_command('seed_demo', stdout=out)

        bens = Beneficiary.objects.filter(facility=self.facility)
        self.assertEqual(bens.count(), 6)
        self.assertTrue(all('サンプルデータ' in b.notes for b in bens))
        self.assertEqual(Guardian.objects.filter(beneficiary__in=bens).count(), 7)
        self.assertEqual(RecipientCertificate.objects.filter(beneficiary__in=bens).count(), 6)
        self.assertEqual(ActivityTag.objects.filter(facility=self.facility).count(), 8)
        self.assertEqual(SupportContentTag.objects.filter(facility=self.facility).count(), 8)

        visits = ScheduledVisit.objects.filter(facility=self.facility)
        self.assertGreater(visits.count(), 50)
        today = datetime.date.today()
        # 未来は「予定」のみ、過去は来所／欠席／振替のいずれか
        self.assertFalse(visits.filter(date__gte=today).exclude(status='scheduled').exists())
        self.assertFalse(visits.filter(date__lt=today, status='scheduled').exists())
        # 予定と日誌は 1利用者1日1件（unique_together に違反していない）
        self.assertEqual(visits.count(), visits.values('beneficiary', 'date').distinct().count())

        records = DailyRecord.objects.filter(facility=self.facility)
        self.assertGreater(records.count(), 10)
        rec = records.first()
        self.assertTrue(rec.activity_name and rec.activity_aim and rec.activity_reflection)
        self.assertEqual(rec.author, self.staff)
        self.assertGreater(rec.activity_tags.count(), 0)
        self.assertGreater(rec.support_tags.count(), 0)
        # 日誌は来所した日にだけある
        for r in records:
            self.assertEqual(ScheduledVisit.objects.get(beneficiary=r.beneficiary, date=r.date).status, 'attended')

        self.assertEqual(
            BillingMatrixEntry.objects.filter(facility=self.facility).count(),
            visits.exclude(status='scheduled').count(),
        )
        self.assertEqual(StaffMemo.objects.filter(facility=self.facility).count(), 4)
        self.assertIn('サンプルデータを投入しました', out.getvalue())

    def test_refuses_to_seed_twice_without_reset(self):
        call_command('seed_demo', stdout=StringIO())
        with self.assertRaises(CommandError):
            call_command('seed_demo', stdout=StringIO())
        self.assertEqual(Beneficiary.objects.filter(facility=self.facility).count(), 6)

    def test_reset_replaces_sample_data_but_keeps_real_users(self):
        real = Beneficiary.objects.create(
            facility=self.facility, last_name='実在', first_name='太郎',
            date_of_birth=datetime.date(2015, 1, 1),
        )
        call_command('seed_demo', stdout=StringIO())
        call_command('seed_demo', '--reset', stdout=StringIO())
        self.assertEqual(Beneficiary.objects.filter(facility=self.facility).count(), 7)
        self.assertTrue(Beneficiary.objects.filter(pk=real.pk).exists())

    def test_requires_facility_choice_when_multiple(self):
        Facility.objects.create(name='もう一つの施設')
        with self.assertRaises(CommandError):
            call_command('seed_demo', stdout=StringIO())
        call_command('seed_demo', '--facility', str(self.facility.pk), stdout=StringIO())
        self.assertEqual(Beneficiary.objects.filter(facility=self.facility).count(), 6)


class DashboardWeekAndSupportTagPriceTests(TestCase):
    def setUp(self):
        from accounts.models import StaffAccount
        self.facility = Facility.objects.create(name='F')
        self.user = StaffAccount.objects.create_user('s', password='p', facility=self.facility)
        self.client.force_login(self.user)

    def test_dashboard_shows_week_calendar(self):
        from datetime import date
        from beneficiaries.models import Beneficiary
        from schedules.models import ScheduledVisit
        b = Beneficiary.objects.create(facility=self.facility, last_name='山田', first_name='太郎', date_of_birth=date(2016, 4, 1))
        ScheduledVisit.objects.create(facility=self.facility, beneficiary=b, date=date.today())
        res = self.client.get(reverse('facilities:dashboard'))
        self.assertContains(res, '今週の来所状況')
        self.assertContains(res, '山田 太郎')
        self.assertContains(res, 'dash-week')

    def test_support_tag_price_saved_and_billed(self):
        from datetime import date
        from beneficiaries.models import Beneficiary
        from billing.views import _build_invoice_context
        from facilities.models import SupportContentTag
        from records.models import DailyRecord
        res = self.client.post(reverse('facilities:support_tag_add'), {'name': '教材費', 'order': 1, 'price': 200, 'is_active': 'on'})
        tag = SupportContentTag.objects.get(name='教材費')
        self.assertEqual(tag.price, 200)
        b = Beneficiary.objects.create(facility=self.facility, last_name='山田', first_name='太郎', date_of_birth=date(2016, 4, 1))
        for d in (1, 2):
            r = DailyRecord.objects.create(facility=self.facility, beneficiary=b, date=date(2026, 9, d), author=self.user)
            r.support_tags.add(tag)
        ctx = _build_invoice_context(self.facility, b, 2026, 9)
        item = next(i for i in ctx['expense_items'] if i['name'] == '教材費')
        self.assertEqual((item['count'], item['subtotal']), (2, 400))
        self.assertEqual(ctx['expense_total'], 400)


class TermsSweepTests(TestCase):
    """施設の呼び方（職員／利用者）が各画面・メッセージに反映される"""

    def setUp(self):
        self.facility = Facility.objects.create(name='テスト事業所', term_staff='先生', term_beneficiary='園児')
        self.user = StaffAccount.objects.create_user(
            username='admin', password='pw12345678', facility=self.facility, role=StaffAccount.ROLE_ADMIN,
        )
        self.client.force_login(self.user)

    def test_pages_use_facility_terms(self):
        res = self.client.get(reverse('beneficiaries:list'))
        self.assertContains(res, '園児一覧')
        self.assertContains(res, '園児台帳')
        res = self.client.get(reverse('billing:matrix'))
        self.assertContains(res, '在籍中の園児がいません')
        res = self.client.get(reverse('billing:invoice_list'))
        self.assertContains(res, '各園児の請求書・領収書')
        res = self.client.get(reverse('facilities:dashboard'))
        self.assertContains(res, 'ログイン中の先生')
        res = self.client.get(reverse('accounts:theme'))
        self.assertContains(res, '先生ごとの割り当て')
        self.assertNotContains(res, '職員ごとの割り当て')

    def test_messages_and_titles_use_terms(self):
        res = self.client.get(reverse('beneficiaries:create'))
        self.assertContains(res, '園児 新規登録')
        res = self.client.post(reverse('beneficiaries:create'), {
            'last_name': '山田', 'first_name': '太郎', 'date_of_birth': '2016-04-01',
            'gender': 'male', 'status': 'active',
        }, follow=True)
        self.assertContains(res, '園児「山田 太郎」を登録しました')

    def test_default_terms_without_facility(self):
        from facilities.context_processors import get_terms
        self.assertEqual(get_terms(None), {'staff': '職員', 'beneficiary': '利用者'})
        self.facility.term_beneficiary = ''
        self.assertEqual(get_terms(self.user)['beneficiary'], '利用者')


class FeatureToggleTests(TestCase):
    """請求・LINE を使わない設定：メニューから消え、URL を開いてもホームへ"""

    def setUp(self):
        self.facility = Facility.objects.create(name='テスト事業所', use_billing=False, use_line=False)
        self.user = StaffAccount.objects.create_user('admin', password='pw12345678', facility=self.facility, role=StaffAccount.ROLE_ADMIN)
        self.client.force_login(self.user)

    def test_menus_hidden_and_views_redirect(self):
        res = self.client.get(reverse('facilities:dashboard'))
        self.assertNotContains(res, '請求マトリックス')
        self.assertNotContains(res, 'LINE連携')
        self.assertContains(res, '帳票出力')
        res = self.client.get(reverse('billing:matrix'))
        self.assertRedirects(res, reverse('facilities:dashboard'))
        res = self.client.get(reverse('line_integration:delivery_log'))
        self.assertRedirects(res, reverse('facilities:dashboard'))
        res = self.client.get(reverse('facilities:settings'))
        self.assertNotContains(res, 'LINEチャネルアクセストークン')
        self.assertNotContains(res, '加算設定（体制加算）')
        self.assertContains(res, '使う機能と、日誌で AI が作る項目')

    def test_save_feature_settings(self):
        res = self.client.post(reverse('facilities:feature_settings'), {
            'use_billing': 'on', 'journal_sections': ['reaction', 'observation']})
        self.assertRedirects(res, reverse('facilities:settings'))
        self.facility.refresh_from_db()
        self.assertTrue(self.facility.use_billing)
        self.assertFalse(self.facility.use_line)
        self.assertEqual(self.facility.journal_sections, ['reaction', 'observation'])
        res = self.client.get(reverse('billing:matrix'))
        self.assertEqual(res.status_code, 200)
        # 項目なしは拒否
        self.client.post(reverse('facilities:feature_settings'), {'use_billing': 'on'})
        self.facility.refresh_from_db()
        self.assertEqual(self.facility.journal_sections, ['reaction', 'observation'])
        # 一般職員は変えられない
        staff = StaffAccount.objects.create_user('staff', password='pw12345678', facility=self.facility)
        self.client.force_login(staff)
        self.client.post(reverse('facilities:feature_settings'), {'use_line': 'on', 'journal_sections': ['activity']})
        self.facility.refresh_from_db()
        self.assertFalse(self.facility.use_line)

    def test_new_facility_defaults_on(self):
        f = Facility.objects.create(name='新しい施設')
        self.assertTrue(f.use_billing and f.use_line)


class SeedDemoCreateFacilityTests(TestCase):
    def test_create_facility_option(self):
        admin = StaffAccount.objects.create_superuser('root', 'r@example.com', 'pw12345678')
        out = StringIO()
        call_command('seed_demo', '--create-facility', 'あおば', stdout=out)
        admin.refresh_from_db()
        self.assertEqual(admin.facility.name, 'あおば')
        self.assertIn('作成し', out.getvalue())
        self.assertTrue(Beneficiary.objects.filter(facility=admin.facility).exists())


class CreateFacilityCommandTests(TestCase):
    def test_create_with_admin_and_copy(self):
        src = Facility.objects.create(name='本店', term_beneficiary='園児', use_billing=False, journal_sections=['support', 'observation'])
        out = StringIO()
        call_command('create_facility', 'あおば教室', '--admin', 'aoba', '--password', 'pw12345678',
                     '--copy-settings-from', str(src.pk), '--office-number', '1234567890', stdout=out)
        f = Facility.objects.get(name='あおば教室')
        self.assertEqual(f.term_beneficiary, '園児')
        self.assertFalse(f.use_billing)
        self.assertEqual(f.journal_sections, ['support', 'observation'])
        self.assertEqual(f.office_number, '1234567890')
        u = StaffAccount.objects.get(username='aoba')
        self.assertEqual(u.facility, f)
        self.assertTrue(u.is_admin)
        self.assertTrue(u.check_password('pw12345678'))
        self.assertGreater(ActivityTag.objects.filter(facility=f).count(), 10)
        self.assertGreater(SupportContentTag.objects.filter(facility=f).count(), 10)
        self.assertEqual(ActivityTag.objects.filter(facility=src).count(), 0)
        # 同名・同ユーザーは拒否
        with self.assertRaises(CommandError):
            call_command('create_facility', 'あおば教室', stdout=StringIO())
        with self.assertRaises(CommandError):
            call_command('create_facility', '別', '--admin', 'aoba', '--password', 'pw12345678', stdout=StringIO())
        # ログインして自施設だけ見える
        self.client.login(username='aoba', password='pw12345678')
        res = self.client.get(reverse('beneficiaries:list'))
        self.assertContains(res, '園児')

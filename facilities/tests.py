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
        self.assertEqual(bens.count(), 7)
        self.assertTrue(all('サンプルデータ' in b.notes for b in bens))
        self.assertEqual(Guardian.objects.filter(beneficiary__in=bens).count(), 8)
        self.assertEqual(RecipientCertificate.objects.filter(beneficiary__in=bens).count(), 7)
        # 重身の子：重身用の日誌と利用事業所（当施設が上限管理事業所）
        severe = bens.get(is_severe=True)
        self.assertTrue(severe.is_copayment_manager_here)
        self.assertEqual(severe.other_offices.count(), 2)
        self.assertTrue(severe.daily_records.filter(record_kind='severe').exclude(severe_care={}).exists())
        self.assertFalse(bens.filter(is_severe=False, daily_records__record_kind='severe').exists())
        self.facility.refresh_from_db()
        self.assertEqual(self.facility.base_unit_count_severe, 1756)
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
        self.assertEqual(Beneficiary.objects.filter(facility=self.facility).count(), 7)

    def test_reset_replaces_sample_data_but_keeps_real_users(self):
        real = Beneficiary.objects.create(
            facility=self.facility, last_name='実在', first_name='太郎',
            date_of_birth=datetime.date(2015, 1, 1),
        )
        call_command('seed_demo', stdout=StringIO())
        call_command('seed_demo', '--reset', stdout=StringIO())
        self.assertEqual(Beneficiary.objects.filter(facility=self.facility).count(), 8)
        self.assertTrue(Beneficiary.objects.filter(pk=real.pk).exists())

    def test_requires_facility_choice_when_multiple(self):
        Facility.objects.create(name='もう一つの施設')
        with self.assertRaises(CommandError):
            call_command('seed_demo', stdout=StringIO())
        call_command('seed_demo', '--facility', str(self.facility.pk), stdout=StringIO())
        self.assertEqual(Beneficiary.objects.filter(facility=self.facility).count(), 7)


class DashboardWeekAndSupportTagPriceTests(TestCase):
    def setUp(self):
        from accounts.models import StaffAccount
        self.facility = Facility.objects.create(name='F')
        self.user = StaffAccount.objects.create_user('s', password='p', facility=self.facility, role=StaffAccount.ROLE_ADMIN)
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
        self.assertTrue(f.use_billing and f.use_line and f.use_schedule)

    def test_schedule_can_be_turned_off(self):
        """予定（来所予定・出欠）を使わない事業所：メニュー・ホーム・画面から消え、直接開いてもホームへ戻す"""
        import datetime
        res = self.client.get(reverse('facilities:dashboard'))
        self.assertContains(res, '今週の来所状況')
        self.assertContains(res, reverse('schedules:calendar'))
        self.client.post(reverse('facilities:feature_settings'), {'use_billing': 'on', 'journal_sections': ['activity']})
        self.facility.refresh_from_db()
        self.assertFalse(self.facility.use_schedule)
        res = self.client.get(reverse('facilities:dashboard'))
        self.assertNotContains(res, '今週の来所状況')
        self.assertNotContains(res, '今日の来所予定')
        self.assertNotContains(res, reverse('schedules:calendar'))
        self.assertNotContains(res, '<span>予定</span>')
        today = datetime.date.today()
        for url in (reverse('schedules:calendar'), reverse('schedules:daily', args=[today.year, today.month, today.day])):
            self.assertRedirects(self.client.get(url), reverse('facilities:dashboard'))
        self.assertRedirects(self.client.post(reverse('schedules:daily_save', args=[today.year, today.month, today.day])),
                             reverse('facilities:dashboard'))
        # 戻せる
        self.client.post(reverse('facilities:feature_settings'), {'use_schedule': 'on', 'journal_sections': ['activity']})
        self.facility.refresh_from_db()
        self.assertTrue(self.facility.use_schedule)
        self.assertEqual(self.client.get(reverse('schedules:calendar')).status_code, 200)

    def test_ryoiku_layout_menu(self):
        """療育（ゆあーず）の型：基本機能／お試し／設定の3つの見出し"""
        f = self.facility
        f.layout = Facility.LAYOUT_RYOIKU
        f.use_reservation = f.use_therapy_record = True
        f.use_billing = f.use_schedule = False
        f.trial_ai_limit = 20
        f.save()
        res = self.client.get(reverse('facilities:dashboard'))
        html = res.content.decode()
        for text in ('基本機能', 'お試し', '（AI あと 20 回）', '利用者情報', '運用管理', '議事録', '療育記録', '支援計画'):
            self.assertIn(text, html)
        for text in ('利用者台帳', '職員・運用管理', '帳票出力', '<span>予定</span>', '請求マトリックス'):
            self.assertNotIn(text, html)
        self.assertLess(html.index('基本機能'), html.index('お試し'))
        self.assertLess(html.index('お試し'), html.index('<span>記録</span>'))
        # いま開いている画面に印が付く
        res = self.client.get(reverse('therapy:index'))
        self.assertRegex(res.content.decode(), r'sidebar-nav-item active" href="/therapy/"')
        # 一般職員には運用管理が出ない
        staff = StaffAccount.objects.create_user('staff2', password='pw12345678', facility=f)
        self.client.force_login(staff)
        self.assertNotContains(self.client.get(reverse('facilities:dashboard')), '運用管理')

    def test_ryoiku_layout_migration(self):
        from importlib import import_module
        from django.apps import apps
        mig = import_module('facilities.migrations.0017_ryoiku_layout_trial')
        yours = Facility.objects.create(name='発達支援ルーム　ゆあーず')
        other = Facility.objects.create(name='ほかの事業所')
        mig.set_ryoiku(apps, None)
        yours.refresh_from_db(); other.refresh_from_db()
        self.assertEqual((yours.layout, yours.trial_ai_limit), ('ryoiku', 20))
        self.assertEqual((other.layout, other.trial_ai_limit), ('standard', 0))
        mig.unset_ryoiku(apps, None)
        yours.refresh_from_db()
        self.assertEqual((yours.layout, yours.trial_ai_limit), ('standard', 0))

    def test_trial_ai_settings_developer_only(self):
        f = self.facility
        f.trial_ai_limit, f.trial_ai_used = 20, 5
        f.save()
        res = self.client.get(reverse('facilities:settings'))
        self.assertContains(res, '20 回</b>まで使えます')
        self.assertNotContains(res, 'name="trial_ai_limit"')
        # 管理者が送っても変わらない
        self.client.post(reverse('facilities:feature_settings'), {'journal_sections': ['activity'], 'trial_ai_limit': 0, 'trial_ai_reset': '1'})
        f.refresh_from_db()
        self.assertEqual((f.trial_ai_limit, f.trial_ai_used), (20, 5))
        # 開発向けユーザーは変えられる・0 に戻せる
        self.user.is_developer = True
        self.user.save()
        res = self.client.get(reverse('facilities:settings'))
        self.assertContains(res, 'name="trial_ai_limit"')
        self.client.post(reverse('facilities:feature_settings'), {'journal_sections': ['activity'], 'trial_ai_limit': 30, 'trial_ai_reset': '1',
                                                                   'form_set': f.form_set, 'layout': f.layout})
        f.refresh_from_db()
        self.assertEqual((f.trial_ai_limit, f.trial_ai_used), (30, 0))

    def test_ryoiku_migration_turns_off_billing_and_schedule(self):
        from importlib import import_module
        from django.apps import apps
        mig = import_module('facilities.migrations.0016_use_schedule')
        yours = Facility.objects.create(name='発達支援ルーム　ゆあーず')
        other = Facility.objects.create(name='ほかの事業所')
        mig.turn_off_for_ryoiku(apps, None)
        yours.refresh_from_db(); other.refresh_from_db()
        self.assertFalse(yours.use_billing or yours.use_schedule)
        self.assertTrue(other.use_billing and other.use_schedule)
        mig.turn_back_on(apps, None)
        yours.refresh_from_db()
        self.assertTrue(yours.use_billing and yours.use_schedule)


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


class AddonDefaultsLoadTests(TestCase):
    """標準の加算マスタの合わせ込み：追加・名前の付け替え・単位数の更新・無効化"""

    def test_rename_update_and_retire(self):
        from facilities.models import AddonMaster
        from facilities.views import DEFAULT_ADDONS, load_default_addons
        old = AddonMaster.objects.create(name='家族支援加算（オンライン）', addon_type='individual', unit_count=100)
        stale = AddonMaster.objects.create(name='欠席時対応加算', addon_type='individual', unit_count=90)
        keep = AddonMaster.objects.create(name='送迎加算（往・迎え）', addon_type='individual', unit_count=54, code='999999')
        gone = AddonMaster.objects.create(name='医療連携体制加算', addon_type='individual', unit_count=0)
        added, renamed, updated, retired = load_default_addons()
        old.refresh_from_db(); stale.refresh_from_db(); gone.refresh_from_db()
        self.assertEqual((old.name, old.unit_count), ('家族支援加算Ⅰ（オンライン）', 80))
        self.assertEqual((stale.unit_count, stale.code), (94, '635495'))     # 空のコードは標準で埋める
        keep.refresh_from_db()
        self.assertEqual(keep.code, '999999')                                  # 入力ずみのコードは残す
        self.assertFalse(gone.is_active)
        self.assertEqual((renamed, updated, retired), (1, 2, 1))
        self.assertEqual(added, len(DEFAULT_ADDONS) - 3)
        # 2回目は何も変わらない
        self.assertEqual(load_default_addons(), (0, 0, 0, 0))
        # 標準の名前は重複していない
        names = [i['name'] for i in DEFAULT_ADDONS]
        self.assertEqual(len(names), len(set(names)))

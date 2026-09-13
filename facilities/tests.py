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

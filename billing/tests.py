from django.test import TestCase

# Create your tests here.

from decimal import Decimal

from django.urls import reverse

from accounts.models import StaffAccount
from beneficiaries.models import Beneficiary, BeneficiaryOffice, RecipientCertificate
from facilities.models import AddonMaster, Facility, FacilityAddonSetting, facility_addon_rows
from .models import BillingMatrixEntry, CopaymentManagement, CopaymentOfficeRecord


class AddonSettingsTests(TestCase):
    """加算のコード・単位数・料金（施設設定）"""

    def setUp(self):
        self.facility = Facility.objects.create(name='はぴねす', region_category='3', use_billing=True)
        self.admin = StaffAccount.objects.create_user(username='admin', password='pw12345678', facility=self.facility, role=StaffAccount.ROLE_ADMIN)
        self.client.force_login(self.admin)
        self.a1 = AddonMaster.objects.create(name='送迎加算（往・迎え）', addon_type='individual', unit_count=54, code='615001')
        self.a2 = AddonMaster.objects.create(name='児童指導員等加配加算', addon_type='facility', unit_count=0)

    def test_settings_page_lists_code_units_price(self):
        res = self.client.get(reverse('facilities:settings'))
        self.assertContains(res, 'サービスコード')
        self.assertContains(res, '615001')
        self.assertContains(res, '個別加算（利用者×日）')
        self.assertContains(res, '体制加算（施設全体・月単位）')
        # 54 × 11.05 = 596.7 → 596円
        self.assertContains(res, '>596<')

    def test_save_overrides_and_reset_to_master(self):
        res = self.client.post(reverse('facilities:addon_setting'), {
            'addon_ids': [str(self.a1.pk), str(self.a2.pk)],
            f'code_{self.a1.pk}': '615001', f'units_{self.a1.pk}': '54',
            f'code_{self.a2.pk}': '615200', f'units_{self.a2.pk}': '187',
        })
        self.assertEqual(res.status_code, 302)
        s1 = FacilityAddonSetting.objects.get(facility=self.facility, addon=self.a1)
        s2 = FacilityAddonSetting.objects.get(facility=self.facility, addon=self.a2)
        # マスタと同じ値は上書きとして持たない
        self.assertEqual(s1.code, '')
        self.assertIsNone(s1.unit_count)
        self.assertTrue(s1.is_enabled)
        self.assertEqual(s2.code, '615200')
        self.assertEqual(s2.unit_count, 187)
        rows = {r.pk: r for r in facility_addon_rows(self.facility)}
        self.assertEqual(rows[self.a2.pk].code, '615200')
        self.assertEqual(rows[self.a2.pk].unit_count, 187)
        self.assertEqual(rows[self.a2.pk].price_yen, int(Decimal(187) * Decimal('11.05')))
        # 空に戻すとマスタの値
        self.client.post(reverse('facilities:addon_setting'), {f'code_{self.a2.pk}': '', f'units_{self.a2.pk}': ''})
        s2.refresh_from_db()
        self.assertEqual(s2.code, '')
        self.assertIsNone(s2.unit_count)
        self.assertFalse(s2.is_enabled)

    def test_enabled_only_falls_back_to_all(self):
        rows = facility_addon_rows(self.facility, addon_type='individual', enabled_only=True)
        self.assertEqual([r.pk for r in rows], [self.a1.pk])
        FacilityAddonSetting.objects.create(facility=self.facility, addon=self.a1, is_enabled=False)
        self.assertEqual(facility_addon_rows(self.facility, addon_type='individual', enabled_only=True), [])

    def test_cell_popup_shows_code(self):
        ben = Beneficiary.objects.create(facility=self.facility, last_name='佐藤', first_name='はると', date_of_birth='2017-05-12')
        res = self.client.get(reverse('billing:cell_popup', args=[ben.pk, 2026, 9, 15]))
        self.assertContains(res, '615001 · 54単位')


class CopaymentSheetTests(TestCase):
    """上限額管理：利用事業所からの初期値と、管理結果票＋送付状の出力"""

    def setUp(self):
        self.facility = Facility.objects.create(name='はぴねす', office_number='2650000001', use_billing=True,
                                                base_unit_count=604, base_unit_count_severe=1756, region_category='3')
        self.user = StaffAccount.objects.create_user(username='staff', password='pw12345678', facility=self.facility)
        self.client.force_login(self.user)
        self.ben = Beneficiary.objects.create(facility=self.facility, last_name='中村', first_name='みお',
                                              date_of_birth='2015-09-14', is_severe=True)
        RecipientCertificate.objects.create(beneficiary=self.ben, certificate_number='2600001234', granted_days=23,
                                            monthly_cap=4600, valid_from='2026-04-01', valid_until='2027-03-31', municipality='京都市')
        BeneficiaryOffice.objects.create(beneficiary=self.ben, name='はぴねす', office_number='2650000001', is_this_office=True, is_manager=True)
        BeneficiaryOffice.objects.create(beneficiary=self.ben, name='児童デイ ひまわり', office_number='2650000101',
                                         fax='075-000-0200', contact_name='担当 太郎', order=1)

    def test_edit_page_prefills_offices(self):
        res = self.client.get(reverse('billing:copayment_edit', args=[self.ben.pk, 2026, 9]))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'value="児童デイ ひまわり"')
        self.assertContains(res, 'value="2650000101"')
        self.assertContains(res, 'value="はぴねす"')
        self.assertContains(res, 'checked', msg_prefix='当施設が上限管理事業所なので初期値でチェック')

    def _save(self):
        data = {
            'is_upper_limit_manager': 'on', 'management_result': '3',
            'office_records-TOTAL_FORMS': '2', 'office_records-INITIAL_FORMS': '0',
            'office_records-MIN_NUM_FORMS': '0', 'office_records-MAX_NUM_FORMS': '1000',
            'office_records-0-office_name': 'はぴねす', 'office_records-0-office_number': '2650000001',
            'office_records-0-total_cost': '60000', 'office_records-0-original_copayment': '6000', 'office_records-0-adjusted_copayment': '4600',
            'office_records-1-office_name': '児童デイ ひまわり', 'office_records-1-office_number': '2650000101',
            'office_records-1-total_cost': '30000', 'office_records-1-original_copayment': '3000', 'office_records-1-adjusted_copayment': '0',
        }
        return self.client.post(reverse('billing:copayment_edit', args=[self.ben.pk, 2026, 9]), data)

    def test_save_marks_this_office_and_sheet_html(self):
        res = self._save()
        self.assertEqual(res.status_code, 302)
        m = CopaymentManagement.objects.get(beneficiary=self.ben, year_month='2026-09')
        mine = CopaymentOfficeRecord.objects.get(management=m, is_this_office=True)
        self.assertEqual(mine.office_number, '2650000001')
        self.assertEqual(m.this_office_copayment, 4600)
        # 一覧に管理票ボタン
        res = self.client.get(reverse('billing:copayment_list_month', args=[2026, 9]))
        self.assertContains(res, '管理票')
        # 送付状＋結果票（画面表示）
        res = self.client.get(reverse('billing:copayment_sheet', args=[self.ben.pk, 2026, 9]) + '?fmt=html')
        self.assertEqual(res.status_code, 200)
        html = res.content.decode()
        self.assertIn('送　付　状', html)
        self.assertIn('児童デイ ひまわり　御中', html)
        self.assertIn('FAX：075-000-0200', html)
        self.assertIn('利用者負担上限額管理結果票', html)
        self.assertIn('2600001234', html)
        self.assertIn('管理事業所', html)
        # 送付先を1事業所に絞れる
        res = self.client.get(reverse('billing:copayment_sheet', args=[self.ben.pk, 2026, 9]) + f'?fmt=html&office={mine.pk}')
        self.assertNotIn('御中', res.content.decode())

    def test_sheet_refused_when_not_manager(self):
        self._save()
        CopaymentManagement.objects.filter(beneficiary=self.ben).update(is_upper_limit_manager=False)
        res = self.client.get(reverse('billing:copayment_sheet', args=[self.ben.pk, 2026, 9]) + '?fmt=html')
        self.assertEqual(res.status_code, 302)

    def test_invoice_uses_severe_units(self):
        from .views import _build_invoice_context
        for d in (1, 2, 3):
            BillingMatrixEntry.objects.create(facility=self.facility, beneficiary=self.ben, date=f'2026-09-0{d}', status='attended')
        ctx = _build_invoice_context(self.facility, self.ben, 2026, 9)
        self.assertEqual(ctx['total_cost'], int(Decimal(1756) * Decimal('11.05') * 3))
        self.ben.is_severe = False
        self.ben.save()
        ctx = _build_invoice_context(self.facility, self.ben, 2026, 9)
        self.assertEqual(ctx['total_cost'], int(Decimal(604) * Decimal('11.05') * 3))

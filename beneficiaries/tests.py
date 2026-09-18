from django.test import TestCase

# Create your tests here.


from django.urls import reverse

from accounts.models import StaffAccount
from facilities.models import Facility
from .models import Beneficiary, BeneficiaryOffice


class BeneficiaryOfficeTests(TestCase):
    """利用事業所（上限管理事業所のフラグ）と重身フラグ"""

    def setUp(self):
        self.facility = Facility.objects.create(name='はぴねす', office_number='2650000001')
        self.user = StaffAccount.objects.create_user(username='staff', password='pw12345678', facility=self.facility)
        self.client.force_login(self.user)
        self.ben = Beneficiary.objects.create(
            facility=self.facility, last_name='中村', first_name='みお', date_of_birth='2015-09-14',
        )

    def test_add_office_and_manager_flag_is_unique(self):
        url = reverse('beneficiaries:office_create', args=[self.ben.pk])
        self.client.post(url, {'name': 'ひまわり', 'office_number': '2650000101', 'is_manager': 'on', 'order': 1})
        self.client.post(url, {'name': 'はぴねす', 'is_this_office': 'on', 'is_manager': 'on', 'order': 0})
        offices = list(self.ben.offices.all())
        self.assertEqual(len(offices), 2)
        managers = [o for o in offices if o.is_manager]
        self.assertEqual(len(managers), 1)
        self.assertTrue(managers[0].is_this_office)
        # 当施設の行は施設設定の番号を使う
        self.assertEqual(managers[0].office_number, '2650000001')
        self.assertTrue(self.ben.is_copayment_manager_here)
        self.assertEqual(self.ben.other_offices.count(), 1)

    def test_detail_shows_offices(self):
        BeneficiaryOffice.objects.create(beneficiary=self.ben, name='そら', office_number='2650000102', is_manager=True)
        res = self.client.get(reverse('beneficiaries:detail', args=[self.ben.pk]))
        self.assertContains(res, 'そら')
        self.assertContains(res, '上限管理事業所')
        self.assertFalse(self.ben.is_copayment_manager_here)

    def test_update_and_delete_office(self):
        o = BeneficiaryOffice.objects.create(beneficiary=self.ben, name='そら', office_number='2650000102')
        self.client.post(reverse('beneficiaries:office_update', args=[self.ben.pk, o.pk]),
                         {'name': 'そら（本店）', 'office_number': '2650000102', 'order': 0, 'fax': '075-000-0200'})
        o.refresh_from_db()
        self.assertEqual(o.name, 'そら（本店）')
        self.assertEqual(o.fax, '075-000-0200')
        self.client.post(reverse('beneficiaries:office_delete', args=[self.ben.pk, o.pk]))
        self.assertFalse(BeneficiaryOffice.objects.filter(pk=o.pk).exists())

    def test_other_facility_cannot_touch_offices(self):
        other = Facility.objects.create(name='別の事業所')
        other_ben = Beneficiary.objects.create(facility=other, last_name='他', first_name='子', date_of_birth='2015-01-01')
        res = self.client.post(reverse('beneficiaries:office_create', args=[other_ben.pk]), {'name': 'x', 'order': 0})
        self.assertEqual(res.status_code, 404)

    def test_is_severe_saved_from_edit(self):
        res = self.client.post(reverse('beneficiaries:update', args=[self.ben.pk]), {
            'last_name': '中村', 'first_name': 'みお', 'date_of_birth': '2015-09-14', 'gender': 'female',
            'status': 'active', 'is_severe': 'on',
        })
        self.assertEqual(res.status_code, 302)
        self.ben.refresh_from_db()
        self.assertTrue(self.ben.is_severe)
        self.facility.base_unit_count = 604
        self.facility.base_unit_count_severe = 1756
        self.assertEqual(self.facility.base_units_for(self.ben), 1756)
        self.facility.base_unit_count_severe = None
        self.assertEqual(self.facility.base_units_for(self.ben), 604)

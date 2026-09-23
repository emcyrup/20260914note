import datetime

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


class AssessmentTests(TestCase):
    """利用者台帳のアセスメント・資料"""

    def setUp(self):
        import shutil
        import tempfile
        from django.test import override_settings
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        self.override = override_settings(MEDIA_ROOT=self.media)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.f = Facility.objects.create(name='発達支援ルーム　ゆあーず')
        self.user = StaffAccount.objects.create_user('ryo', password='pw12345678', facility=self.f,
                                                     role=StaffAccount.ROLE_STAFF, display_name='永山')
        self.client.login(username='ryo', password='pw12345678')
        self.kid = Beneficiary.objects.create(facility=self.f, last_name='青木', first_name='子',
                                              date_of_birth=datetime.date(2019, 4, 1))
        self.detail = reverse('beneficiaries:detail', args=[self.kid.pk])

    def pdf(self, name='kensa.pdf', size=100):
        from django.core.files.uploadedfile import SimpleUploadedFile
        return SimpleUploadedFile(name, b'%PDF-1.4 ' + b'0' * size, content_type='application/pdf')

    def test_add_text_and_file_then_open(self):
        from .models import BeneficiaryAssessment
        url = reverse('beneficiaries:assessment_create', args=[self.kid.pk])
        res = self.client.post(url, {'date': '2026-09-01', 'kind': 'test', 'title': '新版K式',
                                     'content': '言語 3歳相当。\n手先が器用。', 'file': self.pdf()})
        self.assertRedirects(res, self.detail + '#assessments', fetch_redirect_response=False)
        a = BeneficiaryAssessment.objects.get()
        self.assertEqual((a.kind, a.title, a.file_name, a.created_by), ('test', '新版K式', 'kensa.pdf', self.user))
        self.assertTrue(a.file.name.startswith('beneficiary_assessments/'))
        self.assertNotIn('kensa', a.file.name)
        res = self.client.get(self.detail)
        self.assertContains(res, '発達検査・評価')
        self.assertContains(res, '言語 3歳相当。')
        self.assertContains(res, a.file.url)
        # 書類は同じ事業所の職員だけが開ける
        self.assertEqual(self.client.get(a.file.url).status_code, 200)
        other = Facility.objects.create(name='ほか')
        StaffAccount.objects.create_user('oth', password='pw12345678', facility=other, role=StaffAccount.ROLE_STAFF)
        self.client.login(username='oth', password='pw12345678')
        self.assertEqual(self.client.get(a.file.url).status_code, 404)
        self.assertEqual(self.client.post(url, {'date': '2026-09-01', 'content': 'x'}).status_code, 404)

    def test_needs_content_or_file_and_checks_file(self):
        from .models import BeneficiaryAssessment
        url = reverse('beneficiaries:assessment_create', args=[self.kid.pk])
        self.client.post(url, {'date': '2026-09-01', 'title': '空'})
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.client.post(url, {'date': '2026-09-01', 'content': 'x', 'file': SimpleUploadedFile('a.exe', b'MZ')})
        self.client.post(url, {'date': '', 'content': 'x'})
        self.assertFalse(BeneficiaryAssessment.objects.exists())
        # 件名が空なら種類の名前
        self.client.post(url, {'date': '2026-09-01', 'kind': 'interview', 'content': '母より聞き取り'})
        self.assertEqual(BeneficiaryAssessment.objects.get().title, '面談・聞き取り')

    def test_edit_replace_remove_and_delete_file(self):
        import os
        from .models import BeneficiaryAssessment
        a = BeneficiaryAssessment.objects.create(beneficiary=self.kid, date=datetime.date(2026, 9, 1), title='初回',
                                                 content='x', file=self.pdf(), file_name='kensa.pdf')
        first = os.path.join(self.media, a.file.name)
        self.assertTrue(os.path.exists(first))
        edit = reverse('beneficiaries:assessment_update', args=[self.kid.pk, a.pk])
        self.client.post(edit, {'date': '2026-09-02', 'kind': 'assessment', 'title': '初回（直し）', 'content': 'y',
                                'file': self.pdf('new.pdf')})
        a.refresh_from_db()
        self.assertEqual((a.title, a.file_name), ('初回（直し）', 'new.pdf'))
        self.assertFalse(os.path.exists(first))            # 入れ替えた古い書類は消す
        second = os.path.join(self.media, a.file.name)
        self.client.post(edit, {'date': '2026-09-02', 'title': '初回', 'content': 'y', 'remove_file': '1'})
        a.refresh_from_db()
        self.assertFalse(a.file)
        self.assertFalse(os.path.exists(second))
        self.client.post(reverse('beneficiaries:assessment_delete', args=[self.kid.pk, a.pk]))
        self.assertFalse(BeneficiaryAssessment.objects.exists())

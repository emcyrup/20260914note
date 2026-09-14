"""アップロードファイルの配信：ログイン必須・自事業所のものだけ・署名 URL"""
import shutil
import tempfile
from datetime import date

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import StaffAccount
from beneficiaries.models import Beneficiary
from facilities.media import signed_media_url
from facilities.models import Facility
from records.models import DailyRecord, DailyRecordPhoto

PNG = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89'
       b'\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x01\x01\x01\x00\x18\xdd\x8d\xb4\x00\x00\x00\x00IEND\xaeB`\x82')

_TMP = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=_TMP)
class ProtectedMediaTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_TMP, ignore_errors=True)

    def setUp(self):
        self.a = Facility.objects.create(name='A')
        self.b = Facility.objects.create(name='B')
        self.ua = StaffAccount.objects.create_user('ua', password='pass12345', facility=self.a)
        self.ub = StaffAccount.objects.create_user('ub', password='pass12345', facility=self.b)
        ben = Beneficiary.objects.create(facility=self.a, last_name='佐', first_name='藤', date_of_birth=date(2016, 4, 1))
        rec = DailyRecord.objects.create(facility=self.a, beneficiary=ben, date=date(2026, 9, 1))
        self.photo = DailyRecordPhoto.objects.create(facility=self.a, daily_record=rec,
                                                     photo=SimpleUploadedFile('IMG_1234.jpg', PNG, content_type='image/png'))
        self.url = self.photo.photo.url

    def test_filename_is_not_guessable(self):
        self.assertNotIn('IMG_1234', self.photo.photo.name)
        self.assertTrue(self.photo.photo.name.startswith('daily_record_photos/'))

    def test_anonymous_is_redirected_to_login(self):
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 302)
        self.assertIn(reverse('accounts:login'), res['Location'])

    def test_own_facility_can_read(self):
        self.client.force_login(self.ua)
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(b''.join(res.streaming_content), PNG)

    def test_other_facility_gets_404(self):
        self.client.force_login(self.ub)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_unknown_prefix_and_traversal(self):
        self.client.force_login(self.ua)
        self.assertEqual(self.client.get('/media/other/x.png').status_code, 404)
        self.assertEqual(self.client.get('/media/../settings.py').status_code, 404)
        self.assertEqual(self.client.get('/media/daily_record_photos/../../x').status_code, 404)

    def test_signed_url_works_without_login_and_expires(self):
        url = signed_media_url(self.photo.photo.name, max_age=60)
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        # 署名を改ざん
        self.assertEqual(self.client.get(url.replace('sig=', 'sig=x')).status_code, 404)
        # 期限切れ（max_age を 0 にしても署名の max_age が使われるわけではないので、署名時刻をずらす）
        from unittest import mock
        with mock.patch('django.core.signing.time.time', return_value=1_000_000):
            old = signed_media_url(self.photo.photo.name, max_age=60)
        self.assertEqual(self.client.get(old).status_code, 404)

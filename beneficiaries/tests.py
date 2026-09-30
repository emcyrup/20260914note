import datetime
import json
from unittest import mock

from django.test import TestCase, override_settings

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


class BeneficiaryDocumentTests(TestCase):
    """利用者の基本情報の書類・画像（写真・PDF・Excel・CSV。ゆあーずだけ）"""

    def setUp(self):
        import datetime
        from accounts.models import StaffAccount
        from facilities.models import Facility
        self.f = Facility.objects.create(name='発達支援ルーム　ゆあーず', layout=Facility.LAYOUT_RYOIKU)
        self.user = StaffAccount.objects.create_user('ryo', password='pw12345678', facility=self.f, role=StaffAccount.ROLE_STAFF)
        self.client.login(username='ryo', password='pw12345678')
        self.kid = Beneficiary.objects.create(facility=self.f, last_name='青木', first_name='子', date_of_birth=datetime.date(2019, 4, 1))
        self.url = reverse('beneficiaries:document_upload', args=[self.kid.pk])

    @staticmethod
    def up(name, data=b'x', ctype='application/octet-stream'):
        from django.core.files.uploadedfile import SimpleUploadedFile
        return SimpleUploadedFile(name, data, content_type=ctype)

    def test_upload_list_delete(self):
        from .models import BeneficiaryDocument
        res = self.client.get(reverse('beneficiaries:detail', args=[self.kid.pk]))
        self.assertContains(res, 'ここにファイルを置く')
        self.assertContains(res, 'js/dropzone.js')
        png = b'\x89PNG\r\n\x1a\n' + b'0' * 20
        res = self.client.post(self.url, {'files': [self.up('受給者証.png', png, 'image/png'), self.up('診断書.PDF'),
                                                    self.up('名簿.xlsx'), self.up('data.csv'), self.up('virus.exe')], 'title': '無視される'}, follow=True)
        self.assertContains(res, '書類を 4 件取り込みました')
        self.assertContains(res, 'virus.exe（この種類は入れられません）')
        docs = list(BeneficiaryDocument.objects.filter(beneficiary=self.kid).order_by('pk'))
        self.assertEqual([d.kind for d in docs], ['image', 'pdf', 'sheet', 'sheet'])
        self.assertEqual([d.title for d in docs], ['', '', '', ''])          # 複数のときは件名を付けない
        self.assertContains(res, '書類・画像（4）')
        self.assertContains(res, '受給者証.png')
        self.assertContains(res, 'bi-file-earmark-spreadsheet')
        # 1つだけなら件名が付く。ファイルはこの事業所の職員だけが開ける
        self.client.post(self.url, {'files': [self.up('契約書.jpg', png, 'image/jpeg')], 'title': '契約書 2026'})
        d = BeneficiaryDocument.objects.get(file_name='契約書.jpg')
        self.assertEqual(d.label, '契約書 2026')
        self.assertTrue(d.file.name.startswith('beneficiary_documents/'))
        self.assertEqual(self.client.get(d.file.url).status_code, 200)
        # 削除
        res = self.client.post(reverse('beneficiaries:document_delete', args=[self.kid.pk, d.pk]), follow=True)
        self.assertContains(res, '「契約書 2026」を削除しました')
        self.assertFalse(BeneficiaryDocument.objects.filter(pk=d.pk).exists())
        # 空・大きすぎ
        res = self.client.post(self.url, {}, follow=True)
        self.assertContains(res, 'ファイルを選ぶか')
        from .models import DOCUMENT_MAX_BYTES
        big = self.up('big.pdf', b'0' * (DOCUMENT_MAX_BYTES + 1))
        res = self.client.post(self.url, {'files': [big]}, follow=True)
        self.assertContains(res, 'MB を超えています')

    def test_other_facility_and_non_ryoiku(self):
        import datetime
        from facilities.models import Facility
        from accounts.models import StaffAccount
        g = Facility.objects.create(name='ほか', layout=Facility.LAYOUT_RYOIKU)
        other = Beneficiary.objects.create(facility=g, last_name='井上', first_name='子', date_of_birth=datetime.date(2019, 4, 1))
        self.assertEqual(self.client.post(reverse('beneficiaries:document_upload', args=[other.pk]), {'files': [self.up('a.pdf')]}).status_code, 404)
        # 標準の型の事業所には出ない・受け付けない
        self.f.layout = Facility.LAYOUT_STANDARD
        self.f.save(update_fields=['layout'])
        res = self.client.get(reverse('beneficiaries:detail', args=[self.kid.pk]))
        self.assertNotContains(res, 'ここにファイルを置く')
        self.assertEqual(self.client.post(self.url, {'files': [self.up('a.pdf')]}).status_code, 404)


class RecentTherapyOnDetailTests(TestCase):
    """利用者の詳細に療育記録の直近5日分（ゆあーず）"""

    def setUp(self):
        import datetime
        from accounts.models import StaffAccount
        from facilities.models import Facility
        from therapy.models import TherapyRecord
        self.f = Facility.objects.create(name='発達支援ルーム　ゆあーず', layout=Facility.LAYOUT_RYOIKU,
                                         use_reservation=True, use_therapy_record=True)
        self.user = StaffAccount.objects.create_user('ryo', password='pw12345678', facility=self.f, role=StaffAccount.ROLE_STAFF, display_name='大坂')
        self.client.login(username='ryo', password='pw12345678')
        self.kid = Beneficiary.objects.create(facility=self.f, last_name='青木', first_name='子', date_of_birth=datetime.date(2019, 4, 1))
        for i in range(7):
            TherapyRecord.objects.create(facility=self.f, beneficiary=self.kid, date=datetime.date(2026, 10, 1) + datetime.timedelta(days=i * 2),
                                         time=datetime.time(10, 0), staff=self.user, activities=['ウレタン棒'], body=f'記録{i}')
        TherapyRecord.objects.create(facility=self.f, beneficiary=self.kid, date=datetime.date(2026, 10, 13), body='同じ日の2件目')

    def test_five_days_shown(self):
        res = self.client.get(reverse('beneficiaries:detail', args=[self.kid.pk]))
        self.assertContains(res, '療育記録（直近5日分）')
        self.assertContains(res, '全部で 8 件')
        for body in ('記録6', '記録5', '記録4', '記録3', '記録2', '同じ日の2件目'):
            self.assertContains(res, body)
        self.assertNotContains(res, '記録1')
        self.assertNotContains(res, '記録0')
        self.assertContains(res, '①ウレタン棒')
        self.assertContains(res, '担当 大坂')
        self.assertContains(res, f'#rec{self.kid.therapy_records.first().pk}')

    def test_hidden_for_other_layouts(self):
        from facilities.models import Facility
        self.f.layout = Facility.LAYOUT_STANDARD
        self.f.save(update_fields=['layout'])
        self.assertNotContains(self.client.get(reverse('beneficiaries:detail', args=[self.kid.pk])), '療育記録（直近5日分）')


class BeneficiaryImportTests(TestCase):
    """利用者情報の Excel・CSV 取り込み（ゆあーず）"""

    def setUp(self):
        from accounts.models import StaffAccount
        from facilities.models import Facility
        self.f = Facility.objects.create(name='発達支援ルーム　ゆあーず', layout=Facility.LAYOUT_RYOIKU)
        self.user = StaffAccount.objects.create_user('ryo', password='pw12345678', facility=self.f, role=StaffAccount.ROLE_ADMIN)
        self.client.login(username='ryo', password='pw12345678')
        self.url = reverse('beneficiaries:import')

    @staticmethod
    def xlsx(rows):
        import io
        from django.core.files.uploadedfile import SimpleUploadedFile
        from openpyxl import Workbook
        wb = Workbook(); ws = wb.active
        for r in rows:
            ws.append(r)
        buf = io.BytesIO(); wb.save(buf)
        return SimpleUploadedFile('名簿.xlsx', buf.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    @staticmethod
    def csv_file(text, enc='utf-8-sig'):
        from django.core.files.uploadedfile import SimpleUploadedFile
        return SimpleUploadedFile('名簿.csv', text.encode(enc), content_type='text/csv')

    def test_template(self):
        from openpyxl import load_workbook
        import io
        res = self.client.get(reverse('beneficiaries:import_template'))
        self.assertEqual(res.status_code, 200)
        wb = load_workbook(io.BytesIO(res.content))
        self.assertEqual(wb.sheetnames, ['利用者', '書き方'])
        header = [c.value for c in wb['利用者'][1]]
        self.assertEqual(header[:5], ['姓', '名', 'せい（ふりがな）', 'めい（ふりがな）', '生年月日'])
        self.assertIn('受給者証番号', header)

    def test_parse_row_and_errors(self):
        import datetime
        from . import importer
        data = importer.parse_row({'last_name': '山田', 'first_name': '太郎', 'date_of_birth': '2019年4月1日', 'gender': '女', 'grade': '小1',
                                   'weekdays': '月・水,金', 'is_severe': '○', 'postal_code': '600-8216', 'disability_class': '2級',
                                   'guardian_last_name': '山田', 'guardian_first_name': '花子', 'guardian_relation': '母',
                                   'certificate_number': '2600001234', 'granted_days': '１０', 'monthly_cap': '4,600円', 'valid_until': '2027/3/31'})
        self.assertEqual((data['date_of_birth'], data['gender'], data['grade'], data['is_severe'], data['postal_code'], data['disability_class']),
                         (datetime.date(2019, 4, 1), 'female', 'e1', True, '6008216', '2'))
        self.assertEqual(data['weekdays'], {'weekday_mon', 'weekday_wed', 'weekday_fri'})
        self.assertEqual(data['guardian']['relation'], 'mother')
        self.assertEqual((data['certificate']['granted_days'], data['certificate']['monthly_cap'], data['certificate']['valid_until']),
                         (10, 4600, datetime.date(2027, 3, 31)))
        with self.assertRaisesMessage(importer.RowError, '生年月日が空です'):
            importer.parse_row({'last_name': '山田', 'first_name': '太郎', 'date_of_birth': ''})
        with self.assertRaisesMessage(importer.RowError, '日付を読み取れません'):
            importer.parse_row({'last_name': '山田', 'first_name': '太郎', 'date_of_birth': '平成31年'})
        with self.assertRaisesMessage(importer.RowError, '性別の書き方が違います'):
            importer.parse_row({'last_name': '山田', 'first_name': '太郎', 'date_of_birth': '2019-04-01', 'gender': '男子'})
        with self.assertRaisesMessage(importer.RowError, '有効期間（終了）'):
            importer.parse_row({'last_name': '山田', 'first_name': '太郎', 'date_of_birth': '2019-04-01', 'certificate_number': '1'})
        with self.assertRaisesMessage(importer.RowError, '利用予定曜日の書き方'):
            importer.parse_row({'last_name': '山田', 'first_name': '太郎', 'date_of_birth': '2019-04-01', 'weekdays': '月・日'})

    def test_preview_then_commit_create_and_update(self):
        import datetime
        existing = Beneficiary.objects.create(facility=self.f, last_name='鈴木', first_name='一郎', date_of_birth=datetime.date(2018, 5, 5), address='前の住所')
        rows = [['姓', '名', '生年月日', '性別', '住所', '利用予定曜日', '保護者 姓', '保護者 名', '保護者 続柄', '受給者証番号', '受給者証 有効期間（終了）', '支給量（日/月）'],
                ['山田', '太郎', '2019-04-01', '男', '京都市', '月・水', '山田', '花子', '母', '2600001234', '2027-03-31', '10'],
                ['鈴木', '一郎', '2018/5/5', '', '', '火', '', '', '', '', '', ''],
                ['', '名無し', '2019-04-01', '', '', '', '', '', '', '', '', ''],
                ['佐藤', '花', 'いつか', '', '', '', '', '', '', '', '', '']]
        res = self.client.post(self.url, {'file': self.xlsx(rows)})
        self.assertContains(res, '2. 確かめる（まだ登録していません）')
        self.assertContains(res, '新規 1')
        self.assertContains(res, '書き換え 1')
        self.assertContains(res, '読めない行 2')
        self.assertContains(res, '新しく登録します（保護者・受給者証 も）')
        self.assertContains(res, '同じ姓・名・生年月日の利用者がいるので書き換えます')
        self.assertContains(res, '姓が空です')
        self.assertContains(res, '日付を読み取れません')
        self.assertEqual(Beneficiary.objects.filter(facility=self.f).count(), 1)     # まだ登録していない
        res = self.client.post(self.url, {'action': 'commit'}, follow=True)
        self.assertContains(res, '新規 1 名・書き換え 1 名（読めなかった行 2 件は登録していません）')
        self.assertEqual(Beneficiary.objects.filter(facility=self.f).count(), 2)
        taro = Beneficiary.objects.get(facility=self.f, last_name='山田')
        self.assertEqual((taro.gender, taro.address, taro.weekday_mon, taro.weekday_wed, taro.weekday_tue, taro.has_prior_records),
                         ('male', '京都市', True, True, False, True))
        g = taro.guardians.get()
        self.assertEqual((g.first_name, g.relation, g.is_primary), ('花子', 'mother', True))
        c = taro.recipient_certificates.get()
        self.assertEqual((c.certificate_number, c.granted_days, c.valid_until), ('2600001234', 10, datetime.date(2027, 3, 31)))
        existing.refresh_from_db()
        self.assertEqual((existing.address, existing.weekday_tue, existing.weekday_mon), ('前の住所', True, False))   # 空の欄は変えない
        # もう一度同じファイル → 全部「書き換え」、件数は増えない。受給者証も増えない
        res = self.client.post(self.url, {'file': self.xlsx(rows)})
        self.assertContains(res, '新規 0')
        self.assertContains(res, '書き換え 2')
        self.client.post(self.url, {'action': 'commit'})
        self.assertEqual(Beneficiary.objects.filter(facility=self.f).count(), 2)
        self.assertEqual(taro.recipient_certificates.count(), 1)
        self.assertEqual(taro.guardians.count(), 1)
        # 内容が無いまま登録
        res = self.client.post(self.url, {'action': 'commit'}, follow=True)
        self.assertContains(res, '取り込む内容がありません')

    def test_csv_shift_jis_and_bad_files(self):
        res = self.client.post(self.url, {'file': self.csv_file('姓,名,生年月日,学年\n高橋,結,2017-04-01,小3\n', enc='cp932')})
        self.assertContains(res, '新規 1')
        self.client.post(self.url, {'action': 'commit'})
        self.assertEqual(Beneficiary.objects.get(facility=self.f, last_name='高橋').grade, 'e3')
        res = self.client.post(self.url, {'file': self.csv_file('氏名,誕生日\n高橋 結,2017\n')})
        self.assertContains(res, '「姓」「名」「生年月日」が見つかりません')
        res = self.client.post(self.url, {})
        self.assertContains(res, 'ファイルを選んでください')
        res = self.client.post(self.url, {'file': self.csv_file('姓,名,生年月日\n')})
        self.assertContains(res, 'データの行がありません')

    def test_only_for_ryoiku_and_own_facility(self):
        import datetime
        from facilities.models import Facility
        g = Facility.objects.create(name='ほか', layout=Facility.LAYOUT_RYOIKU)
        Beneficiary.objects.create(facility=g, last_name='山田', first_name='太郎', date_of_birth=datetime.date(2019, 4, 1), address='よそ')
        self.client.post(self.url, {'file': self.csv_file('姓,名,生年月日\n山田,太郎,2019-04-01\n')})
        res = self.client.post(self.url, {'action': 'commit'}, follow=True)
        self.assertContains(res, '新規 1 名')                       # 別の事業所の同名は書き換えない
        self.assertEqual(Beneficiary.objects.filter(facility=g, address='よそ').count(), 1)
        self.assertContains(self.client.get(reverse('beneficiaries:list')), 'Excel・CSV から取り込む')
        self.f.layout = Facility.LAYOUT_STANDARD
        self.f.save(update_fields=['layout'])
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.get(reverse('beneficiaries:import_template')).status_code, 404)
        self.assertNotContains(self.client.get(reverse('beneficiaries:list')), 'Excel・CSV から取り込む')


class GuardianListImportTests(TestCase):
    """前のシステムの「保護者一覧」（1行が保護者1人・児童は名前で照合）の取り込み"""

    def setUp(self):
        import datetime
        from accounts.models import StaffAccount
        from facilities.models import Facility
        self.f = Facility.objects.create(name='発達支援ルーム　ゆあーず', layout=Facility.LAYOUT_RYOIKU)
        self.user = StaffAccount.objects.create_user('ryo', password='pw12345678', facility=self.f, role=StaffAccount.ROLE_ADMIN)
        self.client.login(username='ryo', password='pw12345678')
        self.url = reverse('beneficiaries:import')
        self.taro = Beneficiary.objects.create(facility=self.f, last_name='山田', first_name='太郎', date_of_birth=datetime.date(2019, 4, 1))
        self.hana = Beneficiary.objects.create(facility=self.f, last_name='山田', first_name='花', date_of_birth=datetime.date(2021, 4, 1), address='もとの住所')

    @staticmethod
    def xlsx_from_format(rows):
        """依頼者から届いた書式（見出しだけ）に行を足す"""
        import io, os
        from django.core.files.uploadedfile import SimpleUploadedFile
        from openpyxl import load_workbook
        path = os.path.join(os.path.dirname(__file__), 'testdata', 'guardian_list_format.xlsx')
        wb = load_workbook(path); ws = wb.active
        for r in rows:
            ws.append(r)
        buf = io.BytesIO(); wb.save(buf)
        return SimpleUploadedFile('保護者一覧.xlsx', buf.getvalue())

    def test_format_headers_and_parse(self):
        from . import importer
        rows = importer.rows_from_file(self.xlsx_from_format([
            ['山田 花子', 'ヤマダ ハナコ', '母', '山田 太郎、山田 花', '600-8216', '京都府', '京都市下京区', '○○町1-1', 'コーポ101', '送迎は母',
             '母携帯', '090-1111-1111', '', 'hanako@example.com', '父携帯', '090-2222-2222', '075-000-0000', '',
             '口座振替', '保護者', '山田 一郎', '600-0000', '京都府', '京都市', '△△町2-2', '', '075-111-1111', '', '請求は父へ'],
        ]))
        self.assertEqual(importer.format_of(rows), 'guardian')
        r = rows[0]
        self.assertEqual((r['g_name'], r['relation'], r['children'], r['c1_label'], r['c2_phone2'], r['bill_name'], r['bill_note']),
                         ('山田 花子', '母', '山田 太郎、山田 花', '母携帯', '075-000-0000', '山田 一郎', '請求は父へ'))
        data = importer.parse_guardian_row(r)
        g = data['guardian']
        self.assertEqual((g['last_name'], g['first_name'], g['kana'], g['relation'], g['phone'], g['phone2'], g['email'], g['memo']),
                         ('山田', '花子', 'ヤマダ ハナコ', 'mother', '090-1111-1111', '090-2222-2222', 'hanako@example.com', '送迎は母'))
        self.assertEqual(data['children'], ['山田 太郎', '山田 花'])
        self.assertEqual((data['postal_code'], data['address']), ('6008216', '京都府京都市下京区○○町1-1コーポ101'))
        self.assertEqual(g['extra']['連絡先1 名称'], '母携帯')
        self.assertEqual(g['extra']['連絡先2 電話番号2'], '075-000-0000')
        self.assertEqual(g['extra']['請求先 住所'], '京都府京都市△△町2-2')
        self.assertEqual(g['extra']['支払い方法'], '口座振替')
        self.assertNotIn('連絡先1 その他', g['extra'])        # メールは email に入れたので重複させない
        with self.assertRaisesMessage(importer.RowError, '児童が空です'):
            importer.parse_guardian_row({'g_name': '山田 花子', 'children': ''})

    def test_preview_commit_and_missing_child(self):
        f = self.xlsx_from_format([
            ['山田 花子', 'ヤマダ ハナコ', '母', '山田 太郎、山田 花', '600-8216', '京都府', '京都市下京区', '○○町1-1', '', '',
             '母携帯', '090-1111-1111', '', 'hanako@example.com', '', '', '', '', '', '', '', '', '', '', '', '', '', '', ''],
            ['鈴木 一郎', '', '祖父', '鈴木 次郎', '', '', '', '', '', '', '', '090-3333-3333', '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', ''],
        ])
        res = self.client.post(self.url, {'file': f})
        self.assertContains(res, '保護者一覧</b>の形')
        self.assertContains(res, '台帳の児童に付ける 1')
        self.assertContains(res, '読めない行 1')
        self.assertContains(res, '台帳にいない児童：鈴木 次郎')
        self.assertContains(res, '保護者を 山田 太郎・山田 花 さんに付けます')
        res = self.client.post(self.url, {'action': 'commit'}, follow=True)
        self.assertContains(res, '保護者 2 件（新しく作った児童 0 名）（読めなかった行 1 件は登録していません）')
        g = self.taro.guardians.get()
        self.assertEqual((g.full_name, g.kana, g.relation, g.phone, g.email, g.is_primary, g.extra['連絡先1 名称']),
                         ('山田 花子', 'ヤマダ ハナコ', 'mother', '090-1111-1111', 'hanako@example.com', True, '母携帯'))
        self.assertEqual(self.hana.guardians.get().full_name, '山田 花子')
        self.taro.refresh_from_db(); self.hana.refresh_from_db()
        self.assertEqual((self.taro.postal_code, self.taro.address), ('6008216', '京都府京都市下京区○○町1-1'))
        self.assertEqual(self.hana.address, 'もとの住所')                          # 入っている住所は変えない
        self.assertFalse(Beneficiary.objects.filter(facility=self.f, last_name='鈴木').exists())
        # 「仮の生年月日で作る」を付けると児童も作る。同じ保護者をもう一度入れても増えない
        f = self.xlsx_from_format([
            ['山田 花子', 'ヤマダ ハナコ', '母', '山田 太郎', '', '', '', '', '', '', '', '090-9999-9999', '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', ''],
            ['鈴木 一郎', '', '祖父', '鈴木 次郎', '', '', '', '', '', '', '', '090-3333-3333', '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', ''],
        ])
        res = self.client.post(self.url, {'file': f, 'create_children': '1'})
        self.assertContains(res, '児童も新しく作る 1')
        self.assertContains(res, '仮の生年月日 2000-01-01 で新しく作ります')
        res = self.client.post(self.url, {'action': 'commit', 'create_children': '1'}, follow=True)
        self.assertContains(res, '保護者 2 件（新しく作った児童 1 名）')
        jiro = Beneficiary.objects.get(facility=self.f, last_name='鈴木', first_name='次郎')
        self.assertEqual(jiro.date_of_birth.isoformat(), '2000-01-01')
        self.assertIn('仮の値', jiro.notes)
        g2 = jiro.guardians.get()
        self.assertEqual((g2.full_name, g2.relation, g2.extra.get('続柄（原文）'), g2.phone), ('鈴木 一郎', 'other', '祖父', '090-3333-3333'))
        self.assertEqual(self.taro.guardians.count(), 1)
        self.assertEqual(self.taro.guardians.get().phone, '090-9999-9999')            # 書き換え
        # 詳細に「取り込んだ情報」
        res = self.client.get(reverse('beneficiaries:detail', args=[self.taro.pk]))
        self.assertContains(res, '取り込んだ情報')
        self.assertContains(res, '母携帯')
        self.assertContains(res, 'ヤマダ ハナコ')


class CsvVariantsImportTests(TestCase):
    """CSV の読み方：Shift_JIS・UTF-16（Excel の Unicode テキスト）・タブ区切り・表題行つき・拡張子違い"""

    def setUp(self):
        from accounts.models import StaffAccount
        from facilities.models import Facility
        self.f = Facility.objects.create(name='発達支援ルーム　ゆあーず', layout=Facility.LAYOUT_RYOIKU)
        self.user = StaffAccount.objects.create_user('ryo', password='pw12345678', facility=self.f, role=StaffAccount.ROLE_ADMIN)
        self.client.login(username='ryo', password='pw12345678')
        self.url = reverse('beneficiaries:import')

    @staticmethod
    def up(name, data):
        from django.core.files.uploadedfile import SimpleUploadedFile
        return SimpleUploadedFile(name, data, content_type='application/octet-stream')

    def test_read_rows_variants(self):
        from config.tabular import read_rows, guess_delimiter
        body = '姓,名,生年月日\n高橋,結,2017-04-01\n'
        self.assertEqual(read_rows(body.encode('cp932'), '名簿.csv')[1], ['高橋', '結', '2017-04-01'])
        self.assertEqual(read_rows(('﻿' + body).encode('utf-8'), '名簿.csv')[0], ['姓', '名', '生年月日'])
        tab = '姓\t名\t生年月日\n高橋\t結\t2017/4/1\n'
        self.assertEqual(read_rows(tab.encode('utf-16'), '名簿.txt')[1], ['高橋', '結', '2017/4/1'])       # Excel の Unicode テキスト
        self.assertEqual(read_rows(tab.encode('utf-16-le'), '名簿.tsv')[1], ['高橋', '結', '2017/4/1'])    # BOM 無しの UTF-16
        self.assertEqual(read_rows('姓;名;生年月日\n高橋;結;2017-04-01\n'.encode('utf-8'), 'x.csv')[1][1], '結')
        self.assertEqual(guess_delimiter('a,b\tc\td'), '\t')
        # .csv という名前の xlsx も中身で見分ける
        import io
        from openpyxl import Workbook
        wb = Workbook(); wb.active.append(['姓', '名', '生年月日']); wb.active.append(['高橋', '結', '2017-04-01'])
        buf = io.BytesIO(); wb.save(buf)
        self.assertEqual(read_rows(buf.getvalue(), '名簿.csv')[1], ['高橋', '結', '2017-04-01'])
        with self.assertRaisesMessage(ValueError, '古い Excel 形式'):
            read_rows(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1' + b'0' * 16, '名簿.xls')
        with self.assertRaisesMessage(ValueError, 'PDF は読めません'):
            read_rows(b'%PDF-1.4 ...', '名簿.pdf')

    def test_title_row_above_header_and_guardian_csv(self):
        from . import importer
        # 表題行と空行のあとに見出し
        rows = importer.rows_from_file(self.up('名簿.csv', '利用者名簿（2026年度）\n\n姓,名,生年月日,学年\n高橋,結,2017-04-01,小3\n'.encode('cp932')))
        self.assertEqual(rows, [{'last_name': '高橋', 'first_name': '結', 'date_of_birth': '2017-04-01', 'grade': '小3'}])
        # 保護者一覧を Shift_JIS の CSV（前のシステムの書き出し）で
        header = ','.join(importer.GUARDIAN_HEADERS)
        line = ','.join(['山田 花子', 'ヤマダ ハナコ', '母', '山田 太郎', '', '京都府', '京都市', '1-1', '', '', '母携帯', '090-1111-1111'] + [''] * 17)
        rows = importer.rows_from_file(self.up('保護者一覧.csv', f'保護者一覧\n{header}\n{line}\n'.encode('cp932')))
        self.assertEqual(importer.format_of(rows), 'guardian')
        self.assertEqual((rows[0]['g_name'], rows[0]['children'], rows[0]['c1_label']), ('山田 花子', '山田 太郎', '母携帯'))
        # 画面からタブ区切り（UTF-16）で
        import datetime
        Beneficiary.objects.create(facility=self.f, last_name='山田', first_name='太郎', date_of_birth=datetime.date(2019, 4, 1))
        tsv = '\t'.join(importer.GUARDIAN_HEADERS) + '\n' + line.replace(',', '\t') + '\n'
        res = self.client.post(self.url, {'file': self.up('保護者一覧.txt', tsv.encode('utf-16'))})
        self.assertContains(res, '台帳の児童に付ける 1')


class ChildrenListImportTests(TestCase):
    """前のシステムの「児童一覧」（見出しなし・15列・1行が契約1件）の取り込み"""

    ROWS = [
        ['山田 花子', 'ヤマダ ハナコ', '000010', '山田 太郎', 'ヤマダ タロウ', '男', '2018/4/2', '', '退所', '2023/4/1', '2025/3/31', '利用中', '2025/4/1', '', '利用なし'],
        ['山田 花子', 'ヤマダ ハナコ', '000011', '山田 太郎', 'ヤマダ タロウ', '男', '2018/4/2', '', '利用なし', '', '', '退所', '2021/1/1', '2022/3/31', '利用なし'],
        ['山田 花子', 'ヤマダ ハナコ', '000012', '山田 花', 'ヤマダ ハナ', '女', '2020/5/5', '', '退所', '2024/4/1', '2025/3/31', '利用なし', '', '', '利用なし'],
        ['佐藤 花子', 'サトウ ハナコ', '000013', '佐藤 花', 'サトウ ハナ', '女', '2020/5/5', '', '利用中', '2025/4/1', '', '利用なし', '', '', '利用なし'],
    ]

    def setUp(self):
        from accounts.models import StaffAccount
        from facilities.models import Facility
        self.f = Facility.objects.create(name='発達支援ルーム　ゆあーず', layout=Facility.LAYOUT_RYOIKU)
        self.user = StaffAccount.objects.create_user('ryo', password='pw12345678', facility=self.f, role=StaffAccount.ROLE_ADMIN)
        self.client.login(username='ryo', password='pw12345678')
        self.url = reverse('beneficiaries:import')

    @staticmethod
    def csv(rows, enc='cp932', name='児童一覧.csv'):
        from django.core.files.uploadedfile import SimpleUploadedFile
        text = '\r\n'.join(','.join(r) for r in rows) + '\r\n'
        return SimpleUploadedFile(name, text.encode(enc))

    def test_rows_are_grouped_per_child(self):
        from . import importer
        rows = importer.rows_from_file(self.csv(self.ROWS))
        self.assertEqual(importer.format_of(rows), importer.FORMAT_CHILDREN)
        self.assertEqual([(r['last_name'], r['first_name']) for r in rows], [('山田', '太郎'), ('山田', '花'), ('佐藤', '花')])
        taro, hana, sato = rows
        self.assertEqual((taro['status'], taro['admission_date'], taro['discharge_date']), ('在籍中', '2021/1/1', ''))
        self.assertEqual((taro['last_name_kana'], taro['first_name_kana'], taro['guardian_kana']), ('やまだ', 'たろう', 'やまだ はなこ'))
        self.assertEqual((taro['guardian_last_name'], taro['guardian_first_name'], taro['gender']), ('山田', '花子', '男'))
        self.assertIn('利用者番号 000010 児童発達支援 退所 2023/4/1〜2025/3/31', taro['notes_append'])
        self.assertIn('利用者番号 000010 放課後等デイ 利用中 2025/4/1〜', taro['notes_append'])
        self.assertIn('利用者番号 000011 放課後等デイ 退所 2021/1/1〜2022/3/31', taro['notes_append'])
        self.assertEqual((hana['status'], hana['admission_date'], hana['discharge_date']), ('退所', '2024/4/1', '2025/3/31'))
        self.assertIn('姓だけ違う', hana['_detail'])
        self.assertIn('佐藤 花', hana['_detail'])
        self.assertIn('山田 花', sato['_detail'])

    def test_header_row_and_utf8_are_accepted(self):
        from . import importer
        header = ['保護者', 'カナ', '番号', '児童', 'カナ', '性別', '生年月日', '', '児発', '開始', '終了', '放デイ', '開始', '終了', '他']
        rows = importer.rows_from_file(self.csv([header] + self.ROWS, enc='utf-8', name='list.csv'))
        self.assertEqual(importer.format_of(rows), importer.FORMAT_CHILDREN)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]['_line'], 2)

    def test_preview_and_commit_twice(self):
        import datetime
        res = self.client.post(self.url, {'file': self.csv(self.ROWS)})
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, '児童一覧')
        self.assertContains(res, '放課後等デイ 利用中（2025/4/1〜）')
        self.assertContains(res, '姓だけ違う')
        res = self.client.post(self.url, {'action': 'commit'}, follow=True)
        self.assertContains(res, '新規 3 名')
        taro = Beneficiary.objects.get(facility=self.f, last_name='山田', first_name='太郎')
        self.assertEqual((taro.status, taro.admission_date, taro.discharge_date, taro.gender),
                         ('active', datetime.date(2021, 1, 1), None, 'male'))
        self.assertEqual((taro.last_name_kana, taro.first_name_kana), ('やまだ', 'たろう'))
        self.assertEqual(taro.notes.count('利用者番号 000010'), 2)
        self.assertTrue(taro.has_prior_records)
        g = taro.guardians.get()
        self.assertEqual((g.last_name, g.first_name, g.kana, g.relation, g.is_primary), ('山田', '花子', 'やまだ はなこ', 'other', True))
        hana = Beneficiary.objects.get(facility=self.f, last_name='山田', first_name='花')
        self.assertEqual((hana.status, hana.discharge_date), ('inactive', datetime.date(2025, 3, 31)))
        # もう一度読ませても増えない。備考も二重にならない
        self.client.post(self.url, {'file': self.csv(self.ROWS)})
        res = self.client.post(self.url, {'action': 'commit'}, follow=True)
        self.assertContains(res, '書き換え 3 名')
        self.assertEqual(Beneficiary.objects.filter(facility=self.f).count(), 3)
        taro.refresh_from_db()
        self.assertEqual(taro.notes.count('利用者番号 000010'), 2)
        self.assertEqual(taro.guardians.count(), 1)

    def test_standard_format_status_and_discharge(self):
        import datetime
        from . import importer
        header = ','.join(['姓', '名', '生年月日', '在籍状況', '退所日', '保護者 姓', '保護者 名', '保護者 ふりがな'])
        f = self.csv([header.split(','), ['山田', '太郎', '2018-04-02', '退所', '2025-03-31', '山田', '花子', 'やまだ はなこ']], enc='utf-8', name='a.csv')
        rows = importer.rows_from_file(f)
        self.assertEqual(importer.format_of(rows), importer.FORMAT_BENEFICIARY)
        importer.apply(self.f, importer.plan(self.f, rows))
        taro = Beneficiary.objects.get(facility=self.f, last_name='山田', first_name='太郎')
        self.assertEqual((taro.status, taro.discharge_date), ('inactive', datetime.date(2025, 3, 31)))
        self.assertEqual(taro.guardians.get().kana, 'やまだ はなこ')
        f = self.csv([['姓', '名', '生年月日', '在籍状況'], ['山田', '太郎', '2018-04-02', '在籍中']], enc='utf-8', name='b.csv')
        importer.apply(self.f, importer.plan(self.f, importer.rows_from_file(f)))
        taro.refresh_from_db()
        self.assertEqual((taro.status, taro.discharge_date), ('active', None))
        f = self.csv([['姓', '名', '生年月日', '在籍状況'], ['山田', '太郎', '2018-04-02', '通所中']], enc='utf-8', name='c.csv')
        planned = importer.plan(self.f, importer.rows_from_file(f))
        self.assertEqual(planned[0]['action'], 'error')
        self.assertIn('在籍状況', planned[0]['detail'])


class KnowledgeTests(TestCase):
    """診断書などを AI で読み取り → 確認して保存・台帳に反映 → 療育記録の AI が参考にする（RAG）"""

    READ = {
        'kind': '診断書', 'title': '診断書', 'doc_date': '2026-04-10', 'issuer': 'こども発達クリニック',
        'name_on_doc': '青木 子', 'birth_on_doc': '2019-04-01', 'diagnosis': '自閉スペクトラム症', 'severe': 'no',
        'summary': '自閉スペクトラム症と診断。聴覚過敏があり、大きな音で混乱しやすい。',
        'support_points': ['大きな音の出る活動では、イヤーマフを使う', '予定の変更は絵カードで前もって伝える'],
        'medical_notes': ['てんかんの薬（朝夕）を服用している'],
        'full_text': '診断名：自閉スペクトラム症\n所見：聴覚過敏が強く、太鼓や掃除機の音で耳をふさぐ。トランポリンなど揺れる遊びは好む。\n'
                     '保険証番号：（書かない）',
        'unreadable': '',
    }

    def setUp(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from accounts.models import StaffAccount
        from facilities.models import Facility
        from .models import BeneficiaryDocument
        self.f = Facility.objects.create(name='発達支援ルーム　ゆあーず', layout=Facility.LAYOUT_RYOIKU, use_therapy_record=True)
        StaffAccount.objects.create_user('ryo', password='pw12345678', facility=self.f, role=StaffAccount.ROLE_ADMIN)
        self.client.login(username='ryo', password='pw12345678')
        self.kid = Beneficiary.objects.create(facility=self.f, last_name='青木', first_name='子', last_name_kana='あおき',
                                              first_name_kana='こ', date_of_birth=datetime.date(2019, 4, 1))
        self.doc = BeneficiaryDocument.objects.create(beneficiary=self.kid, file_name='診断書.pdf',
                                                      file=SimpleUploadedFile('shindan.pdf', b'%PDF-1.4 test'))

    def _mock_response(self, data, stop='end_turn'):
        from types import SimpleNamespace
        return SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type='text', text=json.dumps(data, ensure_ascii=False))])

    def _read(self, client_cls, data=None):
        client_cls.return_value.messages.create.return_value = self._mock_response(data or self.READ)
        return self.client.post(reverse('beneficiaries:knowledge_read', args=[self.kid.pk]),
                                {'source': 'document', 'source_pk': self.doc.pk})

    @mock.patch('beneficiaries.knowledge.anthropic.Anthropic')
    @override_settings(ANTHROPIC_API_KEY='test-key')
    def test_read_review_save_and_apply(self, client_cls):
        from therapy.models import TherapyProfile
        from .models import BeneficiaryKnowledge
        TherapyProfile.objects.create(beneficiary=self.kid, cautions='・予定の変更は絵カードで前もって伝える')
        res = self._read(client_cls)
        k = BeneficiaryKnowledge.objects.get()
        self.assertRedirects(res, reverse('beneficiaries:knowledge_review', args=[self.kid.pk, k.pk]))
        kwargs = client_cls.return_value.messages.create.call_args.kwargs
        block = kwargs['messages'][0]['content'][0]
        self.assertEqual((block['type'], block['source']['media_type']), ('document', 'application/pdf'))
        self.assertEqual(kwargs['output_config']['format']['type'], 'json_schema')
        self.assertIn('青木 子さん', kwargs['messages'][0]['content'][1]['text'])
        # 確認待ち：台帳はまだ変わらない。反映案（留意点はすでにある項目を除く）
        self.assertEqual(k.status, 'draft')
        self.assertEqual(k.proposals['disability_type'], {'current': '', 'new': '自閉スペクトラム症', 'checked': True})
        self.assertNotIn('is_severe', k.proposals)
        self.assertIn('てんかんの薬', k.proposals['notes_add']['text'])
        self.assertIn('イヤーマフ', k.proposals['cautions_add']['text'])
        self.assertNotIn('絵カード', k.proposals['cautions_add']['text'])
        self.assertEqual(k.proposals['warnings'], [])
        self.kid.refresh_from_db()
        self.assertEqual(self.kid.disability_type, '')
        page = self.client.get(reverse('beneficiaries:knowledge_review', args=[self.kid.pk, k.pk]))
        self.assertContains(page, '確認待ち')
        self.assertContains(page, '障害種別を書き換える')
        self.assertContains(page, '療育記録の「留意点」に足す')
        detail = self.client.get(reverse('beneficiaries:detail', args=[self.kid.pk]))
        self.assertContains(detail, '確認待ち')
        # 保存：印を付けたもの（障害種別・留意点）だけ反映。備考は印なし
        res = self.client.post(reverse('beneficiaries:knowledge_review', args=[self.kid.pk, k.pk]), {
            'action': 'save', 'kind': '診断書', 'title': '診断書', 'doc_date': '2026-04-10', 'issuer': 'こども発達クリニック',
            'summary': k.summary, 'points': k.points, 'text': k.text, 'use_in_ai': '1',
            'apply_disability_type': '1', 'disability_type': '自閉スペクトラム症',
            'notes_add': k.proposals['notes_add']['text'],
            'apply_cautions': '1', 'cautions_add': k.proposals['cautions_add']['text'],
        }, follow=True)
        self.assertContains(res, '台帳に反映：障害種別・留意点')
        self.kid.refresh_from_db()
        self.assertEqual((self.kid.disability_type, self.kid.notes), ('自閉スペクトラム症', ''))
        self.assertIn('イヤーマフ', TherapyProfile.objects.get(beneficiary=self.kid).cautions)
        k.refresh_from_db()
        self.assertEqual((k.status, k.applied, k.use_in_ai), ('saved', ['障害種別', '留意点'], True))
        self.assertGreaterEqual(k.chunks.count(), 3)
        detail = self.client.get(reverse('beneficiaries:detail', args=[self.kid.pk]))
        self.assertContains(detail, '書類から分かっていること')
        self.assertContains(detail, '読み取り済み')
        self.assertContains(detail, 'AIの参考')
        # 同じ書類を読み直して保存すると入れ替わる
        self._read(client_cls)
        k2 = BeneficiaryKnowledge.objects.get(status='draft')
        self.client.post(reverse('beneficiaries:knowledge_review', args=[self.kid.pk, k2.pk]),
                         {'action': 'save', 'summary': 'x', 'points': '', 'text': '', 'use_in_ai': '1'})
        self.assertEqual(list(BeneficiaryKnowledge.objects.values_list('pk', flat=True)), [k2.pk])

    @mock.patch('beneficiaries.knowledge.anthropic.Anthropic')
    @override_settings(ANTHROPIC_API_KEY='test-key')
    def test_warnings_discard_and_errors(self, client_cls):
        from .models import BeneficiaryKnowledge
        self._read(client_cls, dict(self.READ, name_on_doc='山田 花子', birth_on_doc='2018-01-01', severe='yes'))
        k = BeneficiaryKnowledge.objects.get()
        self.assertEqual(len(k.proposals['warnings']), 2)
        self.assertEqual(k.proposals['is_severe'], {'checked': True})
        page = self.client.get(reverse('beneficiaries:knowledge_review', args=[self.kid.pk, k.pk]))
        self.assertContains(page, '別のお子さまの書類でないか')
        res = self.client.post(reverse('beneficiaries:knowledge_review', args=[self.kid.pk, k.pk]), {'action': 'discard'}, follow=True)
        self.assertContains(res, '読み取り結果を消しました')
        self.assertFalse(BeneficiaryKnowledge.objects.exists())
        # AI が断った・長すぎた
        client_cls.return_value.messages.create.return_value = self._mock_response({}, stop='refusal')
        res = self.client.post(reverse('beneficiaries:knowledge_read', args=[self.kid.pk]),
                               {'source': 'document', 'source_pk': self.doc.pk}, follow=True)
        self.assertContains(res, 'AI がこの書類を読み取れませんでした')
        client_cls.return_value.messages.create.return_value = self._mock_response({}, stop='max_tokens')
        res = self.client.post(reverse('beneficiaries:knowledge_read', args=[self.kid.pk]),
                               {'source': 'document', 'source_pk': self.doc.pk}, follow=True)
        self.assertContains(res, 'ページを分けて')
        self.assertFalse(BeneficiaryKnowledge.objects.exists())

    def test_readable_types_and_other_facility(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from accounts.models import StaffAccount
        from facilities.models import Facility
        from . import knowledge
        from .models import BeneficiaryDocument
        self.assertTrue(knowledge.is_readable('a.PDF'))
        self.assertTrue(knowledge.is_readable('a.jpeg'))
        self.assertFalse(knowledge.is_readable('a.xlsx'))
        heic = BeneficiaryDocument.objects.create(beneficiary=self.kid, file_name='a.heic', file=SimpleUploadedFile('a.heic', b'x'))
        with override_settings(ANTHROPIC_API_KEY='test-key'):
            res = self.client.post(reverse('beneficiaries:knowledge_read', args=[self.kid.pk]),
                                   {'source': 'document', 'source_pk': heic.pk}, follow=True)
        self.assertContains(res, 'HEIC')
        with override_settings(ANTHROPIC_API_KEY=''):
            res = self.client.post(reverse('beneficiaries:knowledge_read', args=[self.kid.pk]),
                                   {'source': 'document', 'source_pk': self.doc.pk}, follow=True)
        self.assertContains(res, 'ANTHROPIC_API_KEY')
        other = Facility.objects.create(name='ほか', layout=Facility.LAYOUT_RYOIKU)
        StaffAccount.objects.create_user('oth', password='pw12345678', facility=other, role=StaffAccount.ROLE_ADMIN)
        self.client.login(username='oth', password='pw12345678')
        res = self.client.post(reverse('beneficiaries:knowledge_read', args=[self.kid.pk]), {'source': 'document', 'source_pk': self.doc.pk})
        self.assertEqual(res.status_code, 404)

    def test_context_for_only_this_child_and_relevant_passages(self):
        from . import knowledge
        from .models import BeneficiaryKnowledge
        other = Beneficiary.objects.create(facility=self.f, last_name='井上', first_name='太郎', date_of_birth=datetime.date(2018, 5, 1))
        k = BeneficiaryKnowledge.objects.create(beneficiary=self.kid, kind='診断書', title='診断書', issuer='こども発達クリニック',
                                                summary='聴覚過敏がある。', points='・大きな音の活動ではイヤーマフを使う',
                                                text=self.READ['full_text'] + '\n' + 'その他の記載。' * 80, status='saved')
        knowledge.reindex(k)
        BeneficiaryKnowledge.objects.create(beneficiary=other, kind='診断書', title='井上の書類', summary='別の子', status='saved')
        BeneficiaryKnowledge.objects.create(beneficiary=self.kid, kind='その他', title='確認待ち', summary='まだ', status='draft')
        text, n = knowledge.context_for(self.kid, '太鼓あそび トランポリン')
        self.assertEqual(n, 1)
        self.assertIn('書類から分かっていること', text)
        self.assertIn('イヤーマフ', text)
        self.assertIn('太鼓や掃除機の音', text)          # 今日の活動に関係しそうな記載
        self.assertNotIn('井上', text)
        self.assertNotIn('確認待ち', text)
        self.assertLessEqual(len(text), knowledge.CONTEXT_MAX + 80)
        k.use_in_ai = False
        k.save()
        self.assertEqual(knowledge.context_for(self.kid, '太鼓'), ('', 0))


@override_settings(ANTHROPIC_API_KEY='test-key')
class KnowledgeInTherapyAiTests(TestCase):
    """療育記録の AI（留意点の要約・記録の文）に、その子の書類から分かっていることが渡る"""

    def setUp(self):
        from accounts.models import StaffAccount
        from facilities.models import Facility
        from . import knowledge
        from .models import BeneficiaryKnowledge
        self.f = Facility.objects.create(name='発達支援ルーム　ゆあーず', layout=Facility.LAYOUT_RYOIKU, use_therapy_record=True)
        StaffAccount.objects.create_user('ryo', password='pw12345678', facility=self.f, role=StaffAccount.ROLE_ADMIN)
        self.client.login(username='ryo', password='pw12345678')
        self.kid = Beneficiary.objects.create(facility=self.f, last_name='青木', first_name='子', date_of_birth=datetime.date(2019, 4, 1))
        k = BeneficiaryKnowledge.objects.create(beneficiary=self.kid, kind='診断書', title='診断書', summary='聴覚過敏がある。',
                                                points='・大きな音の活動ではイヤーマフを使う', text='太鼓の音で耳をふさぐ。', status='saved')
        knowledge.reindex(k)

    def _ai(self, client_cls, text):
        from types import SimpleNamespace
        client_cls.return_value.messages.create.return_value = SimpleNamespace(content=[SimpleNamespace(type='text', text=text)])

    @mock.patch('ai_assist.quick.anthropic.Anthropic')
    def test_record_summary_and_cautions_get_reference(self, client_cls):
        self._ai(client_cls, '太鼓あそびでは聴覚過敏との所見があるため、イヤーマフを使って取り組む。' * 3)
        res = self.client.post(reverse('therapy:record_summary', args=[self.kid.pk]),
                               {'cautions': '・音に敏感', 'activity_1': '太鼓あそび', 'date': '2026-10-02'})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['references'], 1)
        kwargs = client_cls.return_value.messages.create.call_args.kwargs
        self.assertIn('【書類から分かっていること（参考。職員が確かめて登録したもの）】', kwargs['messages'][0]['content'])
        self.assertIn('イヤーマフ', kwargs['messages'][0]['content'])
        self.assertIn('太鼓の音で耳をふさぐ', kwargs['messages'][0]['content'])
        self.assertIn('医学的な判断を加えたりしない', kwargs['system'])
        # 留意点の「短く要約」も参考にする。「詳しくまとめる」は話したことの整理なので使わない
        self._ai(client_cls, '・音に敏感\n・大きな音の活動ではイヤーマフを使う')
        res = self.client.post(reverse('therapy:cautions_summary', args=[self.kid.pk]), {'text': '音に敏感'})
        self.assertEqual(res.json()['references'], 1)
        self.assertIn('イヤーマフ', client_cls.return_value.messages.create.call_args.kwargs['messages'][0]['content'])
        res = self.client.post(reverse('therapy:cautions_summary', args=[self.kid.pk]), {'text': '音に敏感', 'mode': 'detail'})
        self.assertEqual(res.json()['references'], 0)
        self.assertNotIn('書類から分かっていること', client_cls.return_value.messages.create.call_args.kwargs['messages'][0]['content'])
        page = self.client.get(reverse('therapy:child', args=[self.kid.pk]))
        self.assertContains(page, '書類から分かっていること</a>（1 件')


class RyoikuConflictTests(TestCase):
    """アセスメント・資料と、書類から分かっていることの修正の競合"""

    def setUp(self):
        from accounts.models import StaffAccount
        from facilities.models import Facility
        self.f = Facility.objects.create(name='発達支援ルーム　ゆあーず', layout=Facility.LAYOUT_RYOIKU)
        StaffAccount.objects.create_user('ryo', password='pw12345678', facility=self.f, role=StaffAccount.ROLE_ADMIN)
        self.client.login(username='ryo', password='pw12345678')
        self.kid = Beneficiary.objects.create(facility=self.f, last_name='青木', first_name='子', date_of_birth=datetime.date(2019, 4, 1))

    def test_assessment_and_knowledge(self):
        from config.concurrency import version_token
        from .models import BeneficiaryAssessment, BeneficiaryKnowledge
        a = BeneficiaryAssessment.objects.create(beneficiary=self.kid, date=datetime.date(2026, 9, 1), title='面談', content='もと')
        opened = version_token(a)
        detail = self.client.get(reverse('beneficiaries:detail', args=[self.kid.pk]))
        self.assertContains(detail, f'data-concurrency="beneficiary_assessment:{a.pk}" data-concurrency-lazy')
        a.content = '他の職員'
        a.save()
        url = reverse('beneficiaries:assessment_update', args=[self.kid.pk, a.pk])
        res = self.client.post(url, {'date': '2026-09-01', 'title': '面談', 'kind': 'assessment', 'content': 'わたし', 'version': opened}, follow=True)
        self.assertContains(res, '他の職員が')
        a.refresh_from_db()
        self.assertEqual(a.content, '他の職員')
        k = BeneficiaryKnowledge.objects.create(beneficiary=self.kid, kind='診断書', title='診断書', summary='もと', status='saved')
        opened = version_token(k)
        page = self.client.get(reverse('beneficiaries:knowledge_review', args=[self.kid.pk, k.pk]))
        self.assertContains(page, f'data-concurrency="knowledge:{k.pk}"')
        k.summary = '他の職員'
        k.save()
        res = self.client.post(reverse('beneficiaries:knowledge_review', args=[self.kid.pk, k.pk]),
                               {'action': 'save', 'summary': 'わたし', 'points': '', 'text': '', 'use_in_ai': '1', 'version': opened}, follow=True)
        self.assertContains(res, '他の職員が')
        k.refresh_from_db()
        self.assertEqual(k.summary, '他の職員')

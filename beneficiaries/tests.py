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

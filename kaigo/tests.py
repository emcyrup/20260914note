import datetime
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import StaffAccount
from beneficiaries import importer
from beneficiaries.models import Beneficiary, DeletedBeneficiary
from facilities.models import Facility

from .models import Assessment, CareInsurance, KaigoPlan, Meeting, Provider, ScanDocument, ServiceUse, SupportLog

# カイポケの利用者一覧と同じ形（中身は架空）
KAIPOKE_HEADER = ('台帳対象月,表示状態,氏名,氏名カナ,性別,生年月日,年齢,郵便番号,住所,電話番号,携帯番号,緊急連絡先氏名,'
                  '緊急連絡先電話番号,メールアドレス,備考,被保証:保険者番号,被保証:被保険者番号,被保証:要介護度,'
                  '被保証:認定済フラグ,被保証:認定日,被保証:被保険者証交付日,被保証:適用開始日,被保証:適用終了日,'
                  '被保証:認定有効期間開始日,被保証:認定有効期間終了日,被保証:当月被保険者証件数,負担割合証:負担割合,'
                  '負担割合証:交付日,負担割合証:適用開始日')
KAIPOKE_ROWS = [
    '2026-10-01,表示,試験 花子,シケン ハナコ,女,1940-02-03,86,"=""1000001""",架空県架空市1-2-3,"=""0000000001""",,'
    '試験 一郎,"=""09000000001""",,,"=""999999""","=""0000000011""",要介護2,true,2026-04-10,2026-04-15,'
    '2026-04-01,2027-03-31,2026-04-01,2027-03-31,1,1割,2026-07-20,2026-08-01',
    '2026-10-01,非表示,見本 次郎,ミホン ジロウ,男,1935-12-24,90,"=""1000002""",架空県架空町4-5,,"=""08000000002""",'
    '長女,"=""08000000003""",,通院は月1回,"=""999999""","=""0000000022""",要支援1,true,2025-11-01,2025-11-05,'
    '2025-11-01,2026-10-31,2025-11-01,2026-10-31,1,3割,2026-07-20,2026-08-01',
]


def kaipoke_csv(rows=KAIPOKE_ROWS):
    text = '\n'.join([KAIPOKE_HEADER] + rows) + '\n'
    return SimpleUploadedFile('利用者台帳.csv', ('﻿' + text).encode('utf-8'), content_type='text/csv')


class KaigoBase(TestCase):
    def setUp(self):
        from facilities.management.commands.create_facility import apply_kyotaku_preset
        self.f = Facility.objects.create(name='居宅（仮）')
        apply_kyotaku_preset(self.f)
        self.user = StaffAccount.objects.create_user('cm', password='pw12345678', facility=self.f, role=StaffAccount.ROLE_ADMIN)
        self.client.force_login(self.user)
        self.b = Beneficiary.objects.create(facility=self.f, last_name='山本', first_name='和子', last_name_kana='やまもと',
                                            date_of_birth=datetime.date(1940, 5, 12))


class KaipokeImportTests(KaigoBase):
    def test_rows_are_read(self):
        rows = importer.rows_from_file(kaipoke_csv())
        self.assertEqual(len(rows), 2)
        self.assertEqual(importer.format_of(rows), importer.FORMAT_BENEFICIARY)
        d = importer.parse_row(rows[0])
        self.assertEqual((d['last_name'], d['first_name'], d['last_name_kana'], d['first_name_kana']), ('試験', '花子', 'しけん', 'はなこ'))
        self.assertEqual((d['gender'], d['date_of_birth'], d['postal_code']), ('female', datetime.date(1940, 2, 3), '1000001'))
        self.assertEqual((d['home_phone'], d['status']), ('0000000001', 'active'))
        self.assertEqual(d['guardian']['last_name'] + d['guardian']['first_name'], '試験一郎')
        self.assertEqual(d['care']['insurer_number'], '999999')
        self.assertEqual(d['care']['insured_number'], '0000000011')
        self.assertEqual(d['care']['care_level'], '要介護2')
        self.assertEqual(d['care']['valid_until'], datetime.date(2027, 3, 31))
        self.assertEqual(d['care']['copay_ratio'], 1)
        d2 = importer.parse_row(rows[1])
        self.assertEqual(d2['status'], 'inactive')                  # 非表示 → 退所
        self.assertEqual((d2['guardian']['last_name'], d2['guardian']['first_name']), ('長女', ''))   # 空白の無い名前
        self.assertEqual(d2['care']['copay_ratio'], 3)

    def test_import_screen_and_commit(self):
        res = self.client.get(reverse('beneficiaries:list'))
        self.assertContains(res, 'カイポケ CSV から取り込む')
        res = self.client.post(reverse('beneficiaries:import'), {'file': kaipoke_csv()})
        self.assertContains(res, '介護保険証（要介護2）')
        self.assertEqual(res.context['counts'], {'create': 2, 'update': 0, 'error': 0})
        self.client.post(reverse('beneficiaries:import'), {'action': 'commit', 'mode': 'keep'})
        b = Beneficiary.objects.get(facility=self.f, last_name='試験')
        self.assertEqual(b.status, Beneficiary.STATUS_ACTIVE)
        self.assertEqual(Beneficiary.objects.get(facility=self.f, last_name='見本').status, Beneficiary.STATUS_INACTIVE)
        ins = b.care_insurances.get()
        self.assertEqual((ins.care_level, ins.valid_from, ins.copay_ratio), ('要介護2', datetime.date(2026, 4, 1), 1))
        self.assertEqual(b.guardians.get().phone, '09000000001')
        # もう一度読み込んでも増えない（同じ人・同じ認定は書き換え）
        self.client.post(reverse('beneficiaries:import'), {'file': kaipoke_csv()})
        self.client.post(reverse('beneficiaries:import'), {'action': 'commit', 'mode': 'overwrite'})
        self.assertEqual(Beneficiary.objects.filter(facility=self.f).count(), 3)
        self.assertEqual(CareInsurance.objects.filter(beneficiary__facility=self.f).count(), 2)
        self.assertEqual(b.guardians.count(), 1)
        # 認定が更新された CSV なら、新しい介護保険証を足す
        renewed = KAIPOKE_ROWS[0].replace('2026-04-01,2027-03-31,2026-04-01,2027-03-31', '2027-04-01,2028-03-31,2027-04-01,2028-03-31') \
            .replace('要介護2', '要介護3')
        self.client.post(reverse('beneficiaries:import'), {'file': kaipoke_csv([renewed])})
        self.client.post(reverse('beneficiaries:import'), {'action': 'commit', 'mode': 'overwrite'})
        self.assertEqual(b.care_insurances.count(), 2)
        self.assertEqual(b.care_insurances.first().care_level, '要介護3')
        res = self.client.get(reverse('beneficiaries:list'))
        self.assertContains(res, '要介護3')

    def test_bad_care_level(self):
        rows = importer.rows_from_file(kaipoke_csv([KAIPOKE_ROWS[0].replace('要介護2', '要介護9')]))
        plan = importer.plan(self.f, rows)
        self.assertEqual(plan[0]['action'], 'error')
        self.assertIn('要介護度', plan[0]['detail'])

    def test_other_layout_cannot_import(self):
        other = Facility.objects.create(name='ほか')
        self.client.force_login(StaffAccount.objects.create_user('o', password='pw12345678', facility=other))
        self.assertEqual(self.client.get(reverse('beneficiaries:import')).status_code, 404)


class BulkDeleteTests(KaigoBase):
    def test_bulk_delete_to_trash_with_care_records(self):
        b2 = Beneficiary.objects.create(facility=self.f, last_name='川上', first_name='一', date_of_birth=datetime.date(1938, 1, 1))
        CareInsurance.objects.create(beneficiary=self.b, care_level='要介護1')
        SupportLog.objects.create(facility=self.f, beneficiary=self.b, date=datetime.date(2026, 10, 1), content='訪問')
        res = self.client.post(reverse('beneficiaries:bulk'), {'ids': [self.b.pk, b2.pk], 'action': 'delete'})
        self.assertContains(res, '介護保険証')                  # 確認画面に、一緒に消えるものを出す
        self.assertEqual(Beneficiary.objects.filter(facility=self.f).count(), 2)
        self.client.post(reverse('beneficiaries:bulk'), {'ids': [self.b.pk, b2.pk], 'action': 'delete', 'confirm': '1',
                                                          'confirm_text': '削除', 'agree': '1'})
        self.assertEqual(Beneficiary.objects.filter(facility=self.f).count(), 0)
        self.assertEqual(DeletedBeneficiary.objects.filter(facility=self.f).count(), 2)
        from beneficiaries.trash import restore
        restore(DeletedBeneficiary.objects.get(beneficiary_pk=self.b.pk))
        self.assertEqual(SupportLog.objects.filter(beneficiary_id=self.b.pk).count(), 1)
        self.assertEqual(CareInsurance.objects.filter(beneficiary_id=self.b.pk).count(), 1)


class ScreenTests(KaigoBase):
    def test_tabs_on_detail(self):
        CareInsurance.objects.create(beneficiary=self.b, care_level='要支援2', valid_from=datetime.date(2026, 1, 1),
                                     valid_until=datetime.date(2026, 12, 31), insured_number='0000000099')
        res = self.client.get(reverse('beneficiaries:detail', args=[self.b.pk]))
        for label in ('支援経過', '予定・実績', 'ケアプラン', '担当者会議の要点', 'アセスメント', '介護保険証・負担割合証', '0000000099'):
            self.assertContains(res, label)

    def test_insurance_form(self):
        self.client.post(reverse('kaigo:insurance_new', args=[self.b.pk]), {
            'insurer_number': '999999', 'insured_number': '0000000001', 'care_level': '要介護1', 'valid_from': '2026-10-01',
            'valid_until': '2027-09-30', 'copay_ratio': '2'})
        ins = self.b.care_insurances.get()
        self.assertEqual((ins.care_level, ins.copay_ratio), ('要介護1', 2))
        self.assertEqual(self.client.get(reverse('kaigo:insurance_edit', args=[self.b.pk, ins.pk])).status_code, 200)

    def test_logs(self):
        pv = Provider.objects.create(facility=self.f, name='架空デイ', service_kind='通所介護')
        url = reverse('kaigo:logs', args=[self.b.pk])
        self.client.post(url, {'date': '2026-10-05', 'time': '10:00', 'category': 'monitoring', 'method': 'visit', 'content': '自宅でモニタリング'})
        self.client.post(url, {'date': '2026-10-06', 'category': 'provider', 'method': 'phone', 'provider': pv.pk, 'content': '利用日の変更を連絡'})
        self.client.post(url, {'date': '2026-10-06', 'category': 'other', 'content': ''})       # 空は保存しない
        self.assertEqual(SupportLog.objects.filter(beneficiary=self.b).count(), 2)
        res = self.client.get(url + '?cat=provider')
        self.assertContains(res, '利用日の変更を連絡')
        self.assertNotContains(res, '自宅でモニタリング')
        res = self.client.get(reverse('kaigo:logs_print', args=[self.b.pk]) + '?fmt=html')
        self.assertContains(res, '居宅介護支援経過')
        self.assertContains(res, '事業所との連絡・電話・架空デイ')
        log = SupportLog.objects.get(category='monitoring')
        self.client.post(url, {'log': log.pk, 'action': 'delete'})
        self.assertFalse(SupportLog.objects.filter(pk=log.pk).exists())

    def test_uses(self):
        pv = Provider.objects.create(facility=self.f, name='架空ヘルパー')
        url = reverse('kaigo:uses', args=[self.b.pk, 2026, 10])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.post(url, {'row_count': 3, 'r0_id': '', 'r0_provider': pv.pk, 'r0_service': '訪問介護', 'r0_start': '10:00',
                               'r0_end': '11:00', 'r0_plan': ['5', '12', '19'], 'r0_done': ['5'],
                               'r1_id': '', 'r1_provider_name': '架空デイ', 'r1_service': '通所介護', 'r1_plan': ['7', '40'],
                               'r2_id': ''})
        rows = list(ServiceUse.objects.filter(beneficiary=self.b, year=2026, month=10))
        self.assertEqual(len(rows), 2)
        self.assertEqual((rows[0].plan_days, rows[0].done_days, rows[0].provider_label), ([5, 12, 19], [5], '架空ヘルパー'))
        self.assertEqual((rows[1].plan_days, rows[1].provider_label), ([7], '架空デイ'))      # 31 日より後は入れない
        self.client.post(url, {'action': 'plan_to_done'})
        self.assertEqual(ServiceUse.objects.get(pk=rows[0].pk).done_days, [5, 12, 19])
        # 次の月へ写す：同じ曜日・同じ週の並び（10/5 は第1月曜 → 11/2）
        nov = reverse('kaigo:uses', args=[self.b.pk, 2026, 11])
        self.client.post(nov, {'action': 'copy_prev'})
        r = ServiceUse.objects.get(beneficiary=self.b, year=2026, month=11, provider=pv)
        self.assertEqual((r.plan_days, r.done_days), ([2, 9, 16], []))
        res = self.client.get(reverse('kaigo:uses_print', args=[self.b.pk, 2026, 10]) + '?fmt=html')
        self.assertContains(res, 'サービス利用票')
        res = self.client.get(reverse('kaigo:monthly', args=[2026, 11]))
        self.assertContains(res, '未入力')
        # 行を消す
        self.client.post(url, {'row_count': 1, 'r0_id': rows[1].pk, 'r0_service': '通所介護', 'r0_delete': '1'})
        self.assertFalse(ServiceUse.objects.filter(pk=rows[1].pk).exists())

    def test_plan(self):
        CareInsurance.objects.create(beneficiary=self.b, care_level='要介護2', valid_from=datetime.date(2026, 4, 1),
                                     valid_until=datetime.date(2027, 3, 31))
        res = self.client.get(reverse('kaigo:plan_new', args=[self.b.pk]))
        self.assertContains(res, 'value="要介護2" selected')            # 介護保険証から入れる
        self.client.post(reverse('kaigo:plan_new', args=[self.b.pk]), {
            'created_on': '2026-10-01', 'plan_type': 'first', 'cert_state': 'certified', 'care_level': '要介護2',
            'wishes': '自宅で暮らしたい', 'policy': '転ばないように', 'goal_count': 2,
            'g0_need': '一人で入浴できない', 'g0_long_goal': '安全に入浴', 'g0_service': '入浴介助', 'g0_insured': '1',
            'g0_kind': '訪問介護', 'g0_frequency': '週2回', 'g1_need': '',
            'weekly_count': 2, 'w0_days': ['0', '3'], 'w0_start': '10:00', 'w0_end': '11:00', 'w0_content': '訪問介護'})
        plan = KaigoPlan.objects.get(beneficiary=self.b)
        self.assertEqual(len(plan.goals), 1)
        self.assertEqual(plan.goals[0]['insured'], '○')
        self.assertEqual(plan.weekly, [{'days': [0, 3], 'start': '10:00', 'end': '11:00', 'content': '訪問介護'}])
        grid = plan.week_grid()
        ten = next(r for r in grid if r['label'] == '10:00')
        self.assertEqual(ten['cells'][0], ['10:00〜11:00 訪問介護'])
        self.assertEqual(ten['cells'][1], [])
        res = self.client.get(reverse('kaigo:plan_print', args=[self.b.pk, plan.pk]) + '?fmt=html')
        for label in ('居宅サービス計画書（1）', '居宅サービス計画書（2）', '週間サービス計画表', '一人で入浴できない'):
            self.assertContains(res, label)
        # 2 つめは前の計画を写して始める
        res = self.client.get(reverse('kaigo:plan_new', args=[self.b.pk]))
        self.assertContains(res, '一人で入浴できない')

    def test_meeting(self):
        self.client.post(reverse('kaigo:meeting_new', args=[self.b.pk]), {
            'held_on': '2026-10-10', 'start': '13:00', 'end': '13:30', 'place': '自宅', 'count': '1', 'attendee_count': 3,
            'a0_org': '本人', 'a0_name': '山本', 'a1_org': '架空デイ（生活相談員）', 'a1_name': '佐藤', 'a2_org': '',
            'topics': '入浴', 'conclusion': '週2回の入浴介助を続ける', 'add_log': '1'})
        m = Meeting.objects.get(beneficiary=self.b)
        self.assertEqual(len(m.attendees), 2)
        self.assertTrue(SupportLog.objects.filter(beneficiary=self.b, category='meeting').exists())
        res = self.client.get(reverse('kaigo:meeting_print', args=[self.b.pk, m.pk]) + '?fmt=html')
        self.assertContains(res, 'サービス担当者会議の要点')
        self.assertContains(res, '架空デイ（生活相談員）')
        res = self.client.get(reverse('kaigo:meeting_new', args=[self.b.pk]))
        self.assertContains(res, 'value="2"')                 # 開催回数は前回の次

    def test_assessment(self):
        self.client.post(reverse('kaigo:assessment_new', args=[self.b.pk]), {
            'assessed_on': '2026-10-01', 'kind': 'first', 'i_health': '高血圧', 'i_adl': '歩行は杖', 'summary': '入浴に介助が要る'})
        a = Assessment.objects.get(beneficiary=self.b)
        self.assertEqual((a.filled, a.items['adl']), (2, '歩行は杖'))
        res = self.client.get(reverse('kaigo:assessment_print', args=[self.b.pk, a.pk]) + '?fmt=html')
        self.assertContains(res, 'コミュニケーションにおける理解と表出の状況')
        self.assertContains(res, '歩行は杖')

    def test_providers(self):
        url = reverse('kaigo:providers')
        self.client.post(url, {'name': '架空訪問介護', 'service_kind': '訪問介護', 'phone': '000-0000'})
        pv = Provider.objects.get(facility=self.f)
        self.client.post(url, {'provider': pv.pk, 'action': 'toggle'})
        self.assertFalse(Provider.objects.get(pk=pv.pk).is_active)
        self.client.post(url, {'name': 'メール違い', 'email': 'xx'})
        self.assertEqual(Provider.objects.filter(facility=self.f).count(), 1)
        self.assertContains(self.client.get(url), '架空訪問介護')

    def test_other_facility_cannot_open(self):
        other = Facility.objects.create(name='ほか')
        ob = Beneficiary.objects.create(facility=other, last_name='他', first_name='人', date_of_birth=datetime.date(1950, 1, 1))
        self.assertEqual(self.client.get(reverse('kaigo:logs', args=[ob.pk])).status_code, 404)
        self.client.force_login(StaffAccount.objects.create_user('o', password='pw12345678', facility=other))
        self.assertEqual(self.client.get(reverse('kaigo:providers')).status_code, 404)


class ListSortTests(KaigoBase):
    def test_sort_by_care_level(self):
        def person(last, kana, level=None, until=None):
            b = Beneficiary.objects.create(facility=self.f, last_name=last, first_name='一', last_name_kana=kana,
                                           date_of_birth=datetime.date(1940, 1, 1))
            if level is not None:
                CareInsurance.objects.create(beneficiary=b, care_level=level, valid_from=datetime.date(2026, 1, 1), valid_until=until)
            return b
        person('青木', 'あおき', '要支援1', datetime.date(2027, 3, 31))
        person('井上', 'いのうえ', '要介護3', datetime.date(2026, 11, 30))
        person('上田', 'うえだ', '申請中')
        person('江口', 'えぐち', '要介護3', datetime.date(2027, 1, 31))
        CareInsurance.objects.create(beneficiary=self.b, care_level='要介護5', valid_from=datetime.date(2026, 1, 1))   # 山本
        url = reverse('beneficiaries:list')
        names = lambda res: [b.last_name for b in res.context['beneficiaries']]      # noqa: E731
        self.assertEqual(names(self.client.get(url)), ['青木', '井上', '上田', '江口', '山本'])            # 50 音順
        res = self.client.get(url + '?sort=care_desc')
        self.assertEqual(names(res), ['山本', '井上', '江口', '青木', '上田'])
        self.assertContains(res, '要介護3（2 名）')
        self.assertContains(res, 'value="care_desc" selected')
        self.assertEqual(names(self.client.get(url + '?sort=care_asc')), ['青木', '井上', '江口', '山本', '上田'])
        self.assertEqual(names(self.client.get(url + '?sort=cert')), ['井上', '江口', '青木', '上田', '山本'])
        self.assertEqual(names(self.client.get(url + '?sort=care_desc&row=あ')), ['井上', '江口', '青木', '上田'])
        self.assertContains(self.client.get(url + '?sort=care_desc'), '&sort=care_desc')       # タブ・50 音でも並びを保つ


class LogFeedTests(KaigoBase):
    def test_feed_newest_first_with_wareki_and_more(self):
        from kaigo import views
        for i in range(views.LOG_PAGE + 5):
            SupportLog.objects.create(facility=self.f, beneficiary=self.b, date=datetime.date(2026, 1, 1) + datetime.timedelta(days=i),
                                      content=f'経過{i}', category='monitoring')
        url = reverse('kaigo:logs', args=[self.b.pk])
        res = self.client.get(url)
        logs = list(res.context['logs'])
        self.assertEqual(len(logs), views.LOG_PAGE)
        self.assertEqual(logs[0].content, f'経過{views.LOG_PAGE + 4}')          # 新しいものが上、過去分が下に積み上がる
        self.assertContains(res, '令和8年4月15日')
        self.assertContains(res, 'もっと前を見る')
        self.assertNotContains(res, '<details class="card mb-3" id="log-write" open')     # 書く欄はたたんでおく
        res = self.client.get(url + f'?n={views.LOG_PAGE * 2}')
        self.assertEqual(len(res.context['logs']), views.LOG_PAGE + 5)
        self.assertNotContains(res, 'もっと前を見る')


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ScanTests(KaigoBase):
    def upload(self, kind, *files, **extra):
        return self.client.post(reverse('kaigo:scan_upload', args=[self.b.pk]), {'kind': kind, 'files': list(files), **extra})

    def test_upload_list_edit_delete(self):
        pdf = SimpleUploadedFile('ケアプラン.pdf', b'%PDF-1.4 test', content_type='application/pdf')
        jpg = SimpleUploadedFile('第4表.jpg', b'\xff\xd8\xff test', content_type='image/jpeg')
        bad = SimpleUploadedFile('memo.txt', b'x', content_type='text/plain')
        res = self.upload('plan', pdf, bad, title='居宅サービス計画書（R8.4）', doc_date='2026-04-01')
        self.assertRedirects(res, reverse('kaigo:plans', args=[self.b.pk]) + '#scans', fetch_redirect_response=False)
        self.upload('meeting', jpg)
        doc = ScanDocument.objects.get(kind='plan')
        self.assertEqual((doc.title, doc.doc_date, doc.file_name), ('居宅サービス計画書（R8.4）', datetime.date(2026, 4, 1), 'ケアプラン.pdf'))
        self.assertEqual(ScanDocument.objects.filter(beneficiary=self.b).count(), 2)       # .txt は入れない
        res = self.client.get(reverse('kaigo:plans', args=[self.b.pk]))
        self.assertContains(res, '紙・スキャンのケアプラン')
        self.assertContains(res, '居宅サービス計画書（R8.4）')
        self.assertNotContains(res, '第4表.jpg')                                            # ほかのタブの書類は出さない
        self.assertContains(self.client.get(reverse('kaigo:meetings', args=[self.b.pk])), '第4表.jpg')
        for name in ('logs', 'uses_now', 'assessments'):
            res = self.client.get(reverse(f'kaigo:{name}', args=[self.b.pk]), follow=True)
            self.assertContains(res, 'id="scans"')
        self.assertEqual(self.client.get(doc.file.url).status_code, 200)                   # ログインした事業所の人は開ける
        other = Facility.objects.create(name='ほか')
        self.client.force_login(StaffAccount.objects.create_user('o', password='pw12345678', facility=other))
        self.assertEqual(self.client.get(doc.file.url).status_code, 404)                   # ほかの事業所は開けない
        self.client.force_login(self.user)
        self.client.post(reverse('kaigo:scan_edit', args=[self.b.pk, doc.pk]), {'title': '第1〜3表', 'doc_date': '', 'kind': 'assessment'})
        doc.refresh_from_db()
        self.assertEqual((doc.title, doc.doc_date, doc.kind), ('第1〜3表', None, 'assessment'))
        self.client.post(reverse('kaigo:scan_edit', args=[self.b.pk, doc.pk]), {'action': 'delete'})
        self.assertFalse(ScanDocument.objects.filter(pk=doc.pk).exists())

    def test_insurance_scans_on_detail_and_trash(self):
        self.upload('insurance', SimpleUploadedFile('保険証.png', b'\x89PNG test', content_type='image/png'))
        res = self.client.get(reverse('beneficiaries:detail', args=[self.b.pk]))
        self.assertContains(res, '紙・スキャンの介護保険証・負担割合証')
        self.assertContains(res, '保険証.png')
        from beneficiaries.trash import file_names
        self.assertEqual(len(file_names(self.b)), 1)


class MinutesFollowupTests(KaigoBase):
    """2026-10-20 の打ち合わせの対応（記録の種別・AI・アセスメントの差分・絞り込み・メニュー）"""

    def test_ten_categories_and_custom_names(self):
        from .models import KaigoSetting
        self.assertEqual([label for _, label in SupportLog.CATEGORIES],
                         ['モニタリング', 'サービス担当者会議', '家族との連絡', '利用者との連絡', '入院・退院', 'アセスメント',
                          '要介護認定', '事業所との連絡', '医療連携', 'その他'])
        self.client.post(reverse('kaigo:settings'), {'summary_chars': '600', 'style_note': 'もう少し締める',
                                                     'label_medical': '医療機関との連携', 'label_other': ''})
        st = KaigoSetting.of(self.f)
        self.assertEqual((st.summary_chars, st.style_note, st.category_labels), (600, 'もう少し締める', {'medical': '医療機関との連携'}))
        SupportLog.objects.create(facility=self.f, beneficiary=self.b, date=datetime.date(2026, 10, 1), category='medical', content='主治医へ連絡')
        res = self.client.get(reverse('kaigo:logs', args=[self.b.pk]))
        self.assertContains(res, '<div class="kg-entry-cat">医療機関との連携')
        self.assertContains(res, '入院・退院')
        self.assertContains(res, 'value="600" selected')
        res = self.client.get(reverse('kaigo:logs_print', args=[self.b.pk]) + '?fmt=html')
        self.assertContains(res, '医療機関との連携')

    def test_summarize_prompt_has_chars_style_and_plain_words(self):
        from unittest import mock
        from .models import KaigoSetting
        st = KaigoSetting.of(self.f)
        st.style_note = '砕けすぎないように'
        st.save()
        with mock.patch('kaigo.ai.ask_ai', return_value=('自宅で面談した。デイは週2回通えている。', None)) as ask:
            res = self.client.post(reverse('kaigo:ai_summarize'), {'text': 'えっと自宅で面談して、デイは週2回行けてるって', 'chars': 300,
                                                                   'category': 'monitoring'}, content_type='application/json')
        data = res.json()
        self.assertEqual(data['text'], '自宅で面談した。デイは週2回通えている。')
        system, content = ask.call_args[0][0], ask.call_args[0][1]
        self.assertIn('300 文字くらい', system)
        self.assertIn('砕けすぎないように', system)
        self.assertIn('歯が浮くような', system)
        self.assertIn('モニタリング', content)
        res = self.client.post(reverse('kaigo:ai_summarize'), {'text': ''}, content_type='application/json')
        self.assertEqual(res.status_code, 400)

    def test_read_image_and_meeting_draft(self):
        import io
        from unittest import mock

        from PIL import Image
        buf = io.BytesIO()
        Image.new('RGB', (20, 20), 'white').save(buf, format='PNG')
        photo = SimpleUploadedFile('memo.png', buf.getvalue(), content_type='image/png')
        with mock.patch('kaigo.ai.ask_ai', return_value=('入浴　週2回', None)) as ask:
            res = self.client.post(reverse('kaigo:ai_read'), {'file': photo})
        self.assertEqual(res.json()['text'], '入浴　週2回')
        self.assertEqual(ask.call_args[0][1][0]['type'], 'image')
        heic = SimpleUploadedFile('a.heic', b'x', content_type='image/heic')
        self.assertEqual(self.client.post(reverse('kaigo:ai_read'), {'file': heic}).status_code, 400)
        reply = '```json\n{"topics": "1. 入浴", "discussion": "デイで週2回", "conclusion": "続ける", "remaining": ""}\n```'
        with mock.patch('kaigo.ai.ask_ai', return_value=(reply, None)):
            res = self.client.post(reverse('kaigo:ai_meeting'), {'text': '入浴の話'}, content_type='application/json')
        self.assertEqual(res.json(), {'topics': '1. 入浴', 'discussion': 'デイで週2回', 'conclusion': '続ける', 'remaining': ''})
        res = self.client.get(reverse('kaigo:meeting_new', args=[self.b.pk]))
        self.assertContains(res, '会議のメモ・音声から下書きを作る')
        self.assertContains(res, 'kaigo-ai.js')

    def test_assessment_copy_and_diff(self):
        url = reverse('kaigo:assessment_new', args=[self.b.pk])
        self.client.post(url, {'assessed_on': '2026-04-01', 'kind': 'first', 'i_health': '高血圧', 'i_adl': '杖で歩く', 'summary': '入浴に介助'})
        res = self.client.get(url)
        self.assertContains(res, '前回（2026/4/1）の内容を写してあります')
        self.assertContains(res, '杖で歩く')
        self.assertContains(res, '入浴に介助')                                   # 課題のまとめも写す
        res = self.client.post(url, {'assessed_on': '2026-10-01', 'kind': 'regular', 'i_health': '高血圧', 'i_adl': '車いす',
                                     'summary': '入浴に介助'}, follow=True)
        self.assertContains(res, '前回から変わった項目：ADL')
        res = self.client.get(reverse('kaigo:assessments', args=[self.b.pk]))
        self.assertContains(res, '前回から変わった項目：ADL')
        new = Assessment.objects.get(assessed_on=datetime.date(2026, 10, 1))
        res = self.client.get(reverse('kaigo:assessment_edit', args=[self.b.pk, new.pk]))
        self.assertContains(res, '前回（2026/4/1）とくらべています')
        self.assertContains(res, 'kg-asm-prev')

    def test_list_filter_by_care_level_and_menu(self):
        for last, level in (('青木', '要支援1'), ('井上', '要介護3'), ('上田', '申請中')):
            b = Beneficiary.objects.create(facility=self.f, last_name=last, first_name='一', date_of_birth=datetime.date(1940, 1, 1))
            CareInsurance.objects.create(beneficiary=b, care_level=level)
        url = reverse('beneficiaries:list')
        names = lambda res: sorted(b.last_name for b in res.context['beneficiaries'])      # noqa: E731
        self.assertEqual(names(self.client.get(url + '?care=support')), ['青木'])
        self.assertEqual(names(self.client.get(url + '?care=care')), ['井上'])
        self.assertEqual(names(self.client.get(url + '?care=要介護3')), ['井上'])
        self.assertEqual(names(self.client.get(url + '?care=other')), ['上田', '山本'])
        res = self.client.get(url + '?care=care&sort=care_desc')
        self.assertContains(res, '要介護（1〜5）（1）')
        nav = res.content.decode().split('<nav', 1)[1].split('</nav>', 1)[0]
        self.assertIn('<span>利用者情報</span>', nav)


class DemoTests(KaigoBase):
    def test_seed_twice_render_and_remove_only_demo(self):
        from io import StringIO

        from django.core.management import call_command
        real_provider = Provider.objects.create(facility=self.f, name='本物の事業所')
        out = StringIO()
        call_command('kaigo_demo', stdout=out)
        self.assertIn('利用者 4 人', out.getvalue())
        self.assertNotIn('見本', out.getvalue())                        # 出力は件数だけ
        call_command('kaigo_demo', '--facility', str(self.f.pk), stdout=out)
        self.assertIn('もう入っています', out.getvalue())
        demo_people = Beneficiary.objects.filter(facility=self.f, notes__startswith='【サンプル】')
        self.assertEqual(demo_people.count(), 4)
        b = demo_people.get(last_name='試験')
        self.assertEqual(b.care_insurances.get().care_level, '要介護4')
        for name in ('logs', 'plans', 'meetings', 'assessments', 'uses_now'):
            self.assertEqual(self.client.get(reverse(f'kaigo:{name}', args=[b.pk]), follow=True).status_code, 200)
        plan = KaigoPlan.objects.get(beneficiary=b)
        self.assertContains(self.client.get(reverse('kaigo:plan_print', args=[b.pk, plan.pk]) + '?fmt=html'), '週間サービス計画表')
        self.assertContains(self.client.get(reverse('beneficiaries:list') + '?sort=care_desc'), '要介護4（1 名）')
        self.assertContains(self.client.get(reverse('kaigo:monthly_now')), '試験')
        call_command('kaigo_demo', '--facility', str(self.f.pk), '--remove', stdout=out)
        self.assertFalse(Beneficiary.objects.filter(facility=self.f, notes__startswith='【サンプル】').exists())
        self.assertTrue(Beneficiary.objects.filter(pk=self.b.pk).exists())                  # 本物の利用者は残る
        self.assertEqual(list(Provider.objects.filter(facility=self.f)), [real_provider])
        self.assertFalse(SupportLog.objects.filter(facility=self.f).exists())

    def test_other_layout_refused(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError
        other = Facility.objects.create(name='ほか')
        with self.assertRaises(CommandError):
            call_command('kaigo_demo', '--facility', str(other.pk))


class LayoutPolishTests(KaigoBase):
    def test_modal_saves_address_and_keeps_hidden_fields(self):
        self.b.disability_type = '前からの値'
        self.b.save()
        res = self.client.post(reverse('beneficiaries:update', args=[self.b.pk]), {
            '_partial': '1', 'last_name': '山本', 'first_name': '和子', 'last_name_kana': 'やまもと', 'first_name_kana': '',
            'date_of_birth': '1940-05-12', 'gender': 'female', 'status': 'active', 'notes': '',
            'postal_code': '1000001', 'address': '架空市1-1', 'home_phone': '000-0000-0000', 'mobile_phone': ''})
        self.assertEqual(res.status_code, 302)
        self.b.refresh_from_db()
        self.assertEqual((self.b.address, self.b.postal_code, self.b.home_phone), ('架空市1-1', '1000001', '000-0000-0000'))
        self.assertEqual(self.b.disability_type, '前からの値')          # 窓に出していない項目はそのまま

    def test_plan_form_hides_extra_blank_rows(self):
        res = self.client.get(reverse('kaigo:plan_new', args=[self.b.pk]))
        self.assertContains(res, 'kg-more-hidden')
        self.assertContains(res, '課題（ニーズ）の欄を足す')
        self.assertContains(res, 'data-bs-target="#scan-form-plan"', count=0)     # 新しく作るときは紙の欄を出さない
        res = self.client.get(reverse('kaigo:plans', args=[self.b.pk]))
        self.assertContains(res, 'data-bs-target="#scan-form-plan"')               # 取り込みはたたんである


class HardeningTests(KaigoBase):
    """数字でない ID・件数を渡しても 500 にしない（2026-10-22 の点検）"""

    def test_non_numeric_ids_do_not_crash(self):
        pv = Provider.objects.create(facility=self.f, name='架空')
        url = reverse('kaigo:logs', args=[self.b.pk])
        self.assertEqual(self.client.get(url + '?edit=abc&n=xyz').status_code, 200)
        self.assertEqual(self.client.post(url, {'log': 'abc', 'action': 'delete'}).status_code, 302)
        self.assertEqual(self.client.post(url, {'date': '2026-10-01', 'category': 'other', 'provider': 'x', 'content': 'a'}).status_code, 302)
        self.assertEqual(SupportLog.objects.get(content='a').provider, None)
        self.assertEqual(self.client.get(reverse('kaigo:providers') + '?edit=abc').status_code, 200)
        self.assertEqual(self.client.post(reverse('kaigo:providers'), {'provider': 'abc', 'action': 'toggle', 'name': 'x'}).status_code, 302)
        self.assertTrue(Provider.objects.filter(pk=pv.pk, is_active=True).exists())
        uses = reverse('kaigo:uses', args=[self.b.pk, 2026, 10])
        self.assertEqual(self.client.post(uses, {'row_count': 'abc'}).status_code, 302)
        self.assertEqual(self.client.post(uses, {'row_count': '1', 'r0_provider': 'abc', 'r0_service': 'x'}).status_code, 302)
        self.assertEqual(self.client.post(reverse('kaigo:plan_new', args=[self.b.pk]), {'created_on': '2026-10-01', 'goal_count': 'x',
                                                                                        'weekly_count': '-3'}).status_code, 302)
        self.assertEqual(self.client.post(reverse('kaigo:meeting_new', args=[self.b.pk]), {'held_on': '2026-10-01', 'attendee_count': 'x'}).status_code, 302)
        self.assertEqual(self.client.get(reverse('homecare:plan_new') + '?b=abc').status_code, 404)

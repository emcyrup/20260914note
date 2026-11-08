"""オウルの用紙：フェイスシート・アセスメントシート（2026-11-08）と、実施記録「本日の様子」の出力"""
import datetime

from django.test import TestCase
from django.urls import reverse

from accounts.models import StaffAccount
from facilities.models import Facility

from .models import AssessmentSheet, Beneficiary, FaceSheet, Guardian


class OwlSheetsTests(TestCase):
    def setUp(self):
        self.f = Facility.objects.create(name='児童発達支援センター　テスト', use_dev_assessment=True, use_therapy_record=True)
        self.user = StaffAccount.objects.create_user('owl', password='pw12345678', facility=self.f, role=StaffAccount.ROLE_ADMIN)
        self.client.force_login(self.user)
        self.kid = Beneficiary.objects.create(facility=self.f, last_name='見本', first_name='太郎', last_name_kana='みほん', first_name_kana='たろう',
                                              date_of_birth=datetime.date(2022, 7, 21), school_name='見本保育園', address='見本市見本町 1-1')
        Guardian.objects.create(beneficiary=self.kid, last_name='見本', first_name='花子', relation='mother', phone='080-0000-0000', is_primary=True)

    def test_face_sheet_prefill_save_pdf(self):
        res = self.client.get(reverse('beneficiaries:face_sheet_create', args=[self.kid.pk]))
        self.assertContains(res, 'フェイスシート')
        self.assertContains(res, 'value="見本保育園"')          # 台帳から写す
        self.assertContains(res, 'value="見本 花子"')
        post = {'date': '2026-07-03', 'filled_by': '職員A', 'head_school': '見本保育園', 'head_hospital': '見本小児科　主治医 見本先生',
                'item_sleep': '就寝・起床の時間はばらつきがある。寝つきは良い。', 'item_meal': '緑の野菜が苦手。スプーン・フォークを使う。',
                'item_strengths': '赤ちゃんに優しい。', 'family_0_name': '見本 祖母', 'family_0_relation': '祖母', 'family_0_birth': '（46）',
                'contact_0_who': '祖母', 'contact_0_where': '携帯', 'contact_0_tel': '080-0000-0000',
                'comm_means_5': '1', 'comm_understand_5': '1', 'head_emergency_work_ok': 'はい'}
        res = self.client.post(reverse('beneficiaries:face_sheet_create', args=[self.kid.pk]), post)
        sheet = FaceSheet.objects.get()
        self.assertRedirects(res, reverse('beneficiaries:face_sheet_edit', args=[self.kid.pk, sheet.pk]))
        self.assertEqual(sheet.date, datetime.date(2026, 7, 3))
        self.assertEqual(sheet.data['items']['sleep'], '就寝・起床の時間はばらつきがある。寝つきは良い。')
        self.assertEqual(sheet.data['family'][0]['relation'], '祖母')
        self.assertEqual(sheet.data['comm_means'], ['5'])
        # 文（AI の材料）
        self.assertIn('睡眠：就寝・起床の時間はばらつきがある', sheet.text())
        self.assertIn('意思伝達手段：⑤言葉', sheet.text())
        # 用紙（PDF の HTML）
        res = self.client.get(reverse('beneficiaries:face_sheet_pdf', args=[self.kid.pk, sheet.pk]) + '?fmt=html')
        self.assertContains(res, '児童発達支援・保育所等訪問支援　フェイスシート')
        self.assertContains(res, '見本 太郎')
        self.assertContains(res, '緑の野菜が苦手')
        self.assertContains(res, 'class="chk-on">⑤言葉')
        self.assertEqual(self.client.get(reverse('beneficiaries:face_sheet_pdf', args=[self.kid.pk, sheet.pk]))['Content-Type'], 'application/pdf')
        # 一覧（利用者情報）に出る。前回を写して作る
        res = self.client.get(reverse('beneficiaries:detail', args=[self.kid.pk]))
        self.assertContains(res, 'id="owl-sheets"')
        self.assertContains(res, '2026年7月3日')
        res = self.client.get(reverse('beneficiaries:face_sheet_create', args=[self.kid.pk]) + '?copy=1')
        self.assertContains(res, '緑の野菜が苦手')
        # 消す
        self.client.post(reverse('beneficiaries:face_sheet_delete', args=[self.kid.pk, sheet.pk]))
        self.assertFalse(FaceSheet.objects.exists())

    def test_assessment_sheet_save_pdf_and_previous(self):
        post = {'date': '2026-10-21', 'filled_by': '職員B', 'level_greeting': '1', 'note_greeting': 'おはようは言える。',
                'level_conversation': '2', 'note_conversation': '2 語文・3 語文が出てきた。', 'level_rhythm': '2', 'note_rhythm': '起床は 8 時。夜は細切れ。',
                'level_literacy': '3', 'note_literacy': '線や渦巻きをかける。'}
        res = self.client.post(reverse('beneficiaries:assessment_sheet_create', args=[self.kid.pk]), post)
        a = AssessmentSheet.objects.get()
        self.assertRedirects(res, reverse('beneficiaries:assessment_sheet_edit', args=[self.kid.pk, a.pk]))
        self.assertEqual(a.items['greeting'], {'level': 1, 'note': 'おはようは言える。'})
        self.assertEqual(a.items['math'], {'level': None, 'note': ''})
        self.assertEqual(a.marks(), {1: 1, 2: 2, 3: 1})
        self.assertIn('対人関係・会話：あまり出来ない／2 語文', a.text())
        res = self.client.get(reverse('beneficiaries:assessment_sheet_pdf', args=[self.kid.pk, a.pk]) + '?fmt=html')
        self.assertContains(res, 'アセスメントシート')
        self.assertContains(res, 'おはようは言える')
        self.assertContains(res, '気持ちと行動の切り替え')
        self.assertContains(res, 'class="on">あまり出来ない')
        # 2 枚目を作ると前回の段階が出る
        res = self.client.get(reverse('beneficiaries:assessment_sheet_create', args=[self.kid.pk]) + '?copy=1')
        self.assertContains(res, '（前回）')
        self.assertContains(res, 'おはようは言える')
        # ほかの事業所の子は開けない
        other = Facility.objects.create(name='ほか', use_dev_assessment=True)
        ob = Beneficiary.objects.create(facility=other, last_name='他', first_name='子', date_of_birth=datetime.date(2020, 1, 1))
        self.assertEqual(self.client.get(reverse('beneficiaries:assessment_sheet_create', args=[ob.pk])).status_code, 404)

    def test_sheets_feed_plan_material(self):
        from support_plans import material
        FaceSheet.objects.create(beneficiary=self.kid, date=datetime.date(2026, 7, 3), data={'head': {}, 'items': {'meal': '緑の野菜が苦手'}})
        AssessmentSheet.objects.create(beneficiary=self.kid, date=datetime.date(2026, 10, 21), items={'greeting': {'level': 1, 'note': 'おはようは言える'}})
        text = material.ledger_text(self.kid)
        self.assertIn('■ フェイスシート 2026/7/3', text)
        self.assertIn('食事：緑の野菜が苦手', text)
        self.assertIn('■ アセスメントシート 2026/10/21', text)
        self.assertIn('挨拶：出来る／おはようは言える', text)

    def test_face_text_for_ai_drops_contacts(self):
        sheet = FaceSheet.objects.create(beneficiary=self.kid, date=datetime.date(2026, 7, 3), data={
            'head': {'address': '見本市見本町 1-2-3', 'tel': '080-0000-0000', 'guardian': '見本 花子',
                     'hospital': '見本小児科 主治医 見本先生 TEL 000-000-0000', 'medicine': '定期薬なし'},
            'items': {'meal': '何でも食べる'}})
        full = sheet.text()
        self.assertIn('住所：見本市見本町 1-2-3', full)
        self.assertIn('TEL：080-0000-0000', full)
        ai = sheet.text(for_ai=True)
        self.assertNotIn('見本町', ai)
        self.assertNotIn('080-0000-0000', ai)
        self.assertNotIn('000-000-0000', ai)
        self.assertNotIn('見本 花子', ai)
        self.assertIn('医療機関', ai)
        self.assertIn('見本小児科 主治医 見本先生', ai)
        self.assertIn('服薬（定期薬名・服薬方法）：定期薬なし', ai)
        self.assertIn('食事：何でも食べる', ai)
        # 材料にも連絡先を出さない
        from support_plans import material
        self.assertNotIn('080-0000-0000', material.ledger_text(self.kid))

    def test_assessment_changes_feed_monitoring_material(self):
        from support_plans import material
        AssessmentSheet.objects.create(beneficiary=self.kid, date=datetime.date(2026, 4, 1), items={
            'greeting': {'level': 2, 'note': ''}, 'conversation': {'level': 3, 'note': ''}, 'focus': {'level': 2, 'note': ''},
            'meal': {'level': 1, 'note': ''}})
        AssessmentSheet.objects.create(beneficiary=self.kid, date=datetime.date(2026, 10, 1), items={
            'greeting': {'level': 1, 'note': 'おはようは言える'}, 'conversation': {'level': 2, 'note': '2 語文が出る'},
            'focus': {'level': 3, 'note': ''}, 'meal': {'level': 1, 'note': ''}})
        text = material.sheet_changes_text(self.kid, datetime.date(2026, 5, 1), datetime.date(2026, 10, 31))
        self.assertIn('前回 2026/4/1 → 今回 2026/10/1', text)
        self.assertIn('良くなった項目', text)
        self.assertIn('対人関係・挨拶：あまり出来ない → 出来る（おはようは言える）', text)
        self.assertIn('対人関係・会話：ほとんど出来ない → あまり出来ない（2 語文が出る）', text)
        self.assertIn('下がった項目', text)
        self.assertIn('生活面・集中力：あまりない → ほとんどない', text)
        self.assertNotIn('食事', text)
        # 期間に今回のシートが無ければ出さない。collect() にも入る
        self.assertEqual(material.sheet_changes_text(self.kid, datetime.date(2026, 5, 1), datetime.date(2026, 9, 30)), '')
        m = material.collect(self.kid, datetime.date(2026, 5, 1), datetime.date(2026, 10, 31))
        self.assertIn('アセスメントシートの前回からの変化', m['sources'])
        self.assertIn('【アセスメントシートの変化', material.prompt_blocks(m))

    def test_blank_pdfs_and_report_links(self):
        # 空の用紙（氏名・所属は台帳から）
        res = self.client.get(reverse('beneficiaries:face_sheet_blank_pdf', args=[self.kid.pk]), {'fmt': 'html'})
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, '見本 太郎')
        self.assertContains(res, '見本保育園')
        self.assertContains(res, '利用者情報へ戻る')
        res = self.client.get(reverse('beneficiaries:assessment_sheet_blank_pdf', args=[self.kid.pk]), {'fmt': 'html'})
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, '挨拶')
        self.assertNotContains(res, 'class="on"')
        res = self.client.get(reverse('beneficiaries:face_sheet_blank_pdf', args=[self.kid.pk]))
        self.assertEqual(res['Content-Type'], 'application/pdf')
        # 帳票出力の「用紙（PDF）」：まだ無ければ案内、空の用紙へ、あれば最新へ
        self.f.use_reports = True
        self.f.save()
        res = self.client.get(reverse('reports:index'))
        self.assertContains(res, '用紙（PDF）：フェイスシート・アセスメントシート')
        res = self.client.get(reverse('reports:sheet'), {'beneficiary': self.kid.pk, 'sheet': 'face', 'which': 'latest'})
        self.assertRedirects(res, reverse('reports:index'))
        res = self.client.get(reverse('reports:sheet'), {'beneficiary': self.kid.pk, 'sheet': 'assess', 'which': 'blank'})
        self.assertEqual(res.status_code, 302)
        self.assertEqual(res['Location'], reverse('beneficiaries:assessment_sheet_blank_pdf', args=[self.kid.pk]) + '?inline=1')
        sheet = FaceSheet.objects.create(beneficiary=self.kid, date=datetime.date(2026, 7, 3), data={'head': {}, 'items': {}})
        res = self.client.get(reverse('reports:sheet'), {'beneficiary': self.kid.pk, 'sheet': 'face', 'which': 'latest'})
        self.assertEqual(res['Location'], reverse('beneficiaries:face_sheet_pdf', args=[self.kid.pk, sheet.pk]) + '?inline=1')
        res = self.client.get(reverse('reports:sheet'), {'beneficiary': self.kid.pk, 'sheet': 'xxx'})
        self.assertRedirects(res, reverse('reports:index'))

    def test_hidden_when_feature_off(self):
        self.f.use_dev_assessment = False
        self.f.save(update_fields=['use_dev_assessment'])
        res = self.client.get(reverse('beneficiaries:face_sheet_create', args=[self.kid.pk]))
        self.assertEqual(res.status_code, 302)
        self.assertNotContains(self.client.get(reverse('beneficiaries:detail', args=[self.kid.pk])), 'id="owl-sheets"')


class DailyNotesReportTests(TestCase):
    """実施記録「本日の様子」：療育記録を 支援内容／本日の様子／記録者 の形で"""

    def setUp(self):
        self.f = Facility.objects.create(name='児童発達支援センター　テスト', use_therapy_record=True, use_reports=True)
        self.user = StaffAccount.objects.create_user('owl', password='pw12345678', facility=self.f, role=StaffAccount.ROLE_ADMIN, display_name='見本 職員')
        self.client.force_login(self.user)
        self.kid = Beneficiary.objects.create(facility=self.f, last_name='見本', first_name='太郎', date_of_birth=datetime.date(2022, 7, 21))

    def test_report(self):
        from therapy.models import TherapyRecord
        from reports import services
        TherapyRecord.objects.create(facility=self.f, beneficiary=self.kid, date=datetime.date(2026, 8, 3), time=datetime.time(10, 0), staff=self.user,
                                     activities=['バッティング', 'パズル'], body='紙コップタワーを倒すことができた。', situation='「もう一回！」と言ってくり返した。')
        rep = services.daily_notes(self.f, self.kid, datetime.date(2026, 8, 1), datetime.date(2026, 8, 31))
        self.assertEqual(rep.columns, ['日付', '施設', '本日の様子', '記録者名'])
        self.assertEqual(len(rep.rows), 1)
        self.assertIn('【支援内容】\n・バッティング\n・パズル', rep.rows[0][2])
        self.assertIn('【本日の様子】\n紙コップタワー', rep.rows[0][2])
        self.assertEqual(rep.rows[0][3], '見本 職員')
        self.assertIn('daily_notes', services.report_kinds(self.f))
        self.assertNotIn('daily_notes', services.report_kinds(Facility.objects.create(name='日誌の事業所')))
        res = self.client.get(reverse('reports:export'), {'kind': 'daily_notes', 'beneficiary': self.kid.pk, 'start': '2026-08-01', 'end': '2026-08-31', 'fmt': 'html'})
        self.assertContains(res, '本日の様子')
        self.assertContains(res, '利用サービス：児童発達支援')
        self.assertContains(res, 'バッティング')

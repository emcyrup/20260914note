"""アンケート・自己評価のテスト"""
import datetime
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from accounts.models import StaffAccount
from beneficiaries.models import Beneficiary, Guardian
from facilities.models import Facility

from .models import SelfEvaluation, Survey, SurveyResponse, fiscal_label, fiscal_year_of
from .questions import default_questions, questions_from_text, questions_to_text


class SurveyTests(TestCase):
    def setUp(self):
        self.f = Facility.objects.create(name='児童発達支援センター　オウル', layout=Facility.LAYOUT_RYOIKU, use_survey=True,
                                         line_channel_access_token='tok')
        self.user = StaffAccount.objects.create_user('owl', password='pw12345678', facility=self.f,
                                                     role=StaffAccount.ROLE_ADMIN, display_name='大和')
        self.client.login(username='owl', password='pw12345678')
        self.kid = Beneficiary.objects.create(facility=self.f, last_name='青木', first_name='子', date_of_birth=datetime.date(2019, 4, 1))
        Guardian.objects.create(beneficiary=self.kid, last_name='青木', first_name='母', relation='母', line_user_id='U1', line_linked=True)
        Guardian.objects.create(beneficiary=self.kid, last_name='青木', first_name='父', relation='父', line_user_id='U1', line_linked=True)  # 同じ LINE は 1 回

    def test_questions_roundtrip_and_fiscal(self):
        qs = default_questions('guardian', 'jihatsu')
        self.assertGreater(len(qs), 20)
        self.assertEqual(qs[0]['section'], '環境・体制整備')
        text = questions_to_text(qs)
        self.assertTrue(text.startswith('# 環境・体制整備\n'))
        self.assertEqual(questions_from_text(text), qs)
        self.assertEqual(questions_from_text('# A\n\n問1\n問2\n# B\n問3')[2], {'no': 3, 'section': 'B', 'text': '問3'})
        self.assertEqual(fiscal_year_of(datetime.date(2026, 3, 31)), 2025)
        self.assertEqual(fiscal_year_of(datetime.date(2026, 4, 1)), 2026)
        self.assertEqual(fiscal_label(2026), '令和8年度')

    def test_disabled_hides_menu(self):
        self.f.use_survey = False
        self.f.save()
        res = self.client.get(reverse('surveys:index'))
        self.assertRedirects(res, reverse('facilities:dashboard'))
        res = self.client.get(reverse('facilities:dashboard'))
        self.assertNotContains(res, 'アンケート・自己評価')

    def test_create_answer_aggregate_pdf(self):
        res = self.client.get(reverse('surveys:index'))
        self.assertContains(res, 'アンケート・自己評価')
        res = self.client.get(reverse('surveys:create') + '?kind=guardian&year=2026')
        self.assertContains(res, '# 環境・体制整備')
        self.assertContains(res, 'value="令和8年度 保護者評価"')
        res = self.client.post(reverse('surveys:create'), {
            'kind': 'guardian', 'service': 'jihatsu', 'fiscal_year': '2026', 'title': '令和8年度 保護者評価', 'intro': 'お願いします',
            'questions': '# 環境\n広さは十分か\n職員は適切か\n# 満足度\n満足しているか', 'is_open': 'on', 'closes_on': '2099-12-31'})
        s = Survey.objects.get()
        self.assertRedirects(res, reverse('surveys:detail', args=[s.pk]))
        self.assertEqual([q['text'] for q in s.questions], ['広さは十分か', '職員は適切か', '満足しているか'])
        self.assertTrue(s.accepting)
        res = self.client.get(reverse('surveys:detail', args=[s.pk]))
        self.assertContains(res, s.public_path())
        self.assertContains(res, '<svg')                   # QR
        self.assertContains(res, 'LINE 連携ずみの保護者に送る（1 人）')
        # 回答ページ（ログインなし）
        self.client.logout()
        url = s.public_path()
        res = self.client.get(url)
        self.assertContains(res, '広さは十分か')
        self.assertContains(res, 'どちらともいえない')
        res = self.client.post(url, {'q1': 'yes', 'q2': 'neutral', 'c2': '人が足りない日がある', 'q3': 'yes', 'free_text': '送迎が助かります'})
        self.assertContains(res, '送信しました')
        self.client.post(url, {'q1': 'no', 'q3': 'yes'})
        self.client.post(url, {})                            # 何も答えない → 保存しない
        self.assertEqual(SurveyResponse.objects.count(), 2)
        # 書き換えたアドレスは 404
        res = self.client.get(url[:-2] + 'x/')
        self.assertEqual(res.status_code, 404)
        # 集計
        summary = s.summary()
        self.assertEqual(summary['n'], 2)
        self.assertEqual(summary['rows'][0]['counts'], {'yes': 1, 'neutral': 0, 'no': 1, 'unknown': 0})
        self.assertEqual(summary['rows'][0]['pct']['yes'], 50)
        self.assertEqual(summary['rows'][1]['comments'], ['人が足りない日がある'])
        self.assertEqual(summary['free'], ['送迎が助かります'])
        self.client.login(username='owl', password='pw12345678')
        res = self.client.get(reverse('surveys:detail', args=[s.pk]))
        self.assertContains(res, '集計（2 件）')
        self.assertContains(res, '人が足りない日がある')
        res = self.client.get(reverse('surveys:results_pdf', args=[s.pk]) + '?fmt=html')
        self.assertContains(res, '保護者等からの事業所評価の集計結果（公表）')
        self.assertContains(res, '送迎が助かります')
        res = self.client.get(reverse('surveys:notice_pdf', args=[s.pk]) + '?fmt=html')
        self.assertContains(res, 'のお願い')
        self.assertContains(res, url)
        # 回答があると設問は変えられない（件名などは直せる）
        res = self.client.post(reverse('surveys:edit', args=[s.pk]), {'fiscal_year': '2026', 'title': '直した件名', 'intro': 'x',
                                                                        'questions': '# A\n別の問'})      # チェックを外す＝送られない
        s.refresh_from_db()
        self.assertEqual((s.title, len(s.questions), s.is_open), ('直した件名', 3, False))
        self.assertFalse(s.accepting)
        res = self.client.get(url)
        self.assertContains(res, '締め切りました')
        # 回答を消す・アンケートを消す（管理者）
        r = SurveyResponse.objects.first()
        self.client.post(reverse('surveys:response_delete', args=[s.pk, r.pk]))
        self.assertEqual(SurveyResponse.objects.count(), 1)
        self.client.post(reverse('surveys:delete', args=[s.pk]))
        self.assertFalse(Survey.objects.exists())

    def test_line_send(self):
        s = Survey.objects.create(facility=self.f, kind='guardian', title='t', questions=default_questions('guardian', 'jihatsu'),
                                  closes_on=datetime.date(2099, 1, 1))
        with mock.patch('line_integration.sending.push_text', return_value=(True, '')) as push:
            res = self.client.post(reverse('surveys:line', args=[s.pk]), follow=True)
        self.assertEqual(push.call_count, 1)
        self.assertIn(s.public_path(), push.call_args[0][2])
        self.assertIn('【児童発達支援センター　オウル】tのお願い', push.call_args[0][2])
        self.assertContains(res, 'LINE で 1 人に送りました')
        s.refresh_from_db()
        self.assertEqual(s.line_sent_count, 1)

    def test_self_evaluation_flow(self):
        g = Survey.objects.create(facility=self.f, kind='guardian', fiscal_year=2026, title='保護者', questions=default_questions('guardian', 'jihatsu'))
        st = Survey.objects.create(facility=self.f, kind='staff', fiscal_year=2026, title='職員', questions=default_questions('staff', 'jihatsu'))
        SurveyResponse.objects.create(survey=st, answers={'1': 'yes', '2': 'no'}, comments={'1': '＋広い部屋がある', '2': '人が足りない'})
        SurveyResponse.objects.create(survey=st, answers={'1': 'yes', '2': 'no'})
        SurveyResponse.objects.create(survey=g, answers={'1': 'yes', '2': 'unknown'}, free_text='ありがとう')
        res = self.client.post(reverse('surveys:self_create'), {'fiscal_year': '2026', 'service': 'jihatsu'})
        ev = SelfEvaluation.objects.get()
        self.assertRedirects(res, reverse('surveys:self_edit', args=[ev.pk]))
        self.assertEqual((ev.guardian_survey, ev.staff_survey), (g, st))
        self.assertEqual(len(ev.items), len(st.questions))
        self.assertEqual(len(ev.guardian_items), len(g.questions))
        # 従業者評価の集計から：1 は はい、2 は いいえ。工夫（＋）と課題の文も入る
        self.assertEqual((ev.items[0]['result'], ev.items[1]['result']), ('yes', 'no'))
        self.assertEqual(ev.items[0]['strength'], '広い部屋がある')
        self.assertEqual(ev.items[1]['improvement'], '人が足りない')
        res = self.client.get(reverse('surveys:self_edit', args=[ev.pk]))
        self.assertContains(res, '職員：はい 2・いいえ 0')
        self.assertContains(res, 'ありがとう')
        post = {'summary': '来年度は職員配置を見直す', 'published_on': '2026-10-10', 'publish_note': 'ホームページ',
                'i1_result': 'yes', 'i1_strength': '広い', 'i1_improvement': '', 'i2_result': 'no', 'i2_improvement': '採用する',
                'g2_improvement': 'お便りで説明する'}
        res = self.client.post(reverse('surveys:self_edit', args=[ev.pk]), post)
        ev.refresh_from_db()
        self.assertEqual((ev.summary, ev.published_on, ev.items[1]['improvement'], ev.guardian_items[1]['improvement']),
                         ('来年度は職員配置を見直す', datetime.date(2026, 10, 10), '採用する', 'お便りで説明する'))
        res = self.client.get(reverse('surveys:self_pdf', args=[ev.pk]) + '?fmt=html')
        self.assertContains(res, '事業所における自己評価結果（公表）')
        self.assertContains(res, '保護者等からの事業所評価の集計結果（公表）')
        self.assertContains(res, '採用する')
        self.assertContains(res, '▶ お便りで説明する')
        self.assertContains(res, '来年度は職員配置を見直す')
        # 保護者評価の集計 PDF にも改善目標が載る
        res = self.client.get(reverse('surveys:results_pdf', args=[g.pk]) + '?fmt=html')
        self.assertContains(res, '▶ お便りで説明する')
        res = self.client.get(reverse('surveys:index'))
        self.assertContains(res, '公表 10/10')
        # 同じ年度でもう一度作ると同じものを開く
        self.client.post(reverse('surveys:self_create'), {'fiscal_year': '2026', 'service': 'jihatsu'})
        self.assertEqual(SelfEvaluation.objects.count(), 1)

    def test_other_facility_cannot_see(self):
        g = Facility.objects.create(name='ほか', layout=Facility.LAYOUT_RYOIKU, use_survey=True)
        s = Survey.objects.create(facility=g, kind='guardian', title='よそ', questions=default_questions('guardian', 'houday'))
        self.assertEqual(self.client.get(reverse('surveys:detail', args=[s.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse('surveys:index')), 'よそ')

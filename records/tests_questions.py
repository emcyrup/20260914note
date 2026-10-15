"""日誌の「共通の質問・個別の質問」（2026-10-15 ウィズユー藤森の依頼。records/questions.py）"""
import datetime
import importlib
from types import SimpleNamespace
from unittest import mock

from django.apps import apps
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import StaffAccount
from beneficiaries.models import Beneficiary
from facilities.models import Facility

from . import questions
from .models import ChildQuestions, DailyRecord, QuestionSetting


def _post_rows(rows, answers):
    data = {'q_count': str(len(rows))}
    for i, r in enumerate(rows):
        data.update({f'q_text_{i}': r['text'], f'q_kind_{i}': r['kind'], f'q_level_{i}': r['level'] or ''})
        if answers[i]:
            data[f'q_answer_{i}'] = answers[i]
    return data


class QuestionTests(TestCase):

    def setUp(self):
        self.f = Facility.objects.create(name='ウィズユー藤森')
        QuestionSetting.objects.create(facility=self.f, enabled=True)
        self.user = StaffAccount.objects.create_user('st', password='pw12345678', facility=self.f, role=StaffAccount.ROLE_ADMIN)
        self.client.force_login(self.user)
        self.b = Beneficiary.objects.create(facility=self.f, last_name='青木', first_name='空', last_name_kana='あおき',
                                            date_of_birth=datetime.date(2016, 4, 1))
        ChildQuestions.objects.create(beneficiary=self.b, levels=[['あいさつできた', '順番を待てた'], ['自分から声をかけられた']])

    def _save(self, day, answers, status='draft'):
        rows = questions.blank_rows(self.b)
        data = _post_rows(rows, answers)
        data.update({'date': day.isoformat(), 'status': status})
        return self.client.post(reverse('records:create', args=[self.b.pk]), data, follow=True)

    def test_turn_on_migration_for_with_you(self):
        mod = importlib.import_module('records.migrations.0012_journal_questions')
        other = Facility.objects.create(name='ほかの事業所')
        QuestionSetting.objects.all().delete()
        mod.turn_on_for_with_you(apps, None)
        self.assertTrue(questions.enabled(Facility.objects.get(pk=self.f.pk)))
        self.assertFalse(questions.enabled(other))

    def test_form_shows_common_and_child_questions_not_viewpoints(self):
        page = self.client.get(reverse('records:list', args=[self.b.pk])).content.decode()
        for q in questions.DEFAULT_COMMON + ['あいさつできた', '順番を待てた']:
            self.assertIn(q, page)
        self.assertNotIn('自分から声をかけられた', page)            # レベル 2 はまだ
        self.assertIn('どちらでもない', page)
        self.assertNotIn('観点（できていたら「はい」', page)       # 自由な観点は出さない
        self.assertIn('個別の質問 レベル 1／2', page)

    def test_save_answers_and_show(self):
        res = self._save(datetime.date(2026, 10, 1), ['yes', 'neutral', 'no', 'yes', 'yes', 'no'])
        self.assertContains(res, '日誌を保存しました')
        r = DailyRecord.objects.get(beneficiary=self.b)
        self.assertEqual([a['answer'] for a in r.question_answers], ['yes', 'neutral', 'no', 'yes', 'yes', 'no'])
        self.assertEqual(r.question_answers[4], {'kind': 'child', 'text': 'あいさつできた', 'level': 1, 'answer': 'yes'})
        self.assertContains(res, 'きょうの様子（質問）')
        self.assertIn('静かに座れた=どちらでもない', questions.text_for_ai(r.question_answers))
        # 質問を変えても、これまでの日誌は書いたときの質問のまま
        ChildQuestions.objects.filter(beneficiary=self.b).update(levels=[['別の質問']])
        self.assertEqual(questions.rows_for(r)[4]['text'], 'あいさつできた')

    def test_confirm_needs_all_answers(self):
        res = self._save(datetime.date(2026, 10, 1), ['yes', None, 'no', 'yes', 'yes', 'no'], status='confirmed')
        self.assertContains(res, '1 問が未回答')
        self.assertEqual(DailyRecord.objects.get(beneficiary=self.b).status, DailyRecord.STATUS_DRAFT)
        res = self._save(datetime.date(2026, 10, 2), ['yes'] * 6, status='confirmed')
        self.assertEqual(DailyRecord.objects.get(beneficiary=self.b, date=datetime.date(2026, 10, 2)).status,
                         DailyRecord.STATUS_CONFIRMED)

    def test_level_up_after_ten_records_with_80_percent_yes(self):
        day = datetime.date(2026, 9, 1)
        for i in range(9):
            child = ['yes', 'yes'] if i % 3 else ['yes', 'neutral']     # 「どちらでもない」は「はい」に数えない
            self._save(day + datetime.timedelta(days=i), ['yes'] * 4 + child)
        st = questions.level_stats(self.b, self.f)
        self.assertEqual((st['level'], st['records'], st['ready']), (1, 9, False))
        res = self._save(day + datetime.timedelta(days=9), ['yes'] * 6)
        self.assertContains(res, 'レベル 2 に上げました')
        cq = ChildQuestions.objects.get(beneficiary=self.b)
        self.assertEqual(cq.level, 2)
        self.assertEqual(questions.blank_rows(self.b)[-1], {'kind': 'child', 'text': '自分から声をかけられた', 'level': 2, 'answer': None})
        # 最後のレベルでは上がらない
        for i in range(10):
            self._save(day + datetime.timedelta(days=20 + i), ['yes'] * 5)
        self.assertEqual(ChildQuestions.objects.get(beneficiary=self.b).level, 2)

    def test_no_level_up_below_rate(self):
        day = datetime.date(2026, 9, 1)
        for i in range(10):
            self._save(day + datetime.timedelta(days=i), ['yes'] * 4 + (['yes', 'no'] if i % 2 else ['no', 'yes']))
        self.assertEqual(ChildQuestions.objects.get(beneficiary=self.b).level, 1)
        self.assertEqual(questions.level_stats(self.b, self.f)['rate'], 50)

    def test_beneficiary_section_save_levels(self):
        page = self.client.get(reverse('beneficiaries:detail', args=[self.b.pk]))
        self.assertContains(page, '個別の質問（日誌）')
        self.assertContains(page, 'あいさつできた')
        res = self.client.post(reverse('records:child_questions_save', args=[self.b.pk]),
                               {'level_1': '・一つ目\n二つ目\n\n二つ目', 'level_2': '', 'level_3': '三段目', 'level': '2'})
        self.assertRedirects(res, reverse('beneficiaries:detail', args=[self.b.pk]) + '#questions', fetch_redirect_response=False)
        cq = ChildQuestions.objects.get(beneficiary=self.b)
        self.assertEqual((cq.levels, cq.level), ([['一つ目', '二つ目'], ['三段目']], 2))

    @override_settings(ANTHROPIC_API_KEY='test-key')
    def test_suggest_from_plan_goals(self):
        url = reverse('records:child_questions_suggest', args=[self.b.pk])
        res = self.client.post(url)
        self.assertEqual(res.status_code, 400)                       # 計画もアセスメントも無い
        from support_plans.models import PlanGoal, SupportPlan
        plan = SupportPlan.objects.create(facility=self.f, beneficiary=self.b, created_by=self.user)
        PlanGoal.objects.create(plan=plan, goal_type=PlanGoal.TYPE_SHORT, content='友だちに自分から声をかける')
        reply = SimpleNamespace(content=[SimpleNamespace(type='text', text='{"levels": [["声をかけられた", "名前を呼べた"], ["遊びに誘えた"]]}')])
        with mock.patch('records.question_views.anthropic.Anthropic') as m:
            m.return_value.messages.create.return_value = reply
            res = self.client.post(url)
            sent = m.return_value.messages.create.call_args.kwargs['messages'][0]['content']
        self.assertEqual(res.json()['levels'], ['声をかけられた\n名前を呼べた', '遊びに誘えた'])
        self.assertIn('友だちに自分から声をかける', sent)
        self.assertNotIn('青木', sent)                                 # 名前は送らない

    def test_settings_save(self):
        res = self.client.post(reverse('facilities:feature_settings'), {
            'journal_sections': ['activity', 'observation'], 'question_form': '1', 'question_enabled': 'on',
            'question_common': '参加できた\n座れた', 'question_window': '5', 'question_rate': '90'})
        self.assertRedirects(res, reverse('facilities:settings'))
        s = QuestionSetting.objects.get(facility=self.f)
        self.assertEqual((s.common_questions, s.window, s.rate), ('参加できた\n座れた', 5, 90))
        self.assertEqual(questions.common_questions(Facility.objects.get(pk=self.f.pk)), ['参加できた', '座れた'])
        self.assertContains(self.client.get(reverse('facilities:settings')), 'id="journal-questions"')

    def test_off_for_other_facilities(self):
        QuestionSetting.objects.filter(facility=self.f).update(enabled=False)
        page = self.client.get(reverse('records:list', args=[self.b.pk])).content.decode()
        self.assertNotIn('name="q_count"', page)
        self.assertNotContains(self.client.get(reverse('beneficiaries:detail', args=[self.b.pk])), '個別の質問（日誌）')
        self.assertEqual(self.client.post(reverse('records:child_questions_save', args=[self.b.pk])).status_code, 404)
        self._save(datetime.date(2026, 10, 1), ['yes'] * 6)
        self.assertEqual(DailyRecord.objects.get(beneficiary=self.b).question_answers, [])

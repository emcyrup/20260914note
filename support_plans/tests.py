import json
from datetime import date, timedelta
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import StaffAccount
from beneficiaries.models import Beneficiary
from esignatures.models import EsignatureRecord
from facilities.models import Facility

from .models import MonitoringRecord, PlanGoal, SupportPlan


class PlanFlowTestBase(TestCase):
    def setUp(self):
        self.facility = Facility.objects.create(name='テスト施設')
        self.user = StaffAccount.objects.create_user('staff', password='pass12345', facility=self.facility,
                                                     role=StaffAccount.ROLE_ADMIN)
        self.client.force_login(self.user)
        self.b = Beneficiary.objects.create(facility=self.facility, last_name='山田', first_name='太郎',
                                            date_of_birth=date(2016, 4, 1))
        self.plan = SupportPlan.objects.create(facility=self.facility, beneficiary=self.b, title='第1期', created_by=self.user)

    def step_url(self, n):
        return reverse('support_plans:step', args=[self.plan.pk, n])

    def fill_assessment(self):
        a = self.plan.get_step(1)
        a.interview_date = date.today()
        a.interviewed_with = '本人・母'
        a.condition = '落ち着いて活動できる日が増えた'
        a.environment = '小学2年生、放課後は週3回利用'
        a.wishes = '友だちと一緒に遊べるようになりたい'
        a.save()

    def fill_draft(self):
        d = self.plan.get_step(2)
        d.period_start = date.today()
        d.period_end = date.today() + timedelta(days=180)
        d.policy = '本人の好きな活動を通して友だちとの関わりを増やす'
        d.save()
        PlanGoal.objects.create(plan=self.plan, goal_type='long', content='友だちと協力して活動できる',
                                target_date=date.today() + timedelta(days=180))
        PlanGoal.objects.create(plan=self.plan, goal_type='short', content='順番を待てる',
                                target_date=date.today() + timedelta(days=90), support_content='職員が横につき声かけ')

    def fill_meeting(self):
        m = self.plan.get_step(3)
        m.meeting_date = date.today()
        m.attendees = '児発管・担当職員'
        m.beneficiary_attended = True
        m.opinions = '原案どおりで良い'
        m.save()

    def fill_consent(self, esign=True):
        c = self.plan.get_step(4)
        c.explained_date = date.today()
        c.explained_to = '母'
        if esign:
            EsignatureRecord.objects.create(facility=self.facility, signer_name='山田 花子', relationship='母',
                                            target_type='support_plan', target_id=self.plan.pk,
                                            signature_image='signatures/x.png')
        else:
            c.consent_method = 'paper'
            c.consent_date = date.today()
            c.consent_signer = '山田 花子'
        c.delivered_to_user_date = date.today()
        c.delivered_to_office_date = date.today()
        c.service_start_date = date.today()
        c.save()


class StepGatingTests(PlanFlowTestBase):
    def test_locked_step_redirects_to_current(self):
        res = self.client.get(self.step_url(2))
        self.assertRedirects(res, self.step_url(1))

    def test_cannot_complete_with_missing_items(self):
        remaining = self.plan.complete_step(1, self.user)
        self.assertEqual(len(remaining), 5)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.current_step, 1)

    def test_complete_step_advances(self):
        self.fill_assessment()
        self.assertEqual(self.plan.complete_step(1, self.user), [])
        self.assertEqual(self.plan.current_step, 2)
        self.assertTrue(self.plan.get_step(1).is_completed)
        self.assertEqual(self.client.get(self.step_url(2)).status_code, 200)

    def test_complete_via_post(self):
        self.fill_assessment()
        res = self.client.post(self.step_url(1), {
            'interview_date': date.today().isoformat(), 'interviewed_with': '本人・母',
            'condition': 'a', 'environment': 'b', 'wishes': 'c', 'notes': '', 'action': 'complete',
        })
        self.assertRedirects(res, self.step_url(2))
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.current_step, 2)

    def test_post_complete_with_missing_items_stays(self):
        res = self.client.post(self.step_url(1), {'interview_date': '', 'interviewed_with': '', 'condition': '',
                                                  'environment': '', 'wishes': '', 'notes': '', 'action': 'complete'})
        self.assertRedirects(res, self.step_url(1))
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.current_step, 1)

    def test_completed_step_is_readonly(self):
        self.fill_assessment()
        self.plan.complete_step(1, self.user)
        res = self.client.get(self.step_url(1))
        self.assertContains(res, '読み取り専用')
        self.assertContains(res, '完了を取り消して修正')
        res = self.client.post(self.step_url(1), {'condition': '書き換え', 'action': 'save'})
        self.assertRedirects(res, self.step_url(1))
        self.assertNotEqual(self.plan.get_step(1).condition, '書き換え')

    def test_reopen_only_previous_step(self):
        self.fill_assessment(); self.plan.complete_step(1, self.user)
        self.fill_draft();      self.plan.complete_step(2, self.user)
        self.assertFalse(self.plan.can_reopen_step(1))
        self.assertTrue(self.plan.can_reopen_step(2))
        res = self.client.post(reverse('support_plans:step_reopen', args=[self.plan.pk, 2]))
        self.assertRedirects(res, self.step_url(2))
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.current_step, 2)
        self.assertFalse(self.plan.get_step(2).is_completed)
        self.assertRedirects(self.client.get(self.step_url(3)), self.step_url(2))

    def test_draft_requires_goals(self):
        self.fill_assessment(); self.plan.complete_step(1, self.user)
        d = self.plan.get_step(2)
        d.period_start = d.period_end = date.today(); d.policy = 'x'; d.save()
        remaining = self.plan.complete_step(2, self.user)
        self.assertEqual(len(remaining), 2)
        self.assertTrue(any('長期目標' in r for r in remaining))

    def test_meeting_requires_absence_reason(self):
        self.fill_assessment(); self.plan.complete_step(1, self.user)
        self.fill_draft();      self.plan.complete_step(2, self.user)
        m = self.plan.get_step(3)
        m.meeting_date = date.today(); m.attendees = '職員'; m.opinions = 'ok'; m.save()
        remaining = self.plan.complete_step(3, self.user)
        self.assertEqual(remaining, ['本人の出席（欠席の場合は理由）を記録する'])
        m.absence_reason = '体調不良'; m.save()
        self.assertEqual(self.plan.complete_step(3, self.user), [])

    def test_consent_requires_signature_then_activates(self):
        self.fill_assessment(); self.plan.complete_step(1, self.user)
        self.fill_draft();      self.plan.complete_step(2, self.user)
        self.fill_meeting();    self.plan.complete_step(3, self.user)
        c = self.plan.get_step(4)
        c.explained_date = date.today(); c.explained_to = '母'
        c.delivered_to_user_date = c.delivered_to_office_date = c.service_start_date = date.today(); c.save()
        remaining = self.plan.complete_step(4, self.user)
        self.assertEqual(len(remaining), 1)
        self.assertIn('同意', remaining[0])
        EsignatureRecord.objects.create(facility=self.facility, signer_name='母', target_type='support_plan',
                                        target_id=self.plan.pk, signature_image='signatures/x.png')
        self.assertEqual(self.plan.complete_step(4, self.user), [])
        self.assertEqual(self.plan.current_step, 5)
        self.assertEqual(self.plan.status, SupportPlan.STATUS_ACTIVE)
        # 次回モニタリング期限はサービス開始から3か月後
        self.assertGreater(self.plan.next_monitoring_due, date.today() + timedelta(days=80))

    def test_signature_endpoint_rejects_before_step4(self):
        res = self.client.post(reverse('esignatures:save'), data={
            'image_data': 'data:image/png;base64,iVBORw0KGgo=', 'signer_name': '母',
            'target_type': 'support_plan', 'target_id': self.plan.pk,
        }, content_type='application/json')
        self.assertFalse(res.json()['ok'])
        self.assertIn('ステップ4', res.json()['error'])

    def test_other_facility_cannot_open(self):
        other = Facility.objects.create(name='他施設')
        u2 = StaffAccount.objects.create_user('other', password='x', facility=other)
        self.client.force_login(u2)
        self.assertEqual(self.client.get(reverse('support_plans:detail', args=[self.plan.pk])).status_code, 404)


class MonitoringAndSuccessorTests(PlanFlowTestBase):
    def setUp(self):
        super().setUp()
        self.fill_assessment(); self.plan.complete_step(1, self.user)
        self.fill_draft();      self.plan.complete_step(2, self.user)
        self.fill_meeting();    self.plan.complete_step(3, self.user)
        self.fill_consent();    self.plan.complete_step(4, self.user)

    def test_monitoring_record_updates_due_and_overdue(self):
        res = self.client.post(reverse('support_plans:monitoring_add', args=[self.plan.pk]), {
            'date': date.today().isoformat(), 'interviewed_with': '本人', 'implementation': '計画どおり',
            'achievement': 'progressing', 'achievement_detail': '', 'review_reason': '',
            'next_due': (date.today() - timedelta(days=1)).isoformat(),
        })
        self.assertRedirects(res, self.step_url(5))
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.monitoring_records.count(), 1)
        self.assertTrue(self.plan.monitoring_overdue)
        self.assertTrue(self.plan.get_step(5).is_completed)
        self.assertFalse(self.plan.can_reopen_step(4))

    def test_review_needed_requires_reason(self):
        res = self.client.post(reverse('support_plans:monitoring_add', args=[self.plan.pk]), {
            'date': date.today().isoformat(), 'implementation': 'x', 'achievement': 'achieved',
            'review_needed': 'on', 'review_reason': '',
        })
        self.assertEqual(res.status_code, 200)
        self.assertEqual(MonitoringRecord.objects.count(), 0)

    def test_successor_inherits_assessment_and_closes_predecessor(self):
        res = self.client.post(reverse('support_plans:successor', args=[self.plan.pk]))
        new = SupportPlan.objects.get(predecessor=self.plan)
        self.assertRedirects(res, reverse('support_plans:step', args=[new.pk, 1]))
        self.assertEqual(new.title, '第2期 個別支援計画')
        self.assertEqual(new.get_step(1).condition, self.plan.get_step(1).condition)
        self.assertEqual(new.current_step, 1)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.status, SupportPlan.STATUS_ACTIVE)
        # 新しい計画がサービス開始（ステップ4完了）すると前の計画は終了
        self.plan = new
        self.fill_assessment(); new.complete_step(1, self.user)
        self.fill_draft();      new.complete_step(2, self.user)
        self.fill_meeting();    new.complete_step(3, self.user)
        self.fill_consent(esign=False); new.complete_step(4, self.user)
        old = SupportPlan.objects.get(pk=new.predecessor_id)
        self.assertEqual(old.status, SupportPlan.STATUS_CLOSED)

    def test_pages_render(self):
        for url in (reverse('support_plans:list'), reverse('support_plans:detail', args=[self.plan.pk]),
                    reverse('support_plans:print', args=[self.plan.pk]), reverse('support_plans:create'),
                    reverse('beneficiaries:detail', args=[self.b.pk]), *[self.step_url(n) for n in range(1, 6)]):
            self.assertEqual(self.client.get(url).status_code, 200, url)


class PdfAndAiTests(PlanFlowTestBase):
    def test_plan_pdf_and_monitoring_report(self):
        self.fill_assessment(); self.plan.complete_step(1, self.user)
        self.fill_draft();      self.plan.complete_step(2, self.user)
        self.fill_meeting();    self.plan.complete_step(3, self.user)
        self.fill_consent();    self.plan.complete_step(4, self.user)
        res = self.client.get(reverse('support_plans:pdf', args=[self.plan.pk]))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Content-Type'], 'application/pdf')
        self.assertTrue(res.content.startswith(b'%PDF'))
        res = self.client.get(reverse('support_plans:monitoring_report', args=[self.plan.pk]))
        self.assertContains(res, 'モニタリング報告書')
        res = self.client.get(reverse('support_plans:monitoring_pdf', args=[self.plan.pk]))
        self.assertTrue(res.content.startswith(b'%PDF'))

    def _records(self):
        from records.models import DailyRecord
        recs = []
        for i, (act, obs) in enumerate([('ブロック', '「かして」と伝えて順番を待った'), ('工作', '困ったときに支援員へ声をかけた'),
                                          ('おやつ', '自分から出来事を報告する場面が増えた')]):
            recs.append(DailyRecord.objects.create(facility=self.facility, beneficiary=self.b, author=self.user,
                                                   date=date.today() - timedelta(days=10 * (i + 1)),
                                                   activity_name=act, observation_text=obs))
        return recs

    def test_find_evidence_ranks_related_records(self):
        from . import ai
        recs = self._records()
        hits = ai.find_evidence(self.b, '気持ちを言葉で伝える 順番を待つ', date.today() - timedelta(days=60), date.today())
        self.assertEqual(len(hits), 3)
        self.assertEqual(hits[0]['record'], recs[0])

    def test_goal_evidence_page_and_save(self):
        recs = self._records()
        self.fill_assessment(); self.plan.complete_step(1, self.user)
        g = PlanGoal.objects.create(plan=self.plan, goal_type='short', content='順番を待てる', support_content='声かけ')
        url = reverse('support_plans:goal_evidence', args=[self.plan.pk, g.pk])
        res = self.client.get(url)
        self.assertContains(res, '順番を待てる')
        self.assertContains(res, 'かして')
        res = self.client.post(url, {'record_ids': [recs[0].pk, recs[1].pk]})
        self.assertRedirects(res, self.step_url(2))
        self.assertEqual(g.evidence_records.count(), 2)
        res = self.client.get(self.step_url(2))
        self.assertContains(res, '根拠になった記録')
        # 印刷にも出る
        res = self.client.get(reverse('support_plans:print', args=[self.plan.pk]))
        self.assertContains(res, '根拠：')

    @override_settings(ANTHROPIC_API_KEY='k')
    @mock.patch('support_plans.ai.anthropic.Anthropic')
    def test_ai_assessment_draft(self, mock_cls):
        self._records()
        mock_cls.return_value.messages.create.return_value = SimpleNamespace(content=[SimpleNamespace(
            type='text', text='{"condition": "c", "environment": "e", "wishes": "w"}')])
        res = self.client.post(reverse('support_plans:ai_assessment', args=[self.plan.pk]), {'months': 6})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['draft'], {'condition': 'c', 'environment': 'e', 'wishes': 'w'})
        self.assertEqual(res.json()['records'], 3)
        self.assertIn('かして', mock_cls.return_value.messages.create.call_args.kwargs['messages'][0]['content'])

    @override_settings(ANTHROPIC_API_KEY='k')
    @mock.patch('support_plans.ai.anthropic.Anthropic')
    def test_ai_plan_draft_creates_goals_with_evidence(self, mock_cls):
        recs = self._records()
        self.fill_assessment(); self.plan.complete_step(1, self.user)
        d1 = recs[0].date.isoformat()
        mock_cls.return_value.messages.create.return_value = SimpleNamespace(content=[SimpleNamespace(
            type='text', text=json.dumps({'policy': '方針', 'family_wishes': '意向', 'goals': [
                {'type': 'long', 'content': '友だちと協力できる', 'evidence_dates': [d1]},
                {'type': 'short', 'content': '順番を待てる', 'support_content': '声かけ', 'frequency': '毎回', 'evidence_dates': [d1, '1999-01-01']},
            ]}))])
        res = self.client.post(reverse('support_plans:ai_draft', args=[self.plan.pk]),
                               {'start': (date.today() - timedelta(days=60)).isoformat(), 'end': date.today().isoformat()})
        self.assertRedirects(res, self.step_url(2))
        d = self.plan.get_step(2)
        self.assertEqual(d.policy, '方針')
        goals = list(self.plan.goals.order_by('goal_type'))
        self.assertEqual(len(goals), 2)
        short = self.plan.goals.get(goal_type='short')
        self.assertEqual(list(short.evidence_records.all()), [recs[0]])
        self.assertIsNotNone(short.target_date)

    def test_journal_menu_links_to_plan(self):
        res = self.client.get(reverse('records:list', args=[self.b.pk]))
        self.assertContains(res, reverse('support_plans:detail', args=[self.plan.pk]))

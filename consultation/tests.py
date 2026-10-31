import datetime
import json
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import StaffAccount
from beneficiaries.models import Beneficiary, BeneficiaryKnowledge
from facilities.models import Facility

from .models import ConsultationPlan


class ConsultationTests(TestCase):
    """相談支援の計画：使う設定の事業所だけ、入力と保存、3 つの印刷、AI の下書き、書類の読み取りからの登録、個別支援計画の材料になる"""

    def setUp(self):
        self.f = Facility.objects.create(name='児童発達支援センター　オウル', layout=Facility.LAYOUT_RYOIKU, use_therapy_record=True, use_consultation=True)
        self.user = StaffAccount.objects.create_user('owl', password='pw12345678', facility=self.f, role=StaffAccount.ROLE_ADMIN)
        self.client.force_login(self.user)
        self.b = Beneficiary.objects.create(facility=self.f, last_name='試験', first_name='太郎', last_name_kana='しけん', date_of_birth=datetime.date(2022, 7, 21))

    def _post(self, pk=None, **extra):
        data = {'beneficiary': self.b.pk, 'office_name': '相談支援事業所テスト', 'planner': '若木', 'plan_date': '2026-06-11', 'status': 'draft',
                'monitoring_period': '6ヶ月ごと（01月、07月）', 'copay_cap': '0円', 'child_cert_number': '1234567890',
                'overview': '出生時に黄疸。2歳から療育に通う。', 'cs_meal': 'スプーンは使える。', 'cs_toilet': 'おむつ。', 'history': '', 'medical': 'アトピー。',
                'complaint_child': '', 'complaint_family': '気持ちを言葉で伝えられるように。',
                'fam_0_relation': '祖母', 'fam_0_name': '（非表示）', 'fam_0_age': '46', 'fam_0_together': '同居', 'fam_0_job': '', 'fam_0_notes': '',
                'sv_0_name': '児童発達支援', 'sv_0_provider': 'センター', 'sv_0_content': '療育', 'sv_0_frequency': '月・水・金', 'sv_0_notes': '',
                'wishes': '本人：ぬいぐるみが好き。\n家族：気持ちを言葉で伝えられるように。', 'policy': '性格や特性を理解し心身の発達を促す。',
                'long_goal': '本人のペースで楽しむ。', 'short_goal': '先生と一緒に活動に取り組む。',
                'row_0_priority': '2', 'row_0_issue': 'ぬいぐるみが好き', 'row_0_goal': '先生と一緒に楽しむ', 'row_0_timing': '1年', 'row_0_service': '児童発達支援 月8日',
                'row_0_provider': 'センター', 'row_0_role': '参加する', 'row_0_eval_timing': '6ヶ月', 'row_0_notes': '',
                'row_1_priority': '1', 'row_1_issue': '気持ちを言葉で', 'row_1_goal': '気持ちを言葉で伝えられる', 'row_1_timing': '1年', 'row_1_service': '児童発達支援',
                'row_1_provider': 'センター', 'row_1_role': '伝える練習', 'row_1_eval_timing': '6ヶ月', 'row_1_notes': '',
                'row_2_priority': '', 'row_2_issue': '', 'row_2_goal': '',
                'wk_mon_0_time': '10:00', 'wk_mon_0_text': '児童発達支援センター（送迎あり）', 'wk_mon_1_time': '', 'wk_mon_1_text': '',
                'weekly_other': '保育所等訪問支援 2回/月', 'life_summary': '必要な支援を受けながら、言葉の発達と生活スキルの向上。'}
        data.update(extra)
        return self.client.post(reverse('consultation:edit', args=[pk]) if pk else reverse('consultation:create'), data)

    def test_gating_and_menu(self):
        res = self.client.get(reverse('facilities:dashboard'))
        self.assertContains(res, '相談支援の計画')
        Facility.objects.filter(pk=self.f.pk).update(use_consultation=False)
        res = self.client.get(reverse('consultation:index'))
        self.assertRedirects(res, reverse('facilities:dashboard'))
        self.assertNotContains(self.client.get(reverse('facilities:dashboard')), '相談支援の計画')

    def test_create_edit_and_print(self):
        res = self._post()
        self.assertEqual(res.status_code, 302)
        cp = ConsultationPlan.objects.get()
        self.assertEqual((cp.beneficiary, cp.plan_date, cp.child_state['meal'], len(cp.rows), cp.family[0]['relation'], cp.weekly['mon'][0]['text']),
                         (self.b, datetime.date(2026, 6, 11), 'スプーンは使える。', 2, '祖母', '児童発達支援センター（送迎あり）'))
        self.assertEqual([r['priority'] for r in cp.row_list()], ['1', '2'])          # 表は優先順位の順
        self.assertEqual(cp.goals_text(), ['優先1：気持ちを言葉で伝えられる', '優先2：先生と一緒に楽しむ'])
        res = self.client.get(reverse('consultation:edit', args=[cp.pk]))
        self.assertContains(res, 'value="児童発達支援センター（送迎あり）"')
        self.assertContains(res, '① 申請者の現状')
        for name, text in (('pdf_basic', '申請者の現状'), ('pdf_plan', '様式2-1'), ('pdf_weekly', '様式2-2')):
            res = self.client.get(reverse(f'consultation:{name}', args=[cp.pk]) + '?fmt=html')
            self.assertEqual(res.status_code, 200, name)
            self.assertContains(res, text)
        res = self.client.get(reverse('consultation:pdf_plan', args=[cp.pk]) + '?fmt=html')
        self.assertContains(res, '気持ちを言葉で伝えられる')
        self.assertContains(res, '1234567890')
        res = self.client.get(reverse('consultation:pdf_weekly', args=[cp.pk]) + '?fmt=html')
        self.assertContains(res, '<b>10:00</b>児童発達支援センター（送迎あり）')
        self.assertContains(res, '保育所等訪問支援 2回/月')
        # 編集して保存（優先順位の表を 1 行に）
        res = self._post(cp.pk, row_0_priority='', row_0_issue='', row_0_goal='', row_0_timing='', row_0_service='', row_0_provider='', row_0_role='', row_0_eval_timing='', status='active')
        cp.refresh_from_db()
        self.assertEqual((len(cp.rows), cp.status), (1, 'active'))
        # 一覧
        res = self.client.get(reverse('consultation:index'))
        self.assertContains(res, 'サービス等利用計画（2026/6/11）')

    def test_feeds_support_plan_material(self):
        self._post()
        from support_plans import material
        m = material.collect(self.b, datetime.date(2026, 1, 1), datetime.date(2026, 12, 31))
        self.assertIn('サービス等利用計画（相談支援事業所テスト 2026/6/11）', m['service_plan'])
        self.assertIn('優先1：気持ちを言葉で伝えられる', m['service_plan'])
        self.assertIn('サービス等利用計画', m['sources'])

    @override_settings(ANTHROPIC_API_KEY='k')
    @mock.patch('consultation.ai.anthropic.Anthropic')
    def test_ai_draft(self, mock_cls):
        from support_plans.models import PlanGoal, SupportPlan
        from therapy.models import TherapyRecord
        TherapyRecord.objects.create(facility=self.f, beneficiary=self.b, date=datetime.date.today() - datetime.timedelta(days=3), body='平均台', situation='手をつなぐと渡れた')
        plan = SupportPlan.objects.create(facility=self.f, beneficiary=self.b, title='第1期', created_by=self.user)
        d = plan.get_step(2); d.policy = '児発の方針'; d.save()
        PlanGoal.objects.create(plan=plan, goal_type='short', content='要求を言葉で伝える')
        cp = ConsultationPlan.objects.create(facility=self.f, beneficiary=self.b, created_by=self.user)
        mock_cls.return_value.messages.create.return_value = SimpleNamespace(content=[SimpleNamespace(type='text', text=json.dumps({
            'overview': '概要の文', 'child_state': {'meal': '食事の文', 'toilet': ''}, 'complaint_child': '', 'complaint_family': '家族の主訴', 'wishes': '意向',
            'policy': '方針', 'long_goal': '長期', 'short_goal': '短期', 'life_summary': '全体像',
            'rows': [{'priority': '', 'issue': '課題1', 'goal': '目標1', 'timing': '1年', 'service': '児童発達支援', 'provider': 'センター', 'role': '参加', 'eval_timing': '6ヶ月', 'notes': ''}]},
            ensure_ascii=False))])
        res = self.client.post(reverse('consultation:ai_draft', args=[cp.pk]), {'months': 6})
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()['draft']
        self.assertEqual((data['overview'], data['child_state']['meal'], data['rows'][0]['priority'], data['rows'][0]['goal']), ('概要の文', '食事の文', '1', '目標1'))
        self.assertEqual(data['sources'], ['療育記録 1 件', '児童発達支援の個別支援計画'])
        sent = mock_cls.return_value.messages.create.call_args.kwargs['messages'][0]['content']
        self.assertIn('児発の方針', sent)
        self.assertIn('要求を言葉で伝える', sent)
        self.assertIn('平均台', sent)
        page = self.client.get(reverse('consultation:edit', args=[cp.pk]))
        self.assertContains(page, 'id="cfAiBtn"')

    @override_settings(ANTHROPIC_API_KEY='k')
    @mock.patch('beneficiaries.knowledge.anthropic.Anthropic')
    def test_import_from_knowledge(self, client_cls):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from beneficiaries.models import BeneficiaryDocument
        doc = BeneficiaryDocument.objects.create(beneficiary=self.b, file_name='計画.pdf', file=SimpleUploadedFile('p.pdf', b'%PDF-1.4 x'))
        base = {'kind': 'サービス等利用計画', 'title': 'サービス等利用計画・障害児支援利用計画', 'doc_date': '2026-06-11', 'issuer': '相談支援事業所テスト',
                'name_on_doc': '試験 太郎', 'birth_on_doc': '2022-07-21', 'diagnosis': '', 'severe': 'unknown', 'summary': '6か月ごとのモニタリング。',
                'support_points': [], 'medical_notes': [], 'full_text': '',
                'wishes': ['（本児）ぬいぐるみが好き。運動が好き。', '（家族）自分の気持ちを言葉で伝えられるようになってほしい。'],
                'goals': ['優先1：気持ちを言葉で伝えられるようになる', '優先2：先生と一緒に楽しみながら課題に取り組む', '優先3：育児の負担軽減を図りたい'],
                'items': [{'label': '総合的な支援の方針', 'value': '本児の性格や特性を理解し、発達を促す。'}, {'label': '長期目標', 'value': '支援を受けながら興味を持って参加できる活動を見つける。'},
                          {'label': '短期目標', 'value': '本人のペースで事業所に通う。'}, {'label': 'モニタリング期間', 'value': '6ヶ月ごと（01月、07月）'},
                          {'label': '計画作成担当者', 'value': '若木'}, {'label': '通所受給者証番号', 'value': '1234567890'}],
                'entries': [], 'unreadable': ''}
        client_cls.return_value.messages.create.return_value = SimpleNamespace(stop_reason='end_turn', content=[SimpleNamespace(type='text', text=json.dumps(base, ensure_ascii=False))])
        self.client.post(reverse('beneficiaries:knowledge_read', args=[self.b.pk]), {'source': 'document', 'source_pk': doc.pk})
        k = BeneficiaryKnowledge.objects.get()
        page = self.client.get(reverse('beneficiaries:knowledge_review', args=[self.b.pk, k.pk]))
        self.assertContains(page, '相談支援の計画として登録する')
        res = self.client.post(reverse('beneficiaries:knowledge_review', args=[self.b.pk, k.pk]),
                               {'action': 'save', 'kind': k.kind, 'title': k.title, 'doc_date': '2026-06-11', 'issuer': k.issuer, 'summary': k.summary, 'points': '',
                                'text': '', 'use_in_ai': '1', 'wishes': '\n'.join(k.wishes_list), 'goals': '\n'.join(k.goals_list), 'apply_consultation': '1'}, follow=True)
        self.assertContains(res, '相談支援の計画「サービス等利用計画（2026/6/11）」')
        cp = ConsultationPlan.objects.get()
        self.assertEqual((cp.office_name, cp.planner, cp.monitoring_period, cp.child_cert_number, cp.plan_date),
                         ('相談支援事業所テスト', '若木', '6ヶ月ごと（01月、07月）', '1234567890', datetime.date(2026, 6, 11)))
        self.assertEqual(cp.policy, '本児の性格や特性を理解し、発達を促す。')
        self.assertIn('気持ちを言葉で伝えられるようになってほしい', cp.wishes)
        self.assertEqual([(r['priority'], r['goal']) for r in cp.rows], [('1', '気持ちを言葉で伝えられるようになる'), ('2', '先生と一緒に楽しみながら課題に取り組む'), ('3', '育児の負担軽減を図りたい')])
        self.assertEqual(cp.source_knowledge, k)
        # 基本情報の書類は同じ作成中の計画の空の欄に入る
        BeneficiaryKnowledge.objects.all().delete()
        base2 = dict(base, kind='相談支援の基本情報', title='申請者の現状', summary='概要の要約', wishes=['（家族）気持ちを言葉で'], goals=[],
                     items=[{'label': '概要', 'value': '出生後すぐ入院。2歳から療育。'}, {'label': '食事', 'value': 'スプーンは使える。'}, {'label': '生活歴', 'value': '阿南保育園に入園。'}])
        client_cls.return_value.messages.create.return_value = SimpleNamespace(stop_reason='end_turn', content=[SimpleNamespace(type='text', text=json.dumps(base2, ensure_ascii=False))])
        self.client.post(reverse('beneficiaries:knowledge_read', args=[self.b.pk]), {'source': 'document', 'source_pk': doc.pk})
        k2 = BeneficiaryKnowledge.objects.get()
        self.client.post(reverse('beneficiaries:knowledge_review', args=[self.b.pk, k2.pk]),
                         {'action': 'save', 'kind': k2.kind, 'title': k2.title, 'summary': k2.summary, 'points': '', 'text': '', 'use_in_ai': '1', 'apply_consultation': '1'}, follow=True)
        cp.refresh_from_db()
        self.assertEqual(ConsultationPlan.objects.count(), 1)
        self.assertEqual((cp.overview, cp.child_state['meal'], cp.history, cp.policy), ('出生後すぐ入院。2歳から療育。', 'スプーンは使える。', '阿南保育園に入園。', '本児の性格や特性を理解し、発達を促す。'))

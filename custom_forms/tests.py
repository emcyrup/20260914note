from datetime import date

from django.test import TestCase
from django.urls import reverse

from accounts.models import StaffAccount
from beneficiaries.models import Beneficiary
from facilities.models import Facility
from support_plans.models import PlanGoal, SupportPlan

from .models import AgencyMeetingReport, SpecializedSupportPlan


class CustomFormsTests(TestCase):
    """はぴねす様式：施設の設定で有効化、作成・PDF、他施設からの遮断"""

    def setUp(self):
        self.facility = Facility.objects.create(name='はぴねす', form_set=Facility.FORM_SET_HAPPINESS)
        self.user = StaffAccount.objects.create_user('staff', password='pass12345', facility=self.facility, display_name='山本')
        self.client.force_login(self.user)
        self.b = Beneficiary.objects.create(facility=self.facility, last_name='佐藤', first_name='はると', date_of_birth=date(2016, 4, 1), weekday_mon=True)

    def test_hidden_for_standard_facility(self):
        other = Facility.objects.create(name='標準の施設')
        u = StaffAccount.objects.create_user('std', password='pass12345', facility=other)
        self.client.force_login(u)
        res = self.client.get(reverse('facilities:dashboard'))
        self.assertNotContains(res, '事業所様式')
        res = self.client.get(reverse('custom_forms:index'))
        self.assertRedirects(res, reverse('reports:index'))

    def test_index_and_sidebar(self):
        res = self.client.get(reverse('facilities:dashboard'))
        self.assertContains(res, '事業所様式')
        res = self.client.get(reverse('custom_forms:index'))
        self.assertContains(res, '関係機関連携加算Ⅱ 報告書')
        self.assertContains(res, '専門的支援実施計画書')

    def test_meeting_create_and_pdf(self):
        res = self.client.post(reverse('custom_forms:meeting_add'), {
            'beneficiary': self.b.pk, 'date': '2026-06-18', 'start_time': '09:00', 'end_time': '10:00',
            'place': '明日香養護学校', 'format': 'face',
            'p_affiliation': ['明日香養護学校', '', 'はぴねす'], 'p_name': ['田中', '', '山本'],
            'purpose': '進級後の支援方針の共有', 'result': '学校での様子を確認した。', 'opinions': '家庭でも同じ声かけを。', 'policy': '声かけを統一する。',
            'recorder': self.user.pk,
        })
        self.assertRedirects(res, reverse('custom_forms:index'))
        m = AgencyMeetingReport.objects.get(beneficiary=self.b)
        self.assertEqual(m.time_range, '09:00～10:00')
        self.assertEqual(len(m.participants), 2)
        self.assertEqual(len(m.participant_rows()), 4)
        res = self.client.get(reverse('custom_forms:meeting_pdf', args=[m.pk]) + '?fmt=html')
        self.assertContains(res, '関係機関連携加算Ⅱ　報告書')
        self.assertContains(res, '明日香養護学校')
        self.assertContains(res, '放課後等デイサービス　はぴねす')
        res = self.client.get(reverse('custom_forms:meeting_pdf', args=[m.pk]))
        self.assertEqual(res['Content-Type'], 'application/pdf')
        # 他施設からは 404
        other = Facility.objects.create(name='別', form_set=Facility.FORM_SET_HAPPINESS)
        self.client.force_login(StaffAccount.objects.create_user('o', password='pass12345', facility=other))
        self.assertEqual(self.client.get(reverse('custom_forms:meeting_pdf', args=[m.pk])).status_code, 404)

    def test_specialized_create_and_pdf(self):
        res = self.client.post(reverse('custom_forms:specialized_add'), {
            'beneficiary': self.b.pk, 'period_start': '2026-04-01', 'period_end': '2026-09-30', 'wishes': '歩けるように',
            'rom_parts': ['肩', '膝'], 'weak_parts': ['下肢'], 'balance': 'yes', 'muscle_tone': 'high',
            'move_rolling': 'independent', 'move_standing': 'partial', 'abms_neck': '3', 'abms_t_stairs': '1',
            'key_areas': '立位', 'goals': '10分立位保持', 'support_items': ['ストレッチング', '歩行訓練', '存在しない'],
            'support_other': '水中', 'implementation': '週2回', 'explained_date': '2026-04-01', 'explained_to': '本人、家族（母）', 'explained_by': self.user.pk,
        })
        self.assertRedirects(res, reverse('custom_forms:index'))
        s = SpecializedSupportPlan.objects.get(beneficiary=self.b)
        self.assertEqual(s.rom_parts, ['肩', '膝'])
        self.assertEqual(s.support_items, ['ストレッチング', '歩行訓練'])
        self.assertEqual(s.abms['neck'], '3')
        self.assertEqual(s.movement_rows()[0], ('寝返り', '自立'))
        res = self.client.get(reverse('custom_forms:specialized_pdf', args=[s.pk]) + '?fmt=html')
        self.assertContains(res, '専門的支援実施計画書')
        self.assertContains(res, '10分立位保持')
        self.assertContains(res, 'class="chk on">肩<')
        res = self.client.get(reverse('custom_forms:specialized_pdf', args=[s.pk]))
        self.assertEqual(res['Content-Type'], 'application/pdf')

    def test_plan_extra_and_plan_forms(self):
        plan = SupportPlan.objects.create(facility=self.facility, beneficiary=self.b, title='第1期', manager=self.user)
        d = plan.get_step(SupportPlan.STEP_DRAFT)
        d.policy = '安心して過ごせる場をつくる'
        d.family_wishes = '友だちと遊べるように'
        d.period_start, d.period_end = date(2026, 4, 1), date(2027, 3, 31)
        d.save()
        g1 = PlanGoal.objects.create(plan=plan, goal_type=PlanGoal.TYPE_LONG, content='友だちと一緒に活動できる', target_date=date(2027, 3, 1))
        g2 = PlanGoal.objects.create(plan=plan, goal_type=PlanGoal.TYPE_SHORT, content='順番を待てる', support_content='声かけと視覚支援', frequency='毎回', target_date=date(2026, 9, 30))
        res = self.client.post(reverse('custom_forms:plan_extra', args=[plan.pk]), {
            'usage_form': '放課後等デイサービス（月曜 週1回）', 'specialists': '理学療法士', 'medical_care': '主治医連携：なし',
            f'g{g1.pk}_category': 'self_social', f'g{g1.pk}_item': '社会性', f'g{g1.pk}_staff': '山本',
            f'g{g2.pk}_category': 'nope', f'g{g2.pk}_procedure': '絵カードを使う', f'g{g2.pk}_criteria': '3回中2回',
        })
        self.assertRedirects(res, reverse('support_plans:detail', args=[plan.pk]))
        plan.refresh_from_db(); g1.refresh_from_db(); g2.refresh_from_db()
        self.assertEqual(plan.form_extra['specialists'], '理学療法士')
        self.assertEqual(g1.form_extra['category'], 'self_social')
        self.assertEqual(g2.form_extra['category'], '')

        res = self.client.get(reverse('custom_forms:plan_sheet1', args=[plan.pk]) + '?fmt=html')
        self.assertContains(res, '個別支援計画書')
        self.assertContains(res, '安心して過ごせる場をつくる')
        self.assertContains(res, '順番を待てる')
        self.assertContains(res, '声かけと視覚支援（毎回）')
        self.assertContains(res, '月曜日')
        res = self.client.get(reverse('custom_forms:plan_detail', args=[plan.pk]) + '?fmt=html')
        self.assertContains(res, '放課後等デイサービス 個別支援計画書')
        self.assertContains(res, '理学療法士')
        self.assertContains(res, '友だちと一緒に活動できる')
        self.assertContains(res, '絵カードを使う')
        self.assertContains(res, '（未分類）')
        for name in ('plan_sheet1', 'plan_detail'):
            res = self.client.get(reverse(f'custom_forms:{name}', args=[plan.pk]))
            self.assertEqual(res['Content-Type'], 'application/pdf', name)
        # 計画詳細に様式ボタンが出る
        res = self.client.get(reverse('support_plans:detail', args=[plan.pk]))
        self.assertContains(res, '計画書（別紙1）')

    def test_feature_settings_saves_form_set(self):
        admin = StaffAccount.objects.create_user('adm', password='pass12345', facility=self.facility, role=StaffAccount.ROLE_ADMIN)
        self.client.force_login(admin)
        self.client.post(reverse('facilities:feature_settings'), {'use_billing': 'on', 'journal_sections': ['activity'], 'form_set': 'standard'})
        self.facility.refresh_from_db()
        self.assertEqual(self.facility.form_set, 'standard')
        self.client.post(reverse('facilities:feature_settings'), {'journal_sections': ['activity'], 'form_set': 'bogus'})
        self.facility.refresh_from_db()
        self.assertEqual(self.facility.form_set, 'standard')

from datetime import date

from django.test import TestCase
from django.urls import reverse

from accounts.models import StaffAccount
from beneficiaries.models import Beneficiary
from facilities.models import Facility
from support_plans.models import PlanGoal, SupportPlan

from .models import AgencyMeetingReport, SpecializedSupportPlan


class CustomFormsTests(TestCase):
    """はびりす様式：施設の設定で有効化、作成・PDF、他施設からの遮断"""

    def setUp(self):
        self.facility = Facility.objects.create(name='はびりす', form_set=Facility.FORM_SET_HAPPINESS)
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
            'p_affiliation': ['明日香養護学校', '', 'はびりす'], 'p_name': ['田中', '', '山本'],
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
        self.assertContains(res, '放課後等デイサービス　はびりす')
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
        # 事業所の管理者は帳票様式を変えられない（開発向けユーザーのみ）
        admin = StaffAccount.objects.create_user('adm', password='pass12345', facility=self.facility, role=StaffAccount.ROLE_ADMIN)
        self.client.force_login(admin)
        self.client.post(reverse('facilities:feature_settings'), {'use_billing': 'on', 'journal_sections': ['activity'], 'form_set': 'standard'})
        self.facility.refresh_from_db()
        self.assertEqual(self.facility.form_set, 'happiness')
        dev = StaffAccount.objects.create_user('dev', password='pass12345', facility=self.facility, role=StaffAccount.ROLE_ADMIN, is_developer=True)
        self.client.force_login(dev)
        self.client.post(reverse('facilities:feature_settings'), {'use_billing': 'on', 'journal_sections': ['activity'], 'form_set': 'standard'})
        self.facility.refresh_from_db()
        self.assertEqual(self.facility.form_set, 'standard')
        self.client.post(reverse('facilities:feature_settings'), {'journal_sections': ['activity'], 'form_set': 'bogus'})
        self.facility.refresh_from_db()
        self.assertEqual(self.facility.form_set, 'standard')


class SeedCustomFormsTests(TestCase):
    """seed_demo：はびりす様式の施設には事業所様式のサンプルも入る"""

    def test_seed_creates_custom_forms(self):
        from io import StringIO
        from django.core.management import call_command
        f = Facility.objects.create(name='はびりす', form_set=Facility.FORM_SET_HAPPINESS)
        StaffAccount.objects.create_user('h', password='pass12345', facility=f)
        out = StringIO()
        call_command('seed_demo', '--facility', f.pk, stdout=out)
        self.assertEqual(AgencyMeetingReport.objects.filter(facility=f).count(), 2)
        self.assertEqual(SpecializedSupportPlan.objects.filter(facility=f).count(), 2)
        self.assertTrue(SupportPlan.objects.filter(facility=f).exclude(form_extra={}).exists())
        self.assertTrue(PlanGoal.objects.filter(plan__facility=f).exclude(form_extra={}).exists())
        self.assertIn('専門的支援実施計画書', out.getvalue())
        # 標準の施設には入らない
        g = Facility.objects.create(name='標準')
        call_command('seed_demo', '--facility', g.pk, stdout=StringIO())
        self.assertEqual(AgencyMeetingReport.objects.filter(facility=g).count(), 0)


class OwlFormsTests(TestCase):
    """オウル様式：個別支援計画（計画書・別表）の出力と別表の入力。様式のまま編集の欄がオウルの並びになる。他の様式からは遮断"""

    def setUp(self):
        self.facility = Facility.objects.create(name='児童発達支援センター　オウル', form_set=Facility.FORM_SET_OWL)
        self.user = StaffAccount.objects.create_user('owl', password='pass12345', facility=self.facility, display_name='太田', role=StaffAccount.ROLE_ADMIN)
        self.client.force_login(self.user)
        self.b = Beneficiary.objects.create(facility=self.facility, last_name='試験', first_name='太郎', date_of_birth=date(2022, 7, 1),
                                            weekday_mon=True, weekday_wed=True, weekday_fri=True)
        self.plan = SupportPlan.objects.create(facility=self.facility, beneficiary=self.b, title='第1期', created_by=self.user, manager=self.user,
                                               current_step=SupportPlan.STEP_DRAFT)
        d = self.plan.get_step(2)
        d.period_start, d.period_end, d.policy, d.family_wishes = date(2026, 4, 23), date(2026, 10, 22), '方針の文', '意向の文'
        d.save()
        PlanGoal.objects.create(plan=self.plan, goal_type='long', content='いろいろな活動に挑戦できる', target_date=date(2026, 10, 22))
        PlanGoal.objects.create(plan=self.plan, goal_type='short', content='安心した環境の中で興味の幅を広げる', support_content='安心して過ごせる環境を整え…',
                                target_date=date(2026, 10, 22), form_extra={'item': '発達支援', 'timing': '6か月', 'staff': 'オウル職員', 'notes': '健康・生活', 'priority': '2'})
        PlanGoal.objects.create(plan=self.plan, goal_type='short', content='要求を言葉で伝える', support_content='言葉を添える', target_date=date(2026, 10, 22),
                                form_extra={'item': '発達支援', 'timing': '6か月', 'staff': 'オウル職員', 'priority': '1'})

    def test_index_sidebar_and_gating(self):
        res = self.client.get(reverse('custom_forms:index'))
        self.assertContains(res, 'オウル様式')
        self.assertContains(res, reverse('custom_forms:plan_owl', args=[self.plan.pk]))
        self.assertNotContains(res, '関係機関連携加算Ⅱ 報告書')
        # はびりすだけの画面は開けない
        self.assertRedirects(self.client.get(reverse('custom_forms:plan_sheet1', args=[self.plan.pk])), reverse('reports:index'))
        # はびりすの施設からオウルの画面は開けない
        other = Facility.objects.create(name='はびりす', form_set=Facility.FORM_SET_HAPPINESS)
        u = StaffAccount.objects.create_user('h', password='pass12345', facility=other)
        self.client.force_login(u)
        self.assertRedirects(self.client.get(reverse('custom_forms:plan_owl', args=[self.plan.pk])), reverse('reports:index'))

    def test_plan_owl_html(self):
        res = self.client.get(reverse('custom_forms:plan_owl', args=[self.plan.pk]) + '?fmt=html')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, '試験 太郎さんの個別支援計画')
        self.assertContains(res, '利用児及び家族の<br>生活に対する意向')
        self.assertContains(res, '方針の文')
        self.assertContains(res, '・いろいろな活動に挑戦できる（2026年10月まで）')
        self.assertContains(res, '2026年04月23日')
        self.assertContains(res, '2026年10月22日')
        self.assertContains(res, '月・水・金曜日')
        # 表は優先順位の順
        body = res.content.decode()
        table = body[body.index('優先<br>順位'):]
        self.assertLess(table.index('要求を言葉で伝える'), table.index('安心した環境の中で興味の幅を広げる'))
        self.assertContains(res, '児童発達支援管理責任者')
        self.assertContains(res, '太田')

    def test_table_edit_and_html(self):
        url = reverse('custom_forms:plan_owl_table_edit', args=[self.plan.pk])
        res = self.client.get(url)
        self.assertContains(res, '提供時間（曜日ごと）')
        self.assertContains(res, '専門的支援実施加算')
        res = self.client.post(url, {'w_mon_start': '10時00分', 'w_mon_end': '10時40分', 'w_mon_hours': '0時間40分', 'w_wed_start': '10時00分',
                                     'addons': ['専門的支援実施加算', '送迎加算', 'でたらめ'], 'e_mon_before': '', 'ext_reason': '',
                                     'slots_note': '時間枠の案内', 'service_name': '児童発達支援', 'cert_number': '1234567890', 'revision': '2', 'next': 'pdf'})
        self.assertEqual(res.status_code, 302)
        self.plan.refresh_from_db()
        x = self.plan.form_extra
        self.assertEqual(x['weekly']['mon'], {'start': '10時00分', 'end': '10時40分', 'hours': '0時間40分'})
        self.assertEqual(x['addons'], ['専門的支援実施加算', '送迎加算'])
        self.assertNotIn('fri', x['weekly'])
        res = self.client.get(reverse('custom_forms:plan_owl_table', args=[self.plan.pk]) + '?fmt=html')
        self.assertContains(res, '個別支援計画別表')
        self.assertContains(res, '10時00分〜10時40分')
        self.assertContains(res, 'class="on">・専門的支援実施加算')
        self.assertContains(res, '時間枠の案内')
        res = self.client.get(reverse('custom_forms:plan_owl', args=[self.plan.pk]) + '?fmt=html')
        self.assertContains(res, '1234567890')
        self.assertContains(res, '児童発達支援')
        self.assertContains(res, '<td class="c">2</td>')

    def test_sheet_editor_uses_owl_cells(self):
        res = self.client.get(reverse('support_plans:sheet', args=[self.plan.pk]))
        self.assertContains(res, '項目<br>（本人のニーズ等）')
        self.assertContains(res, '具体的な達成目標')
        self.assertContains(res, 'name="f_transport"')
        self.assertContains(res, 'name="f_long_goal_text"')
        self.assertContains(res, '優先<br>順位')
        self.assertContains(res, '別表（提供時間・加算）')
        g = self.plan.goals.get(content='要求を言葉で伝える')
        self.assertContains(res, f'name="g{g.pk}_priority" value="1"')
        # 保存：オウルの欄が form_extra と目標に入る
        d = self.plan.get_step(2)
        post = {'f_family_wishes': '意向', 'f_policy': '方針', 'f_long_goal_text': '長期の文', 'f_short_goal_text': '短期の文', 'f_service_hours': '週3回',
                'f_transport': '迎え：あり', 'f_notes': '備考'}
        for goal in self.plan.goals.all():
            p = f'g{goal.pk}_'
            e = goal.form_extra or {}
            post.update({p + 'type': goal.goal_type, p + 'content': goal.content, p + 'support_content': goal.support_content, p + 'frequency': '',
                         p + 'item': e.get('item', ''), p + 'timing': e.get('timing', ''), p + 'staff': e.get('staff', ''), p + 'notes': e.get('notes', ''),
                         p + 'priority': '3' if goal.content == '要求を言葉で伝える' else e.get('priority', ''), p + 'target_date': '2026-10-22'})
        res = self.client.post(reverse('support_plans:sheet', args=[self.plan.pk]), post)
        self.assertEqual(res.status_code, 302)
        self.plan.refresh_from_db(); d.refresh_from_db(); g.refresh_from_db()
        self.assertEqual((self.plan.form_extra['transport'], self.plan.form_extra['long_goal_text'], d.notes, g.form_extra['priority']), ('迎え：あり', '長期の文', '備考', '3'))
        res = self.client.get(reverse('custom_forms:plan_owl', args=[self.plan.pk]) + '?fmt=html')
        self.assertContains(res, '長期の文')
        self.assertContains(res, '迎え：あり')

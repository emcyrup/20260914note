"""計画書中心の画面（シンプル）のテスト"""
import datetime

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from accounts.models import StaffAccount
from beneficiaries.models import Beneficiary, Guardian
from facilities.models import Facility
from support_plans.models import SupportPlan

from . import services
from .models import ContactNote, Interview


class _Base(TestCase):
    def setUp(self):
        self.facility = Facility.objects.create(name='シンプル', layout=Facility.LAYOUT_PLANBOOK, use_line=True)
        self.user = StaffAccount.objects.create_user(username='simple', password='pw12345678', facility=self.facility,
                                                     role=StaffAccount.ROLE_ADMIN)
        self.client.force_login(self.user)
        self.ben = Beneficiary.objects.create(facility=self.facility, last_name='てすと', first_name='じどう',
                                              last_name_kana='てすと', first_name_kana='じどう', date_of_birth='2018-09-09')
        self.guardian = Guardian.objects.create(beneficiary=self.ben, last_name='てすと', first_name='ほごしゃ',
                                                relation='father', phone='08000000000', is_primary=True)


class LayoutTests(_Base):
    def test_home_redirects_to_students_and_nav_has_six_items(self):
        res = self.client.get('/', follow=True)
        self.assertEqual(res.redirect_chain[-1][0], reverse('planbook:students'))
        html = res.content.decode()
        for label in ('完了期日一覧', 'スタッフ', '保護者', '連絡帳', '施設'):
            self.assertIn(label, html)
        self.assertIn('layout-planbook', html)
        self.assertNotIn('請求マトリックス', html)

    def test_standard_facility_keeps_standard_nav(self):
        self.facility.layout = Facility.LAYOUT_STANDARD
        self.facility.save()
        res = self.client.get('/')
        self.assertEqual(res.status_code, 200)
        self.assertNotIn('layout-planbook', res.content.decode())

    def test_all_list_pages_render(self):
        for name in ('students', 'student_create', 'deadlines', 'staff', 'guardians', 'notes', 'facility', 'student_import'):
            res = self.client.get(reverse(f'planbook:{name}'))
            self.assertEqual(res.status_code, 200, name)

    def test_other_facility_student_is_404(self):
        other = Facility.objects.create(name='別')
        b = Beneficiary.objects.create(facility=other, last_name='他', first_name='子', date_of_birth='2015-01-01')
        self.assertEqual(self.client.get(reverse('planbook:student', args=[b.pk])).status_code, 404)


class ProfileTests(_Base):
    def test_create_and_profile_save_with_certificate(self):
        res = self.client.post(reverse('planbook:student_create'), {
            'last_name': '山田', 'first_name': '花', 'gender': 'female', 'date_of_birth': '2017-04-01', 'status': 'active'})
        b = Beneficiary.objects.get(last_name='山田')
        self.assertRedirects(res, reverse('planbook:student', args=[b.pk]))
        res = self.client.post(reverse('planbook:student', args=[b.pk]), {
            'last_name': '山田', 'first_name': '花', 'gender': 'female', 'date_of_birth': '2017-04-01', 'status': 'active',
            'cert_number': '2600001234', 'cert_until': '2027-03-31', 'postal_code': '6008216', 'school_name': '○○小学校',
            'grade': 'e3', 'admission_date': '2026-04-01', 'has_prior_records': 'on'})
        self.assertEqual(res.status_code, 302)
        b.refresh_from_db()
        self.assertEqual(b.grade, 'e3')
        self.assertTrue(b.has_prior_records)
        cert = b.latest_certificate
        self.assertEqual(cert.certificate_number, '2600001234')
        self.assertEqual(cert.valid_until, datetime.date(2027, 3, 31))
        res = self.client.get(reverse('planbook:student', args=[b.pk]))
        self.assertContains(res, '2600001234')
        self.assertContains(res, '2027/03/31')

    def test_delete_without_records_removes_and_with_records_discharges(self):
        pk = self.ben.pk
        self.client.post(reverse('planbook:student_delete', args=[pk]))
        self.assertFalse(Beneficiary.objects.filter(pk=pk).exists())
        b = Beneficiary.objects.create(facility=self.facility, last_name='記録', first_name='あり', date_of_birth='2015-01-01')
        services.create_period(b, self.user)
        self.client.post(reverse('planbook:student_delete', args=[b.pk]))
        b.refresh_from_db()
        self.assertEqual(b.status, Beneficiary.STATUS_INACTIVE)


class PlanFlowTests(_Base):
    def _plan(self):
        self.client.post(reverse('planbook:period_create', args=[self.ben.pk]))
        return SupportPlan.objects.get(beneficiary=self.ben)

    def test_period_creates_plan_and_interview(self):
        plan = self._plan()
        self.assertEqual(plan.title, '第1期 個別支援計画')
        self.assertTrue(Interview.objects.filter(plan=plan).exists())
        self.assertEqual(services.period_number(plan), 1)
        plan2 = services.create_period(self.ben, self.user)
        self.assertEqual(services.period_number(plan2), 2)
        self.assertEqual(plan2.predecessor_id, plan.pk)

    def test_interview_save_goals_schedule_and_complete_locks(self):
        plan = self._plan()
        url = reverse('planbook:plan_tab', args=[self.ben.pk, plan.pk, 'interview'])
        data = {'period_start': '2026-10-01', 'period_end': '2027-03-31', 'interview_date': '2026-09-19',
                'created_date': '2026-09-19', 'author': str(self.user.pk), 'participants': [str(self.user.pk)],
                'home_parent': '家では落ち着いている', 'future_wishes': '友だちと遊べるように',
                'goal_idx': ['new-1', 'new-2'], 'goal_new-1_category': 'self', 'goal_new-1_priority': '1',
                'goal_new-1_staff': '児童指導員', 'goal_new-1_content': '順番を待てる', 'goal_new-1_target': '5分待てる',
                'goal_new-1_support': '絵カードで見通し', 'goal_new-1_timing': '3か月', 'goal_new-1_eval_timing': '月末',
                'goal_new-1_domains': ['cognition', 'social'], 'goal_new-2_content': 'あいさつ', 'goal_new-2_support': 'モデル',
                'sched_mon_start': '15:00', 'sched_mon_end': '17:30', 'sched_mon_pickup': '学校', 'notes': '特記'}
        res = self.client.post(url, data)
        self.assertEqual(res.status_code, 302)
        iv = Interview.objects.get(plan=plan)
        self.assertEqual(iv.period_end, datetime.date(2027, 3, 31))
        self.assertEqual(iv.schedule['mon']['pickup'], '学校')
        self.assertEqual(iv.schedule_rows()[0]['minutes'], 150)
        self.assertEqual(iv.missing_for_completion(), [])
        goals = list(plan.goals.all())
        self.assertEqual(len(goals), 2)
        x = services.goal_extra(goals[0])
        self.assertEqual(x['category_label'], '本人支援')
        self.assertEqual(x['target'], '5分待てる')
        self.assertEqual(x['domains'], ['cognition', 'social'])
        # 次回更新日＝支援期間の終了。標準画面のアセスメントにも写る
        self.assertEqual(services.next_update_date(plan), datetime.date(2027, 3, 31))
        self.assertEqual(plan.get_step(1).interview_date, datetime.date(2026, 9, 19))
        # 完了 → ロック → 保存できない
        res = self.client.post(reverse('planbook:stage_complete', args=[plan.pk, 'interview']))
        self.assertRedirects(res, reverse('planbook:plan_tab', args=[self.ben.pk, plan.pk, 'draft']))
        plan.refresh_from_db()
        iv.refresh_from_db()
        self.assertTrue(iv.is_completed)
        self.assertEqual(plan.current_step, 2)
        self.assertEqual(services.stage_status(plan)['interview']['state'], 'done')
        self.client.post(url, {**data, 'home_parent': '変更'})
        iv.refresh_from_db()
        self.assertEqual(iv.home_parent, '家では落ち着いている')
        res = self.client.get(url)
        self.assertContains(res, 'ロックを外して修正')
        # ロックを外す
        self.client.post(reverse('planbook:stage_reopen', args=[plan.pk, 'interview']))
        iv.refresh_from_db()
        plan.refresh_from_db()
        self.assertFalse(iv.is_completed)
        self.assertEqual(plan.current_step, 1)

    def test_interview_complete_requires_fields(self):
        plan = self._plan()
        res = self.client.post(reverse('planbook:stage_complete', args=[plan.pk, 'interview']), follow=True)
        self.assertContains(res, '支援期間')
        self.assertFalse(Interview.objects.get(plan=plan).is_completed)

    def test_full_flow_to_active_and_deadline_list(self):
        plan = self._plan()
        self.client.post(reverse('planbook:plan_tab', args=[self.ben.pk, plan.pk, 'interview']), {
            'period_start': '2026-10-01', 'period_end': '2027-03-31', 'interview_date': '2026-09-19', 'created_date': '2026-09-19',
            'author': str(self.user.pk), 'participants': [str(self.user.pk)], 'action': 'complete'})
        self.client.post(reverse('planbook:plan_tab', args=[self.ben.pk, plan.pk, 'draft']), {
            'period_start': '2026-10-01', 'period_end': '2027-03-31', 'policy': '方針', 'goal_idx': ['new-1'],
            'goal_new-1_content': '目標', 'goal_new-1_support': '支援', 'action': 'complete'})
        self.client.post(reverse('planbook:plan_tab', args=[self.ben.pk, plan.pk, 'meeting']), {
            'meeting_date': '2026-09-20', 'attendees': '職員', 'opinions': '原案どおり', 'action': 'complete'})
        self.client.post(reverse('planbook:plan_tab', args=[self.ben.pk, plan.pk, 'plan']), {
            'explained_date': '2026-09-21', 'explained_to': '母', 'consent_method': 'paper', 'consent_date': '2026-09-21',
            'consent_signer': '母', 'delivered_to_user_date': '2026-09-21', 'delivered_to_office_date': '2026-09-21',
            'service_start_date': '2026-10-01', 'action': 'complete'})
        plan.refresh_from_db()
        self.assertEqual(plan.current_step, SupportPlan.STEP_MONITORING)
        self.assertEqual(plan.status, SupportPlan.STATUS_ACTIVE)
        st = services.stage_status(plan)
        self.assertEqual([st[k]['state'] for k in ('interview', 'draft', 'meeting', 'plan')], ['done'] * 4)
        self.client.post(reverse('planbook:monitoring_add', args=[plan.pk]), {
            'date': '2026-12-01', 'conducted_by': str(self.user.pk), 'implementation': '計画どおり', 'achievement': 'progressing'})
        res = self.client.get(reverse('planbook:deadlines'))
        self.assertContains(res, '2026/12/01')
        self.assertContains(res, '2027/03/31')
        res = self.client.get(reverse('planbook:students'))
        self.assertContains(res, '完了')
        # 計画書の印刷（標準画面の様式）も開ける
        self.assertEqual(self.client.get(reverse('support_plans:print', args=[plan.pk])).status_code, 200)

    def test_plan_consent_complete_requires_fields(self):
        plan = self._plan()
        res = self.client.post(reverse('planbook:stage_complete', args=[plan.pk, 'plan']), follow=True)
        self.assertContains(res, '完了するには')
        plan.refresh_from_db()
        self.assertEqual(plan.status, SupportPlan.STATUS_IN_PROGRESS)


class NoteTests(_Base):
    def test_staff_note_without_line_is_recorded(self):
        res = self.client.post(reverse('planbook:note_thread', args=[self.guardian.pk]), {'body': '今日は元気でした'})
        self.assertEqual(res.status_code, 302)
        n = ContactNote.objects.get()
        self.assertEqual(n.sender, ContactNote.FROM_STAFF)
        self.assertFalse(n.line_sent)
        self.assertEqual(n.author, self.user)

    def test_guardian_line_message_is_unread_until_opened(self):
        services.record_guardian_line(self.facility, self.guardian, 'ありがとうございます')
        self.assertEqual(services.unread_note_count(self.facility), 1)
        res = self.client.get(reverse('planbook:notes'))
        self.assertContains(res, 'ありがとうございます')
        self.client.get(reverse('planbook:note_thread', args=[self.guardian.pk]))
        self.assertEqual(services.unread_note_count(self.facility), 0)

    def test_line_send_when_linked(self):
        from unittest import mock
        self.guardian.mark_line_linked('U123')
        self.guardian.save()
        self.facility.line_channel_access_token = 'token'
        self.facility.save()
        with mock.patch('line_integration.sending.push_text', return_value=(True, '')) as push:
            self.client.post(reverse('planbook:note_thread', args=[self.guardian.pk]), {'body': '明日は休みです'})
        push.assert_called_once()
        self.assertTrue(ContactNote.objects.get().line_sent)


class ImportTests(_Base):
    def test_template_and_csv_import(self):
        res = self.client.get(reverse('planbook:student_import_template'))
        self.assertEqual(res.status_code, 200)
        f = SimpleUploadedFile('s.csv', services.import_template_csv().encode('utf-8-sig'), content_type='text/csv')
        res = self.client.post(reverse('planbook:student_import'), {'file': f})
        self.assertRedirects(res, reverse('planbook:students'))
        b = Beneficiary.objects.get(last_name='山田', first_name='太郎')
        self.assertEqual(b.grade, 'e3')
        self.assertEqual(b.latest_certificate.certificate_number, '2600001234')
        self.assertEqual(b.guardians.get().relation, 'mother')

    def test_shift_jis_and_errors(self):
        text = '姓,名,生年月日\n鈴木,一郎,2016-05-05\n,,2016-05-05\n佐藤,二郎,不明\n'
        f = SimpleUploadedFile('s.csv', text.encode('cp932'), content_type='text/csv')
        res = self.client.post(reverse('planbook:student_import'), {'file': f}, follow=True)
        msgs = [str(m) for m in res.context['messages']]
        self.assertTrue(any('1 名' in m for m in msgs))
        self.assertTrue(any('3 行目' in m for m in msgs))
        self.assertTrue(any('4 行目' in m for m in msgs))


class FacilityInfoTests(_Base):
    def test_edit_company_fields(self):
        res = self.client.post(reverse('planbook:facility'), {
            'name': 'シンプル', 'postal_code': '6008216', 'address': '京都市', 'address2': '', 'phone': '075-000-0000',
            'company_name': 'BETTERA', 'representative_name': '代表', 'representative_email': 'rep@example.com'})
        self.assertEqual(res.status_code, 302)
        self.facility.refresh_from_db()
        self.assertEqual(self.facility.company_name, 'BETTERA')
        res = self.client.get(reverse('planbook:facility'))
        self.assertContains(res, 'rep@example.com')

    def test_staff_cannot_edit(self):
        staff = StaffAccount.objects.create_user(username='s2', password='pw12345678', facility=self.facility)
        self.client.force_login(staff)
        self.client.post(reverse('planbook:facility'), {'name': 'かえた', 'postal_code': '', 'address': '', 'address2': '',
                                                        'phone': '', 'company_name': '', 'representative_name': '', 'representative_email': ''})
        self.facility.refresh_from_db()
        self.assertEqual(self.facility.name, 'シンプル')


class CreateFacilityLayoutTests(TestCase):
    def test_command_sets_layout(self):
        from io import StringIO
        from django.core.management import call_command
        call_command('create_facility', 'シンプル', '--admin', 'simple', '--password', 'pw12345678', '--layout', 'planbook', stdout=StringIO())
        f = Facility.objects.get(name='シンプル')
        self.assertTrue(f.is_planbook)
        self.assertEqual(f.brand_color, '#6f8f4e')

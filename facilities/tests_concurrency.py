"""同時編集：版の確認（楽観ロック）と「編集中」の通知"""
from datetime import date

from django.test import TestCase
from django.urls import reverse

from accounts.models import StaffAccount
from beneficiaries.models import Beneficiary
from config.concurrency import version_token
from facilities.models import EditingSession, Facility
from records.models import DailyRecord
from support_plans.models import SupportPlan


class ConflictTests(TestCase):
    def setUp(self):
        self.f = Facility.objects.create(name='F')
        self.a = StaffAccount.objects.create_user('a', password='pass12345', facility=self.f, role=StaffAccount.ROLE_ADMIN, display_name='職員A')
        self.b = StaffAccount.objects.create_user('b', password='pass12345', facility=self.f, display_name='職員B')
        self.ben = Beneficiary.objects.create(facility=self.f, last_name='佐', first_name='藤', date_of_birth=date(2016, 4, 1))
        self.rec = DailyRecord.objects.create(facility=self.f, beneficiary=self.ben, date=date(2026, 9, 1), observation_text='最初')

    def _record_post(self, version, **extra):
        data = {'observation_text': extra.pop('text', '書き換え'), 'status': 'draft', 'version': version, 'journal_sections': []}
        data.update(extra)
        return self.client.post(reverse('records:update', args=[self.rec.pk]), data)

    def test_daily_record_stale_version_is_rejected(self):
        self.client.force_login(self.a)
        opened = version_token(self.rec)
        # B が先に保存
        self.rec.observation_text = 'Bの内容'; self.rec.save()
        res = self._record_post(opened)
        self.assertEqual(res.status_code, 302)
        self.rec.refresh_from_db()
        self.assertEqual(self.rec.observation_text, 'Bの内容')
        msgs = [str(m) for m in res.wsgi_request._messages]
        self.assertTrue(any('他の職員が' in m for m in msgs), msgs)
        # 上書きを明示すれば保存できる
        res = self._record_post(opened, force_save='1', text='Aで上書き')
        self.rec.refresh_from_db()
        self.assertEqual(self.rec.observation_text, 'Aで上書き')
        # 最新の版なら普通に保存できる
        res = self._record_post(version_token(self.rec), text='最新で保存')
        self.rec.refresh_from_db()
        self.assertEqual(self.rec.observation_text, '最新で保存')

    def test_form_without_version_still_saves(self):
        # 古い画面や外部からの POST（version なし）は従来どおり
        self.client.force_login(self.a)
        self.client.post(reverse('records:update', args=[self.rec.pk]), {'observation_text': 'x', 'status': 'draft'})
        self.rec.refresh_from_db()
        self.assertEqual(self.rec.observation_text, 'x')

    def test_create_same_date_twice_is_blocked(self):
        self.client.force_login(self.a)
        url = reverse('records:create', args=[self.ben.pk])
        res = self.client.post(url, {'date': '2026-09-01', 'observation_text': '二重', 'status': 'draft', 'version': ''})
        self.assertEqual(res.status_code, 302)
        self.rec.refresh_from_db()
        self.assertEqual(self.rec.observation_text, '最初')
        self.assertIn(f'selected={self.rec.pk}', res['Location'])
        # 新しい日付は普通に作れる
        self.client.post(url, {'date': '2026-09-02', 'observation_text': '新規', 'status': 'draft', 'version': ''})
        self.assertTrue(DailyRecord.objects.filter(beneficiary=self.ben, date=date(2026, 9, 2)).exists())

    def test_plan_step_conflict_rerenders_with_banner(self):
        plan = SupportPlan.objects.create(facility=self.f, beneficiary=self.ben, title='計画', created_by=self.a)
        step = plan.get_step(1)
        self.client.force_login(self.a)
        opened = version_token(step)
        step.condition = 'Bが先に保存'; step.save()
        url = reverse('support_plans:step', args=[plan.pk, 1])
        res = self.client.post(url, {'interview_date': '2026-09-01', 'condition': 'Aの入力', 'version': opened, 'action': 'save'})
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'force_save')
        self.assertContains(res, 'Aの入力')          # 入力は保持される
        step.refresh_from_db()
        self.assertEqual(step.condition, 'Bが先に保存')
        res = self.client.post(url, {'interview_date': '2026-09-01', 'condition': 'Aの入力', 'version': opened, 'action': 'save', 'force_save': '1'})
        self.assertEqual(res.status_code, 302)
        step.refresh_from_db()
        self.assertEqual(step.condition, 'Aの入力')

    def test_beneficiary_conflict(self):
        self.client.force_login(self.a)
        opened = version_token(self.ben)
        self.ben.notes = 'B'; self.ben.save()
        res = self.client.post(reverse('beneficiaries:update', args=[self.ben.pk]), {
            'last_name': '佐', 'first_name': '藤', 'date_of_birth': '2016-04-01', 'status': 'active', 'notes': 'A', 'version': opened})
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'force_save')
        self.ben.refresh_from_db()
        self.assertEqual(self.ben.notes, 'B')

    def test_facility_settings_conflict(self):
        self.client.force_login(self.a)
        opened = version_token(self.f)
        self.f.name = 'F2'; self.f.save()
        self.client.post(reverse('facilities:feature_settings'), {'use_billing': 'on', 'journal_sections': ['activity'], 'version': opened})
        self.f.refresh_from_db()
        self.assertTrue(self.f.use_line)   # 保存されていない（既定 True のまま）


class EditingSessionTests(TestCase):
    def setUp(self):
        self.f = Facility.objects.create(name='F')
        self.g = Facility.objects.create(name='G')
        self.a = StaffAccount.objects.create_user('a', password='pass12345', facility=self.f, display_name='職員A')
        self.b = StaffAccount.objects.create_user('b', password='pass12345', facility=self.f, display_name='職員B')
        self.other = StaffAccount.objects.create_user('o', password='pass12345', facility=self.g)
        ben = Beneficiary.objects.create(facility=self.f, last_name='佐', first_name='藤', date_of_birth=date(2016, 4, 1))
        self.rec = DailyRecord.objects.create(facility=self.f, beneficiary=ben, date=date(2026, 9, 1))
        self.url = reverse('facilities:editing')

    def test_touch_shows_other_editors_only(self):
        self.client.force_login(self.a)
        res = self.client.post(self.url, {'action': 'touch', 'kind': 'daily_record', 'id': self.rec.pk})
        self.assertEqual(res.json()['others'], [])
        self.client.force_login(self.b)
        res = self.client.post(self.url, {'action': 'touch', 'kind': 'daily_record', 'id': self.rec.pk})
        self.assertEqual([o['name'] for o in res.json()['others']], ['職員A'])
        res = self.client.get(self.url, {'kind': 'daily_record', 'id': self.rec.pk})
        self.assertEqual(res.json()['version'], version_token(self.rec))
        self.assertEqual([o['name'] for o in res.json()['others']], ['職員A'])
        # A がやめると消える
        self.client.force_login(self.a)
        self.client.post(self.url, {'action': 'release', 'kind': 'daily_record', 'id': self.rec.pk})
        self.client.force_login(self.b)
        res = self.client.get(self.url, {'kind': 'daily_record', 'id': self.rec.pk})
        self.assertEqual(res.json()['others'], [])
        self.assertEqual(EditingSession.objects.count(), 1)

    def test_other_facility_and_unknown_kind_are_404(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url, {'kind': 'daily_record', 'id': self.rec.pk}).status_code, 404)
        self.client.force_login(self.a)
        self.assertEqual(self.client.get(self.url, {'kind': 'nope', 'id': self.rec.pk}).status_code, 404)
        self.assertEqual(self.client.get(self.url, {'kind': 'daily_record', 'id': 'x'}).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(self.url, {'kind': 'daily_record', 'id': self.rec.pk}).status_code, 302)

    def test_forms_carry_version_and_attribute(self):
        self.client.force_login(self.a)
        res = self.client.get(reverse('records:list', args=[self.rec.beneficiary_id]))
        self.assertContains(res, f'data-concurrency="daily_record:{self.rec.pk}"')
        self.assertContains(res, f'name="version" value="{version_token(self.rec)}"')
        self.assertContains(res, 'js/concurrency.js')

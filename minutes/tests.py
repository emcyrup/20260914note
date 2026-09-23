"""議事録のテスト"""
import datetime
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import StaffAccount
from facilities.models import Facility

from .models import KEEP, Minutes


class MinutesTests(TestCase):
    def setUp(self):
        self.f = Facility.objects.create(name='発達支援ルーム　ゆあーず', use_therapy_record=True)
        self.user = StaffAccount.objects.create_user('ryo', password='pw12345678', facility=self.f,
                                                     role=StaffAccount.ROLE_STAFF, display_name='永山')
        self.client.login(username='ryo', password='pw12345678')
        self.url = reverse('minutes:index')

    def test_page_and_sidebar_link_opens_new_tab(self):
        res = self.client.get(self.url)
        self.assertContains(res, 'data-voice-target="transcript"')
        self.assertContains(res, 'id="mn-organize"')
        self.assertContains(res, f'href="{self.url}" target="_blank"')

    def test_create_edit_delete(self):
        res = self.client.post(self.url, {'title': '職員会議', 'held_on': '2026-09-24', 'transcript': 'えー、来月の行事について', 'summary': ''})
        m = Minutes.objects.get()
        self.assertRedirects(res, f'{self.url}?id={m.pk}')
        self.assertEqual((m.title, m.held_on, m.created_by), ('職員会議', datetime.date(2026, 9, 24), self.user))
        res = self.client.get(f'{self.url}?id={m.pk}')
        self.assertContains(res, '来月の行事について')
        self.client.post(self.url, {'id': m.pk, 'title': '', 'held_on': 'bad', 'transcript': 'x', 'summary': '【概要】\n行事'})
        m.refresh_from_db()
        self.assertEqual(m.summary, '【概要】\n行事')
        self.assertTrue(m.title.endswith('の記録'))     # 件名が空なら日付から付ける
        self.assertEqual(Minutes.objects.count(), 1)
        res = self.client.get(reverse('minutes:print', args=[m.pk]))
        self.assertContains(res, '行事')
        self.client.post(self.url, {'action': 'delete', 'id': m.pk})
        self.assertFalse(Minutes.objects.exists())

    def test_empty_is_not_saved(self):
        self.client.post(self.url, {'title': '空', 'transcript': ' ', 'summary': ''})
        self.assertFalse(Minutes.objects.exists())

    def test_keeps_newest_ten(self):
        for i in range(KEEP + 2):
            self.client.post(self.url, {'title': f'会議{i}', 'transcript': f'内容{i}'})
        titles = list(Minutes.objects.values_list('title', flat=True))
        self.assertEqual(len(titles), KEEP)
        self.assertEqual(titles[0], f'会議{KEEP + 1}')
        self.assertNotIn('会議0', titles)
        self.assertNotIn('会議1', titles)
        res = self.client.get(self.url)
        self.assertContains(res, '一番古い議事録')
        # 直すだけなら消えない
        m = Minutes.objects.last()
        self.client.post(self.url, {'id': m.pk, 'title': '直した', 'transcript': 'x'})
        self.assertEqual(Minutes.objects.count(), KEEP)

    def test_other_facility_cannot_open(self):
        other = Facility.objects.create(name='ほか')
        m = Minutes.objects.create(facility=other, title='ひみつ', held_on=datetime.date(2026, 9, 1), transcript='x')
        self.assertEqual(self.client.get(f'{self.url}?id={m.pk}').status_code, 404)
        self.assertEqual(self.client.get(reverse('minutes:print', args=[m.pk])).status_code, 404)
        self.client.post(self.url, {'action': 'delete', 'id': m.pk})
        self.assertTrue(Minutes.objects.filter(pk=m.pk).exists())
        # ほかの事業所の議事録は数に入れない
        for i in range(KEEP):
            self.client.post(self.url, {'title': f'会議{i}', 'transcript': 'x'})
        self.assertTrue(Minutes.objects.filter(pk=m.pk).exists())
        self.assertNotContains(self.client.get(self.url), 'ひみつ')

    def test_login_required(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    @override_settings(ANTHROPIC_API_KEY='test-key')
    @mock.patch('ai_assist.quick.anthropic.Anthropic')
    def test_organize(self, client_cls):
        client_cls.return_value.messages.create.return_value = SimpleNamespace(content=[SimpleNamespace(type='text', text=(
            '# 概要\n来月の行事を話した。\n## 誕生日会\n- 日程：「25日にしよう」と決めた。\n【次にやること】\n* 永山：案内を作る'))])
        res = self.client.post(reverse('minutes:organize'), {'text': 'えー、誕生日会は25日にしよう', 'title': '職員会議'})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['result'], '【概要】\n来月の行事を話した。\n\n【誕生日会】\n・日程：「25日にしよう」と決めた。\n\n【次にやること】\n・永山：案内を作る')
        kwargs = client_cls.return_value.messages.create.call_args.kwargs
        self.assertIn('【決まったこと】', kwargs['system'])
        self.assertIn('【件名】職員会議', kwargs['messages'][0]['content'])
        self.assertFalse(Minutes.objects.exists())   # 整理するだけで保存しない

    def test_organize_empty(self):
        res = self.client.post(reverse('minutes:organize'), {'text': ''})
        self.assertEqual(res.status_code, 400)

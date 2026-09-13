import json
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import StaffAccount
from facilities.models import Facility


def _fake_response(text, with_thinking=True):
    """Claude API の返答を模したオブジェクト（思考ブロックが先頭に来るケースも再現）"""
    blocks = []
    if with_thinking:
        blocks.append(SimpleNamespace(type='thinking', thinking='...'))
    blocks.append(SimpleNamespace(type='text', text=text))
    return SimpleNamespace(content=blocks)


@override_settings(ANTHROPIC_API_KEY='test-key')
class AiActivityPlanViewTests(TestCase):
    """活動名 → めあて・考察 生成エンドポイント"""

    def setUp(self):
        self.facility = Facility.objects.create(name='テスト事業所')
        self.user = StaffAccount.objects.create_user(
            username='staff', password='pw12345678', facility=self.facility,
        )
        self.client.force_login(self.user)
        self.url = reverse('records:ai_activity_plan')

    def test_requires_activity(self):
        res = self.client.post(self.url, {'activity': '   '})
        self.assertEqual(res.status_code, 400)
        self.assertIn('活動名', res.json()['error'])

    def test_requires_login(self):
        self.client.logout()
        res = self.client.post(self.url, {'activity': 'クッキー作り'})
        self.assertEqual(res.status_code, 302)

    @override_settings(ANTHROPIC_API_KEY='')
    def test_missing_api_key(self):
        res = self.client.post(self.url, {'activity': 'クッキー作り'})
        self.assertEqual(res.status_code, 500)
        self.assertIn('ANTHROPIC_API_KEY', res.json()['error'])

    @mock.patch('records.views.anthropic.Anthropic')
    def test_generates_aim_and_reflection(self, mock_client_cls):
        payload = {
            'aim': '役割分担、気を付けて調理道具をつかおう',
            'viewpoints': ['順番を待てるか', '道具の持ち方', '友だちへの声かけ'],
            'reflection': '（下書き）役割分担の場面で…',
        }
        # 思考ブロック＋前後の余分なテキストが付いていても解析できること
        mock_client_cls.return_value.messages.create.return_value = _fake_response(
            'はい。\n' + json.dumps(payload, ensure_ascii=False) + '\n以上です。'
        )

        res = self.client.post(self.url, {
            'activity': 'クッキー作り', 'memo': '', 'tags': '工作、おやつ',
        })
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()
        self.assertEqual(data['aim'], payload['aim'])
        self.assertEqual([v['text'] for v in data['viewpoints']], payload['viewpoints'])
        self.assertTrue(all(v['answer'] is None for v in data['viewpoints']))
        self.assertEqual(
            data['aim_text'],
            '役割分担、気を付けて調理道具をつかおう\n・順番を待てるか\n・道具の持ち方\n・友だちへの声かけ',
        )
        self.assertEqual(data['reflection'], payload['reflection'])

        # モデル・入力内容の確認
        kwargs = mock_client_cls.return_value.messages.create.call_args.kwargs
        self.assertEqual(kwargs['model'], 'claude-opus-5')
        user_content = kwargs['messages'][0]['content']
        self.assertIn('【活動】\nクッキー作り', user_content)
        self.assertIn('工作、おやつ', user_content)
        self.assertNotIn('【職員のメモ】', user_content)

    @mock.patch('records.views.anthropic.Anthropic')
    def test_memo_is_passed_to_model(self, mock_client_cls):
        mock_client_cls.return_value.messages.create.return_value = _fake_response(
            json.dumps({'aim': 'a', 'viewpoints': [], 'reflection': 'r'}), with_thinking=False
        )
        res = self.client.post(self.url, {'activity': 'かるた', 'memo': '最後まで集中できた'})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['aim_text'], 'a')
        user_content = mock_client_cls.return_value.messages.create.call_args.kwargs['messages'][0]['content']
        self.assertIn('【職員のメモ】\n最後まで集中できた', user_content)

    @mock.patch('records.views.anthropic.Anthropic')
    def test_invalid_json_is_reported(self, mock_client_cls):
        mock_client_cls.return_value.messages.create.return_value = _fake_response('うまく作れませんでした')
        res = self.client.post(self.url, {'activity': 'かるた'})
        self.assertEqual(res.status_code, 500)
        self.assertIn('解析できません', res.json()['error'])

    @mock.patch('records.views.anthropic.Anthropic')
    def test_unexpected_error_is_reported_as_json(self, mock_client_cls):
        mock_client_cls.return_value.messages.create.side_effect = TypeError('unexpected keyword')
        with self.assertLogs('records.views', level='ERROR'):
            res = self.client.post(self.url, {'activity': 'かるた'})
        self.assertEqual(res.status_code, 500)
        self.assertIn('TypeError', res.json()['error'])

    @mock.patch('records.views.anthropic.Anthropic')
    def test_model_is_configurable(self, mock_client_cls):
        mock_client_cls.return_value.messages.create.return_value = _fake_response(
            '{"aim": "a", "viewpoints": [], "reflection": "r"}')
        with self.settings(AI_PLAN_MODEL='claude-sonnet-5'):
            self.client.post(self.url, {'activity': 'かるた'})
        self.assertEqual(mock_client_cls.return_value.messages.create.call_args.kwargs['model'], 'claude-sonnet-5')


class AiGenerateAllViewTests(TestCase):
    """メモ → 4種の記録文の一括生成。エラー時は理由を JSON で返す。"""

    def setUp(self):
        self.facility = Facility.objects.create(name='テスト施設')
        self.user = StaffAccount.objects.create_user('staff', password='pass12345', facility=self.facility)
        self.client.force_login(self.user)
        self.url = reverse('records:ai_generate_all')

    @mock.patch('records.views.anthropic.Anthropic')
    def test_api_error_message_is_returned(self, mock_client_cls):
        mock_client_cls.return_value.messages.create.side_effect = RuntimeError('invalid x-api-key')
        with self.settings(ANTHROPIC_API_KEY='sk-ant-test'), self.assertLogs('records.views', level='ERROR'):
            res = self.client.post(self.url, {'memo': '集中できた'})
        self.assertEqual(res.status_code, 500)
        self.assertIn('RuntimeError: invalid x-api-key', res.json()['error'])

    @mock.patch('records.views.anthropic.Anthropic')
    def test_thinking_block_is_skipped(self, mock_client_cls):
        thinking = mock.Mock(type='thinking')
        text = mock.Mock(type='text', text='{"observation":"o","support":"s","reaction":"r","parent_message":"p"}')
        mock_client_cls.return_value.messages.create.return_value = mock.Mock(content=[thinking, text])
        with self.settings(ANTHROPIC_API_KEY='sk-ant-test'):
            res = self.client.post(self.url, {'memo': '集中できた'})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['observation'], 'o')

    @mock.patch('records.views.anthropic.Anthropic')
    def test_raw_newlines_inside_strings_are_accepted(self, mock_client_cls):
        text = '{"observation":"1行目\n2行目","support":"s","reaction":"r","parent_message":"p"}'
        mock_client_cls.return_value.messages.create.return_value = _fake_response(text)
        with self.settings(ANTHROPIC_API_KEY='sk-ant-test'):
            res = self.client.post(self.url, {'memo': '集中できた'})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['observation'], '1行目\n2行目')


class ViewpointTests(TestCase):
    """観点の はい／いいえ：保存・表示・考察の作り直し"""

    def setUp(self):
        self.facility = Facility.objects.create(name='テスト施設')
        self.user = StaffAccount.objects.create_user('staff', password='pass12345', facility=self.facility)
        self.client.force_login(self.user)
        from beneficiaries.models import Beneficiary
        from datetime import date
        self.b = Beneficiary.objects.create(facility=self.facility, last_name='山田', first_name='太郎',
                                            date_of_birth=date(2016, 4, 1))

    def test_clean_viewpoints(self):
        from records.models import DailyRecord
        raw = json.dumps([{'text': '順番を待てるか', 'answer': 'yes'}, {'text': '', 'answer': 'no'},
                          {'text': '声をかけられるか', 'answer': 'maybe'}, '道具を正しく持てるか', 5])
        self.assertEqual(DailyRecord.clean_viewpoints(raw), [
            {'text': '順番を待てるか', 'answer': 'yes'},
            {'text': '声をかけられるか', 'answer': None},
            {'text': '道具を正しく持てるか', 'answer': None},
        ])
        self.assertEqual(DailyRecord.clean_viewpoints('not json'), [])

    def test_create_saves_viewpoints_and_detail_shows_answers(self):
        from records.models import DailyRecord
        res = self.client.post(reverse('records:create', args=[self.b.pk]), {
            'date': '2026-09-10', 'activity_name': 'クッキー作り', 'activity_aim': '協力して作ろう',
            'activity_viewpoints': json.dumps([{'text': '順番を待てるか', 'answer': 'yes'},
                                               {'text': '道具を正しく持てるか', 'answer': 'no'},
                                               {'text': '友だちに声をかけるか', 'answer': None}]),
            'status': 'confirmed',
        })
        rec = DailyRecord.objects.get(beneficiary=self.b)
        self.assertEqual([v['answer'] for v in rec.activity_viewpoints], ['yes', 'no', None])
        self.assertRedirects(res, f'/records/{self.b.pk}/?selected={rec.pk}')
        res = self.client.get(f'/records/{self.b.pk}/?selected={rec.pk}')
        self.assertContains(res, 'vp-yes')
        self.assertContains(res, 'vp-no')
        self.assertContains(res, '未確認')

    def test_create_honors_next(self):
        res = self.client.post(reverse('records:create', args=[self.b.pk]), {
            'date': '2026-09-10', 'observation_memo': 'x', 'next': '/records/simple/?date=2026-09-10',
        })
        self.assertRedirects(res, '/records/simple/?date=2026-09-10')
        res = self.client.post(reverse('records:create', args=[self.b.pk]), {
            'date': '2026-09-10', 'next': '//evil.example.com/',
        })
        self.assertTrue(res.url.startswith('/records/'))

    @override_settings(ANTHROPIC_API_KEY='test-key')
    @mock.patch('records.views.anthropic.Anthropic')
    def test_plan_with_answers_keeps_viewpoints(self, mock_client_cls):
        mock_client_cls.return_value.messages.create.return_value = _fake_response(
            '{"aim": "a", "viewpoints": ["別の観点"], "reflection": "順番を待てていました。"}')
        vps = [{'text': '順番を待てるか', 'answer': 'yes'}, {'text': '道具を正しく持てるか', 'answer': 'no'}]
        res = self.client.post(reverse('records:ai_activity_plan'),
                               {'activity': 'クッキー作り', 'viewpoints': json.dumps(vps)})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['viewpoints'], vps)
        prompt = mock_client_cls.return_value.messages.create.call_args.kwargs['messages'][0]['content']
        self.assertIn('順番を待てるか → はい', prompt)
        self.assertIn('道具を正しく持てるか → いいえ', prompt)

    @override_settings(ANTHROPIC_API_KEY='test-key')
    @mock.patch('records.views.anthropic.Anthropic')
    def test_plan_without_answers_returns_new_viewpoints(self, mock_client_cls):
        mock_client_cls.return_value.messages.create.return_value = _fake_response(
            '{"aim": "a", "viewpoints": ["順番を待てるか", "声をかけるか"], "reflection": "r"}')
        res = self.client.post(reverse('records:ai_activity_plan'), {'activity': 'クッキー作り'})
        self.assertEqual(res.json()['viewpoints'],
                         [{'text': '順番を待てるか', 'answer': None}, {'text': '声をかけるか', 'answer': None}])

    def test_simple_pages_render(self):
        from schedules.models import ScheduledVisit
        from datetime import date
        ScheduledVisit.objects.create(facility=self.facility, beneficiary=self.b, date=date.today())
        res = self.client.get(reverse('records:simple_home'))
        self.assertContains(res, '山田 太郎')
        self.assertContains(res, 'まだ')
        res = self.client.get(reverse('records:simple_record', args=[self.b.pk]))
        self.assertContains(res, 'メモを入れる')
        self.assertContains(res, '確認して確定')


class RecordTemplateTests(TestCase):
    """日誌テンプレート：名前の置き換え、作成、反映、編集・削除、他施設からの遮断"""

    def setUp(self):
        from datetime import date
        from beneficiaries.models import Beneficiary
        from records.models import DailyRecord
        self.facility = Facility.objects.create(name='テスト施設')
        self.user = StaffAccount.objects.create_user('staff', password='pass12345', facility=self.facility)
        self.client.force_login(self.user)
        self.src = Beneficiary.objects.create(facility=self.facility, last_name='佐藤', first_name='はると',
                                              last_name_kana='さとう', first_name_kana='はると',
                                              date_of_birth=date(2016, 4, 1))
        self.dst = Beneficiary.objects.create(facility=self.facility, last_name='山田', first_name='太郎',
                                              date_of_birth=date(2015, 4, 1))
        self.rec = DailyRecord.objects.create(
            facility=self.facility, beneficiary=self.src, date=date(2026, 9, 10),
            activity_name='クッキー作り', activity_aim='佐藤 はるとが役割分担できる',
            activity_viewpoints=[{'text': 'はるとが順番を待てるか', 'answer': 'yes'}],
            observation_text='はるとは友だちに声をかけていました。', support_text='見守りました。',
            domain_social=True,
        )

    def test_anonymize_replaces_names(self):
        from records.models import RecordTemplate
        self.assertEqual(RecordTemplate.anonymize('佐藤 はるとと佐藤さんとはると', self.src),
                         '{名前}と{名前}さんと{名前}')
        self.assertEqual(RecordTemplate.anonymize('', self.src), '')

    def test_from_record_and_apply_for(self):
        from records.models import RecordTemplate
        t = RecordTemplate.from_record(self.rec, '', self.user)
        self.assertEqual(t.name, 'クッキー作り')
        self.assertEqual(t.activity_aim, '{名前}が役割分担できる')
        self.assertEqual(t.activity_viewpoints, [{'text': '{名前}が順番を待てるか', 'answer': None}])
        self.assertEqual(t.domains, ['domain_social'])
        self.assertEqual(t.source_beneficiary, self.src)
        data = t.apply_for(self.dst)
        self.assertEqual(data['activity_aim'], '太郎が役割分担できる')
        self.assertEqual(data['observation_text'], '太郎は友だちに声をかけていました。')
        self.assertEqual(data['activity_viewpoints'], [{'text': '太郎が順番を待てるか', 'answer': None}])
        self.assertEqual(data['domains'], ['domain_social'])

    def test_create_from_record_view_and_apply_view(self):
        from records.models import RecordTemplate
        res = self.client.post(reverse('records:template_from_record', args=[self.rec.pk]), {'name': 'クッキー基本'})
        self.assertRedirects(res, f'/records/{self.src.pk}/?selected={self.rec.pk}')
        t = RecordTemplate.objects.get(facility=self.facility)
        self.assertEqual(t.name, 'クッキー基本')
        self.assertEqual(t.created_by, self.user)

        # 反映：利用者の名前で返り、使用回数が増える
        res = self.client.get(reverse('records:template_apply', args=[t.pk]) + f'?beneficiary={self.dst.pk}')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['activity_aim'], '太郎が役割分担できる')
        t.refresh_from_db()
        self.assertEqual(t.use_count, 1)

        # 日誌画面の追加モーダルにテンプレートが出る
        res = self.client.get(f'/records/{self.dst.pk}/')
        self.assertContains(res, 'テンプレートから入力')
        self.assertContains(res, 'クッキー基本')

    def test_list_edit_delete(self):
        from records.models import RecordTemplate
        t = RecordTemplate.from_record(self.rec, 'クッキー基本', self.user)
        res = self.client.get(reverse('records:template_list'))
        self.assertContains(res, 'クッキー基本')
        self.assertContains(res, '{名前}が役割分担できる')

        res = self.client.post(reverse('records:template_edit', args=[t.pk]), {
            'name': 'クッキー改', 'activity_name': 'クッキー作り', 'activity_aim': '{名前}が協力できる',
            'activity_viewpoints': '・順番を待てるか\n\n道具を正しく持てるか\n',
            'domains': ['domain_health_life', 'domain_social'],
        })
        self.assertRedirects(res, reverse('records:template_list'))
        t.refresh_from_db()
        self.assertEqual(t.name, 'クッキー改')
        self.assertEqual([v['text'] for v in t.activity_viewpoints], ['順番を待てるか', '道具を正しく持てるか'])
        self.assertEqual(t.domains, ['domain_health_life', 'domain_social'])

        res = self.client.post(reverse('records:template_delete', args=[t.pk]))
        self.assertRedirects(res, reverse('records:template_list'))
        self.assertFalse(RecordTemplate.objects.filter(pk=t.pk).exists())

    def test_other_facility_is_blocked(self):
        from records.models import RecordTemplate
        t = RecordTemplate.from_record(self.rec, 'クッキー基本', self.user)
        other = Facility.objects.create(name='別施設')
        u2 = StaffAccount.objects.create_user('other', password='pass12345', facility=other)
        self.client.force_login(u2)
        self.assertEqual(self.client.get(reverse('records:template_apply', args=[t.pk]) + f'?beneficiary={self.dst.pk}').status_code, 404)
        self.assertEqual(self.client.post(reverse('records:template_edit', args=[t.pk]), {'name': 'x'}).status_code, 404)
        self.assertEqual(self.client.post(reverse('records:template_delete', args=[t.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('records:template_from_record', args=[self.rec.pk]), {'name': 'x'}).status_code, 404)
        self.assertNotContains(self.client.get(reverse('records:template_list')), 'クッキー基本')

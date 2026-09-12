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
        self.assertEqual(data['viewpoints'], payload['viewpoints'])
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

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


class JournalSectionOrderTests(TestCase):
    """施設ごとの「日誌の項目と順番」：画面の並びと AI 一括生成の指示"""

    def setUp(self):
        from beneficiaries.models import Beneficiary
        from datetime import date
        self.facility = Facility.objects.create(name='テスト施設', journal_sections=['support', 'observation', 'activity'])
        self.user = StaffAccount.objects.create_user('staff', password='pass12345', facility=self.facility)
        self.client.force_login(self.user)
        self.b = Beneficiary.objects.create(facility=self.facility, last_name='山田', first_name='太郎', date_of_birth=date(2016, 4, 1))

    def test_section_keys(self):
        self.assertEqual(self.facility.journal_section_keys(), ['support', 'observation', 'activity'])
        self.assertEqual(self.facility.journal_text_keys(), ['support', 'observation'])
        self.facility.journal_sections = []
        self.assertEqual(self.facility.journal_section_keys(), ['activity', 'observation', 'support', 'reaction', 'parent_message'])
        rows = Facility(journal_sections=['reaction']).journal_section_rows()
        self.assertEqual([(r['key'], r['enabled']) for r in rows][:2], [('reaction', True), ('activity', False)])

    def test_page_renders_in_order(self):
        from records.models import DailyRecord
        from datetime import date
        DailyRecord.objects.create(facility=self.facility, beneficiary=self.b, date=date(2026, 9, 10),
                                   observation_text='かんさつ', support_text='しえん', reaction_text='はんのう')
        res = self.client.get(f'/records/{self.b.pk}/')
        html = res.content.decode()
        self.assertLess(html.index('SUPPORT · 支援内容'), html.index('OBSERVATION · 観察・活動内容'))
        self.assertNotIn('REACTION · 本人の反応', html)
        self.assertNotIn('AI加算提案', html.split('newRecordModal')[0]) if not self.facility.use_billing else None

    @override_settings(ANTHROPIC_API_KEY='test-key')
    @mock.patch('records.views.anthropic.Anthropic')
    def test_generate_all_uses_order(self, mock_client_cls):
        mock_client_cls.return_value.messages.create.return_value = _fake_response(
            '{"support": "しえん", "observation": "かんさつ"}')
        res = self.client.post(reverse('records:ai_generate_all'), {'memo': '集中できた'})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['order'], ['support', 'observation'])
        system = mock_client_cls.return_value.messages.create.call_args.kwargs['system']
        self.assertIn('2種類', system)
        self.assertIn('1. 支援内容、2. 観察・活動内容', system)
        self.assertLess(system.index('"support"'), system.index('"observation"'))
        self.assertNotIn('"reaction"', system)


class PaperScanTests(TestCase):
    """紙の日誌の取り込み：アップロード → AI 読み取り → 確認して保存"""

    def setUp(self):
        from beneficiaries.models import Beneficiary
        from datetime import date
        self.facility = Facility.objects.create(name='テスト施設')
        self.user = StaffAccount.objects.create_user('staff', password='pass12345', facility=self.facility)
        self.client.force_login(self.user)
        self.b = Beneficiary.objects.create(facility=self.facility, last_name='佐藤', first_name='はると',
                                            last_name_kana='さとう', first_name_kana='はると', date_of_birth=date(2016, 4, 1))

    @staticmethod
    def _image(name='scan.png'):
        import io
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile
        buf = io.BytesIO()
        Image.new('RGB', (40, 60), (255, 255, 255)).save(buf, format='PNG')
        return SimpleUploadedFile(name, buf.getvalue(), content_type='image/png')

    def test_upload_and_list(self):
        from records.models import PaperScan
        res = self.client.post(reverse('records:paper_upload'), {'images': [self._image('a.png'), self._image('b.png')]})
        self.assertRedirects(res, reverse('records:paper_list'))
        self.assertEqual(PaperScan.objects.filter(facility=self.facility, status='pending').count(), 2)
        res = self.client.get(reverse('records:paper_list'))
        self.assertContains(res, '取り込んだ写真（2 枚）')
        self.assertContains(res, 'AIで読み取る')

    @override_settings(ANTHROPIC_API_KEY='test-key')
    @mock.patch('records.paper.anthropic.Anthropic')
    def test_extract_and_save(self, mock_client_cls):
        from records.models import DailyRecord, PaperScan
        scan = PaperScan.objects.create(facility=self.facility, uploaded_by=self.user, image=self._image())
        mock_client_cls.return_value.messages.create.return_value = _fake_response(json.dumps({
            'date': '2026-09-03', 'beneficiary_name': '佐藤はると', 'entry_time': '15:30', 'exit_time': '17時00分',
            'health_condition': 'good', 'activity_name': 'かるた', 'activity_aim': '順番を待つ',
            'viewpoints': [{'text': '順番を待てるか', 'answer': 'yes'}, {'text': '声をかけるか', 'answer': None}],
            'observation_text': 'ｶﾙﾀに集中していました。', 'support_text': '声かけをしました。', 'reaction_text': '笑顔でした。',
            'activity_reflection': '', 'parent_message': '', 'other_notes': '', 'unreadable': '右下の数字',
        }, ensure_ascii=False))
        res = self.client.post(reverse('records:paper_extract', args=[scan.pk]))
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data['beneficiary_id'], self.b.pk)
        self.assertEqual(data['date'], '2026-09-03')
        scan.refresh_from_db()
        self.assertEqual(scan.status, 'extracted')
        self.assertEqual(scan.extracted['exit_time'], '17:00')
        self.assertEqual(scan.extracted['observation_text'], 'カルタに集中していました。')
        # 画像は base64 で送られている
        content = mock_client_cls.return_value.messages.create.call_args.kwargs['messages'][0]['content']
        self.assertEqual(content[0]['type'], 'image')
        self.assertEqual(content[0]['source']['media_type'], 'image/jpeg')
        self.assertIn('佐藤 はると', content[1]['text'])

        res = self.client.get(reverse('records:paper_review', args=[scan.pk]))
        self.assertContains(res, 'かるた')
        self.assertContains(res, '右下の数字')
        self.assertContains(res, f'<option value="{self.b.pk}" selected>')

        res = self.client.post(reverse('records:paper_review', args=[scan.pk]), {
            'beneficiary': self.b.pk, 'date': '2026-09-03', 'entry_time': '15:30', 'exit_time': '17:00',
            'health_condition': 'good', 'activity_name': 'かるた', 'activity_aim': '順番を待つ',
            'activity_viewpoints': json.dumps([{'text': '順番を待てるか', 'answer': 'yes'}]),
            'observation_text': 'カルタに集中していました。', 'support_text': '声かけ', 'reaction_text': '笑顔', 'status': 'confirmed',
        })
        rec = DailyRecord.objects.get(beneficiary=self.b, date='2026-09-03')
        self.assertRedirects(res, f'/records/{self.b.pk}/?selected={rec.pk}')
        self.assertEqual(rec.activity_name, 'かるた')
        self.assertEqual(rec.activity_viewpoints[0]['answer'], 'yes')
        self.assertEqual(rec.photos.count(), 1)
        scan.refresh_from_db()
        self.assertEqual(scan.status, 'imported')
        self.assertEqual(scan.record, rec)

        # 同じ日の日誌があるときは上書き確認
        scan2 = PaperScan.objects.create(facility=self.facility, image=self._image())
        res = self.client.post(reverse('records:paper_review', args=[scan2.pk]), {
            'beneficiary': self.b.pk, 'date': '2026-09-03', 'observation_text': '別の内容', 'status': 'draft'})
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, '既存の日誌をこの内容で上書きする')
        res = self.client.post(reverse('records:paper_review', args=[scan2.pk]), {
            'beneficiary': self.b.pk, 'date': '2026-09-03', 'observation_text': '別の内容', 'status': 'draft', 'overwrite': '1'})
        rec.refresh_from_db()
        self.assertEqual(rec.observation_text, '別の内容')
        self.assertEqual(DailyRecord.objects.filter(beneficiary=self.b).count(), 1)

    def test_other_facility_and_delete(self):
        from records.models import PaperScan
        scan = PaperScan.objects.create(facility=self.facility, image=self._image())
        other = Facility.objects.create(name='別施設')
        u2 = StaffAccount.objects.create_user('other', password='pass12345', facility=other)
        self.client.force_login(u2)
        self.assertEqual(self.client.get(reverse('records:paper_review', args=[scan.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('records:paper_extract', args=[scan.pk])).status_code, 404)
        self.client.force_login(self.user)
        res = self.client.post(reverse('records:paper_delete', args=[scan.pk]))
        self.assertRedirects(res, reverse('records:paper_list'))
        self.assertFalse(PaperScan.objects.filter(pk=scan.pk).exists())


class SevereJournalAndAddonTests(TestCase):
    """重身用の日誌・加算の入力・専門的支援の時刻"""

    def setUp(self):
        from beneficiaries.models import Beneficiary
        from facilities.models import AddonMaster, FacilityAddonSetting
        self.facility = Facility.objects.create(name='はぴねす', use_billing=True, region_category='3')
        self.user = StaffAccount.objects.create_user(username='staff', password='pw12345678', facility=self.facility)
        self.client.force_login(self.user)
        self.ben = Beneficiary.objects.create(
            facility=self.facility, last_name='中村', first_name='みお', date_of_birth='2015-09-14', is_severe=True,
        )
        self.pickup = AddonMaster.objects.create(name='送迎加算（往・迎え）', addon_type='individual', unit_count=54, code='615001')
        self.collab = AddonMaster.objects.create(name='関係機関連携加算Ⅱ（情報連携）', addon_type='individual', unit_count=200)
        self.special = AddonMaster.objects.create(name='専門的支援実施加算（個別実施分）', addon_type='individual', unit_count=150)
        self.facility_addon = AddonMaster.objects.create(name='児童指導員等加配加算', addon_type='facility', unit_count=0)
        # 事業所の上書き：単位数
        FacilityAddonSetting.objects.create(facility=self.facility, addon=self.special, is_enabled=True, unit_count=123, code='615999')
        FacilityAddonSetting.objects.create(facility=self.facility, addon=self.pickup, is_enabled=True)
        FacilityAddonSetting.objects.create(facility=self.facility, addon=self.collab, is_enabled=True)

    def _post_create(self, **extra):
        data = {'date': '2026-09-15', 'status': 'draft', 'addons_present': '1'}
        data.update(extra)
        return self.client.post(reverse('records:create', args=[self.ben.pk]), data)

    def test_list_page_shows_kind_and_severe_fields(self):
        res = self.client.get(reverse('records:list', args=[self.ben.pk]))
        self.assertContains(res, '重身用')
        self.assertContains(res, 'severe_temperature')
        self.assertContains(res, '加算の入力はありますか')
        # 事業所の上書き（コード・単位数・円換算 123×11.05）
        self.assertContains(res, '615999')
        self.assertContains(res, '123単位（1359円）')

    def test_create_severe_record_with_care_and_addons(self):
        from billing.models import BillingMatrixAddon, BillingMatrixEntry
        from records.models import DailyRecord
        res = self._post_create(
            record_kind='severe', severe_temperature='36.8', severe_defecation='1', severe_stool='普通',
            severe_seizure='なし', addon_ids=[str(self.pickup.pk), str(self.collab.pk), str(self.facility_addon.pk)],
            collaboration_note='学校の担任と電話で情報共有',
        )
        self.assertEqual(res.status_code, 302)
        rec = DailyRecord.objects.get(beneficiary=self.ben)
        self.assertEqual(rec.record_kind, 'severe')
        self.assertEqual(rec.severe_care['temperature'], '36.8')
        self.assertEqual(rec.severe_care['stool'], '普通')
        self.assertEqual([r['label'] for r in rec.severe_care_rows], ['体温', '排便', '便の性状', '発作'])
        self.assertEqual(rec.collaboration_note, '学校の担任と電話で情報共有')
        entry = BillingMatrixEntry.objects.get(facility=self.facility, beneficiary=self.ben, date=rec.date)
        self.assertEqual(entry.status, 'attended')
        applied = set(BillingMatrixAddon.objects.filter(entry=entry, is_applied=True).values_list('addon_id', flat=True))
        # 体制加算は個別加算ではないので入らない
        self.assertEqual(applied, {self.pickup.pk, self.collab.pk})
        # 詳細に加算と連携内容が出る
        res = self.client.get(reverse('records:list', args=[self.ben.pk]) + f'?selected={rec.pk}')
        self.assertContains(res, '学校の担任と電話で情報共有')
        self.assertContains(res, '615001 · 54単位')
        self.assertContains(res, '体温')

    def test_default_kind_follows_beneficiary(self):
        from records.models import DailyRecord
        self._post_create()
        self.assertEqual(DailyRecord.objects.get(beneficiary=self.ben).record_kind, 'severe')
        self.ben.is_severe = False
        self.ben.save()
        self.client.post(reverse('records:create', args=[self.ben.pk]), {'date': '2026-09-16', 'status': 'draft'})
        self.assertEqual(DailyRecord.objects.get(beneficiary=self.ben, date='2026-09-16').record_kind, 'standard')

    def test_special_support_end_defaults_to_30_minutes_later(self):
        from records.models import DailyRecord
        self._post_create(special_support_staff=str(self.user.pk), special_support_start='15:00',
                          addon_ids=[str(self.special.pk)])
        rec = DailyRecord.objects.get(beneficiary=self.ben)
        self.assertEqual(rec.special_support_staff_id, self.user.pk)
        self.assertEqual(rec.special_support_start.strftime('%H:%M'), '15:00')
        self.assertEqual(rec.special_support_end.strftime('%H:%M'), '15:30')
        # 終了は編集できる
        res = self.client.post(reverse('records:update', args=[rec.pk]), {
            'status': 'draft', 'special_support_staff': str(self.user.pk),
            'special_support_start': '15:00', 'special_support_end': '15:45', 'addons_present': '1',
            'addon_ids': [str(self.special.pk)],
        })
        self.assertEqual(res.status_code, 302)
        rec.refresh_from_db()
        self.assertEqual(rec.special_support_end.strftime('%H:%M'), '15:45')
        res = self.client.get(reverse('records:list', args=[self.ben.pk]) + f'?selected={rec.pk}')
        self.assertContains(res, '専門的支援の実施')
        self.assertContains(res, '15:00〜15:45')

    def test_update_without_addon_section_keeps_billing_addons(self):
        from billing.models import BillingMatrixAddon, BillingMatrixEntry
        from records.models import DailyRecord
        self._post_create(addon_ids=[str(self.pickup.pk)])
        rec = DailyRecord.objects.get(beneficiary=self.ben)
        # 加算の欄を送らない更新（請求機能を使わない画面など）では触らない
        self.client.post(reverse('records:update', args=[rec.pk]), {'status': 'confirmed'})
        entry = BillingMatrixEntry.objects.get(beneficiary=self.ben, date=rec.date)
        self.assertEqual(BillingMatrixAddon.objects.filter(entry=entry).count(), 1)
        # 「加算なし」で保存すると外れる
        self.client.post(reverse('records:update', args=[rec.pk]), {'status': 'confirmed', 'addons_present': '1'})
        self.assertEqual(BillingMatrixAddon.objects.filter(entry=entry).count(), 0)

    def test_no_entry_created_when_no_addons(self):
        from billing.models import BillingMatrixEntry
        self._post_create()
        self.assertFalse(BillingMatrixEntry.objects.filter(beneficiary=self.ben).exists())

    def test_billing_off_hides_addon_section(self):
        self.facility.use_billing = False
        self.facility.save()
        res = self.client.get(reverse('records:list', args=[self.ben.pk]))
        self.assertNotContains(res, '加算の入力はありますか')
        self.assertContains(res, '重身用')

import json
from datetime import date
from types import SimpleNamespace
from unittest import mock

from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import StaffAccount
from beneficiaries.models import Beneficiary
from billing.models import BillingMatrixAddon, BillingMatrixEntry
from facilities.models import AddonMaster, Facility
from records.models import DailyRecord

from . import retrieval, services
from .models import AddonSuggestion, ReferenceDocument


def _fake_response(text):
    return SimpleNamespace(content=[SimpleNamespace(type='thinking', thinking='..'), SimpleNamespace(type='text', text=text)])


def _make_pdf(texts):
    import pymupdf
    doc = pymupdf.open()
    for t in texts:
        page = doc.new_page()
        page.insert_text((50, 80), t, fontname='japan', fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


class RetrievalTests(TestCase):
    def test_tokenize_bigrams(self):
        self.assertEqual(retrieval.tokenize('入浴支援'), ['入浴', '浴支', '支援'])
        self.assertEqual(retrieval.tokenize('Ａ、b'), ['a', 'b'])

    def test_split_chunks_overlap(self):
        text = '。'.join(f'文{i}' for i in range(600))
        chunks = retrieval.split_chunks(text, size=200, overlap=40)
        self.assertGreater(len(chunks), 5)
        self.assertTrue(all(len(c) <= 200 for c in chunks))

    def test_index_and_search(self):
        facility = Facility.objects.create(name='F')
        doc = ReferenceDocument.objects.create(facility=facility, title='算定基準')
        doc.file.save('test.pdf', ContentFile(_make_pdf([
            '入浴支援加算は、入浴の支援を行った場合に算定できる。',
            '延長支援加算は、営業時間を超えて支援した場合に算定する。',
        ])))
        n = retrieval.index_document(doc)
        doc.refresh_from_db()
        self.assertEqual(doc.page_count, 2)
        self.assertEqual(n, doc.chunk_count)
        self.assertTrue(doc.is_indexed)
        hits = retrieval.search(facility, '入浴支援加算の条件', top_k=1)
        self.assertEqual(len(hits), 1)
        self.assertIn('入浴', hits[0]['text'])
        self.assertEqual(hits[0]['page'], 1)
        # 他施設からは見えない
        self.assertEqual(retrieval.search(Facility.objects.create(name='G'), '入浴'), [])


class SuggestionBase(TestCase):
    def setUp(self):
        self.facility = Facility.objects.create(name='F')
        self.user = StaffAccount.objects.create_user('s', password='p', facility=self.facility, role=StaffAccount.ROLE_ADMIN)
        self.client.force_login(self.user)
        self.b = Beneficiary.objects.create(facility=self.facility, last_name='佐藤', first_name='はると', date_of_birth=date(2017, 5, 12))
        self.bath = AddonMaster.objects.create(name='入浴支援加算', addon_type='individual', unit_count=40, description='入浴の支援を行った場合')
        self.ext = AddonMaster.objects.create(name='延長支援加算', addon_type='individual', unit_count=61)
        self.record = DailyRecord.objects.create(facility=self.facility, beneficiary=self.b, date=date(2026, 9, 10),
                                                 author=self.user, support_text='入浴の介助を行った')


@override_settings(ANTHROPIC_API_KEY='k')
class SuggestionTests(SuggestionBase):
    @mock.patch('ai_assist.services.anthropic.Anthropic')
    def test_generate_creates_pending_suggestions(self, mock_cls):
        mock_cls.return_value.messages.create.return_value = _fake_response(
            '{"suggestions": [{"addon": "入浴支援加算", "reason": "入浴の介助を行った", "evidence": ""}, {"addon": "存在しない加算", "reason": "x"}]}')
        created = services.generate_addon_suggestions(self.record)
        self.assertEqual([s.addon for s in created], [self.bath])
        self.assertEqual(self.record.addon_suggestions.count(), 1)
        prompt = mock_cls.return_value.messages.create.call_args.kwargs['messages'][0]['content']
        self.assertIn('入浴の介助を行った', prompt)
        self.assertIn('入浴支援加算', prompt)

    @mock.patch('ai_assist.services.anthropic.Anthropic')
    def test_generate_skips_applied_and_decided(self, mock_cls):
        entry = BillingMatrixEntry.objects.create(facility=self.facility, beneficiary=self.b, date=self.record.date, status='attended')
        BillingMatrixAddon.objects.create(entry=entry, addon=self.bath, is_applied=True)
        AddonSuggestion.objects.create(facility=self.facility, daily_record=self.record, addon=self.ext, status='dismissed')
        mock_cls.return_value.messages.create.return_value = _fake_response(
            '{"suggestions": [{"addon": "入浴支援加算"}, {"addon": "延長支援加算"}]}')
        created = services.generate_addon_suggestions(self.record)
        self.assertEqual(created, [])
        self.assertIn('【既に適用済み】', mock_cls.return_value.messages.create.call_args.kwargs['messages'][0]['content'])

    @mock.patch('ai_assist.services.anthropic.Anthropic')
    def test_record_save_triggers_suggestion_and_detail_shows_it(self, mock_cls):
        mock_cls.return_value.messages.create.return_value = _fake_response(
            '{"suggestions": [{"addon": "入浴支援加算", "reason": "入浴介助あり", "evidence": "算定基準 p.3"}]}')
        res = self.client.post(reverse('records:create', args=[self.b.pk]), {'date': '2026-09-11', 'support_text': '入浴介助'})
        rec = DailyRecord.objects.get(beneficiary=self.b, date=date(2026, 9, 11))
        self.assertEqual(rec.addon_suggestions.count(), 1)
        res = self.client.get(f'/records/{self.b.pk}/?selected={rec.pk}')
        self.assertContains(res, 'AI加算提案')
        self.assertContains(res, '入浴介助あり')
        self.assertContains(res, '採用する')

    def test_adopt_reflects_to_billing_matrix_unconfirmed(self):
        s = AddonSuggestion.objects.create(facility=self.facility, daily_record=self.record, addon=self.bath, reason='r')
        BillingMatrixEntry.objects.create(facility=self.facility, beneficiary=self.b, date=self.record.date, status='attended', is_finalized=True)
        res = self.client.post(reverse('ai_assist:suggestion_decide', args=[s.pk, 'adopt']))
        self.assertRedirects(res, f'/records/{self.b.pk}/?selected={self.record.pk}')
        entry = BillingMatrixEntry.objects.get(beneficiary=self.b, date=self.record.date)
        self.assertFalse(entry.is_finalized)  # ◆ に戻る
        self.assertTrue(BillingMatrixAddon.objects.filter(entry=entry, addon=self.bath, is_applied=True).exists())
        s.refresh_from_db()
        self.assertEqual(s.status, 'adopted')
        # 取り消すと加算も外れる
        self.client.post(reverse('ai_assist:suggestion_decide', args=[s.pk, 'reopen']))
        self.assertFalse(BillingMatrixAddon.objects.filter(entry=entry, addon=self.bath).exists())
        s.refresh_from_db()
        self.assertEqual(s.status, 'pending')

    def test_dismiss(self):
        s = AddonSuggestion.objects.create(facility=self.facility, daily_record=self.record, addon=self.bath)
        self.client.post(reverse('ai_assist:suggestion_decide', args=[s.pk, 'dismiss']))
        s.refresh_from_db()
        self.assertEqual(s.status, 'dismissed')
        self.assertFalse(BillingMatrixEntry.objects.filter(beneficiary=self.b).exists())

    def test_other_facility_cannot_decide(self):
        s = AddonSuggestion.objects.create(facility=self.facility, daily_record=self.record, addon=self.bath)
        other = StaffAccount.objects.create_user('o', password='p', facility=Facility.objects.create(name='G'))
        self.client.force_login(other)
        self.assertEqual(self.client.post(reverse('ai_assist:suggestion_decide', args=[s.pk, 'adopt'])).status_code, 404)


@override_settings(ANTHROPIC_API_KEY='k')
class ChatTests(SuggestionBase):
    def _post(self, payload):
        return self.client.post(reverse('ai_assist:chat'), data=json.dumps(payload), content_type='application/json')

    def test_resolve_beneficiary_and_choices(self):
        Beneficiary.objects.create(facility=self.facility, last_name='佐藤', first_name='ゆい', date_of_birth=date(2016, 1, 1))
        b, cands = services.resolve_beneficiary(self.facility, '佐藤さんの今月の出席日数は？')
        self.assertIsNone(b)
        self.assertEqual(len(cands), 2)
        b, cands = services.resolve_beneficiary(self.facility, 'はるとさんの様子を教えて')
        self.assertEqual(b, self.b)
        b, cands = services.resolve_beneficiary(self.facility, '入浴支援加算の条件は？')
        self.assertIsNone(b)
        self.assertEqual(cands, [])

    def test_chat_returns_choices_when_ambiguous(self):
        Beneficiary.objects.create(facility=self.facility, last_name='佐藤', first_name='ゆい', date_of_birth=date(2016, 1, 1))
        res = self._post({'message': '佐藤さんの今月の出席日数は？'})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.json()['choices']), 2)

    @mock.patch('ai_assist.services.anthropic.Anthropic')
    def test_chat_with_beneficiary_context(self, mock_cls):
        mock_cls.return_value.messages.create.return_value = _fake_response('今月の出席は1日です。')
        res = self._post({'message': 'はるとさんの今月の出席日数は？', 'history': [{'role': 'user', 'content': 'こんにちは'}, {'role': 'assistant', 'content': 'どうぞ'}]})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['reply'], '今月の出席は1日です。')
        self.assertEqual(res.json()['beneficiary']['name'], '佐藤 はると')
        msgs = mock_cls.return_value.messages.create.call_args.kwargs['messages']
        self.assertEqual(len(msgs), 3)
        self.assertIn('利用者: 佐藤 はると', msgs[-1]['content'])
        self.assertIn('入浴支援加算', msgs[-1]['content'])

    @override_settings(ANTHROPIC_API_KEY='')
    def test_chat_without_key(self):
        res = self._post({'message': 'こんにちは'})
        self.assertEqual(res.status_code, 500)
        self.assertIn('ANTHROPIC_API_KEY', res.json()['error'])

    def test_widget_in_page_but_not_in_simple_theme(self):
        res = self.client.get(reverse('facilities:dashboard'))
        self.assertContains(res, 'id="aiChatFab"')
        self.user.ui_theme = 'simple'
        self.user.save()
        res = self.client.get(reverse('records:simple_home'))
        self.assertNotContains(res, 'id="aiChatFab"')


class DocumentViewTests(SuggestionBase):
    def test_upload_index_delete(self):
        pdf = ContentFile(_make_pdf(['入浴支援加算は入浴の支援を行った場合に算定できる。']), name='kijun.pdf')
        res = self.client.post(reverse('ai_assist:document_upload'), {'file': pdf, 'title': '算定基準'})
        self.assertRedirects(res, reverse('facilities:settings'))
        doc = ReferenceDocument.objects.get(facility=self.facility)
        self.assertFalse(doc.is_indexed)
        res = self.client.get(reverse('facilities:settings'))
        self.assertContains(res, '未ベクトル化')
        self.client.post(reverse('ai_assist:document_index', args=[doc.pk]))
        doc.refresh_from_db()
        self.assertTrue(doc.is_indexed)
        self.assertGreater(doc.chunk_count, 0)
        res = self.client.get(reverse('facilities:settings'))
        self.assertContains(res, 'ベクトル化済み')
        self.client.post(reverse('ai_assist:document_delete', args=[doc.pk]))
        self.assertFalse(ReferenceDocument.objects.exists())

    def test_non_pdf_rejected_and_staff_forbidden(self):
        res = self.client.post(reverse('ai_assist:document_upload'), {'file': ContentFile(b'x', name='a.txt')})
        self.assertFalse(ReferenceDocument.objects.exists())
        staff = StaffAccount.objects.create_user('st', password='p', facility=self.facility, role=StaffAccount.ROLE_STAFF)
        self.client.force_login(staff)
        res = self.client.post(reverse('ai_assist:document_upload'), {'file': ContentFile(b'%PDF', name='a.pdf')})
        self.assertRedirects(res, reverse('facilities:settings'))
        self.assertFalse(ReferenceDocument.objects.exists())


class CleanAiTextTests(TestCase):
    """AI の返答を丁寧な日本語の記録文に整える"""

    def test_clean_ai_text(self):
        from ai_assist.text import clean_ai_text, clean_ai_dict, effort_kwargs
        # 文字のまま残ったエスケープ・半角カタカナ・ゼロ幅文字・マークダウン
        self.assertEqual(clean_ai_text('\\u304a\\u306f\\u3088\\u3046'), 'おはよう')
        self.assertEqual(clean_ai_text('ｸｯｷｰ作り​を﻿しました。'), 'クッキー作りをしました。')
        self.assertEqual(clean_ai_text('```\n**観察記録：**「順番を待てました。」\n```'), '順番を待てました。')
        self.assertEqual(clean_ai_text('観察記録: 1行目\\n2行目'), '1行目\n2行目')
        self.assertEqual(clean_ai_text('1行目\n\n\n\n2行目'), '1行目\n\n2行目')
        self.assertEqual(clean_ai_text('前半\n後半', keep_newlines=False), '前半後半')
        self.assertEqual(clean_ai_text(None), '')
        self.assertEqual(clean_ai_dict({'a': ' x ', 'b': None}, ('a', 'b', 'c')), {'a': 'x', 'b': '', 'c': ''})
        self.assertEqual(effort_kwargs('claude-opus-5'), {'output_config': {'effort': 'low'}})
        self.assertEqual(effort_kwargs('claude-sonnet-5', 'medium'), {'output_config': {'effort': 'medium'}})
        self.assertEqual(effort_kwargs('claude-haiku-4-5-20251001'), {})


class SpeechTranscribeTests(TestCase):
    """iPhone の音声入力：画面で録った WAV をサーバーで文字にする（Google Cloud Speech-to-Text）"""

    def setUp(self):
        from accounts.models import StaffAccount
        from facilities.models import Facility
        self.f = Facility.objects.create(name='発達支援ルーム　ゆあーず')
        StaffAccount.objects.create_user('ryo', password='pw12345678', facility=self.f, role=StaffAccount.ROLE_STAFF)
        self.client.login(username='ryo', password='pw12345678')
        from django.urls import reverse
        self.url = reverse('ai_assist:transcribe')

    @staticmethod
    def wav(seconds=1.0, rate=16000, channels=1, bits=16):
        import struct
        n = int(rate * seconds) * channels * (bits // 8)
        return (b'RIFF' + struct.pack('<I', 36 + n) + b'WAVE' + b'fmt ' +
                struct.pack('<IHHIIHH', 16, 1, channels, rate, rate * channels * bits // 8, channels * bits // 8, bits) +
                b'data' + struct.pack('<I', n) + b'\x00' * n)

    def post(self, data):
        from django.core.files.uploadedfile import SimpleUploadedFile
        return self.client.post(self.url, {'audio': SimpleUploadedFile('voice.wav', data, content_type='audio/wav')})

    def reply(self, status, payload):
        from types import SimpleNamespace
        import json as _json
        return SimpleNamespace(status_code=status, json=lambda: payload, text=_json.dumps(payload))

    @override_settings(GOOGLE_SPEECH_API_KEY='k', GOOGLE_SPEECH_MODEL='latest_long')
    def test_transcribes_wav(self):
        with mock.patch('ai_assist.speech.requests.post') as post:
            post.return_value = self.reply(200, {'results': [{'alternatives': [{'transcript': 'きょうは晴れ。'}]},
                                                            {'alternatives': [{'transcript': ' 公園に行った。'}]}]})
            res = self.post(self.wav())
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['text'], 'きょうは晴れ。公園に行った。')
        kwargs = post.call_args.kwargs
        self.assertEqual(kwargs['params'], {'key': 'k'})
        cfg = kwargs['json']['config']
        self.assertEqual((cfg['encoding'], cfg['sampleRateHertz'], cfg['languageCode'], cfg['model']),
                         ('LINEAR16', 16000, 'ja-JP', 'latest_long'))
        self.assertTrue(cfg['enableAutomaticPunctuation'])

    @override_settings(GOOGLE_SPEECH_API_KEY='k')
    def test_retries_with_default_model_on_400(self):
        with mock.patch('ai_assist.speech.requests.post') as post:
            post.side_effect = [self.reply(400, {'error': {'message': 'model not supported'}}),
                                self.reply(200, {'results': [{'alternatives': [{'transcript': 'はい'}]}]})]
            res = self.post(self.wav())
        self.assertEqual(res.json()['text'], 'はい')
        second = post.call_args_list[1].kwargs['json']['config']
        self.assertEqual(second['model'], 'default')
        self.assertNotIn('enableAutomaticPunctuation', second)

    @override_settings(GOOGLE_SPEECH_API_KEY='k')
    def test_silence_returns_empty_text(self):
        with mock.patch('ai_assist.speech.requests.post', return_value=self.reply(200, {})):
            res = self.post(self.wav())
        self.assertEqual(res.json()['text'], '')

    @override_settings(GOOGLE_SPEECH_API_KEY='k')
    def test_rejects_wrong_format_and_long_audio(self):
        with mock.patch('ai_assist.speech.requests.post') as post:
            self.assertEqual(self.post(b'not a wav file at all, just text......................').status_code, 400)
            self.assertEqual(self.post(self.wav(rate=44100)).status_code, 400)
            self.assertEqual(self.post(self.wav(channels=2)).status_code, 400)
            self.assertEqual(self.post(self.wav(seconds=65)).status_code, 400)
            post.assert_not_called()
        self.assertEqual(self.client.post(self.url).status_code, 400)

    @override_settings(GOOGLE_SPEECH_API_KEY='k')
    def test_google_errors_are_explained(self):
        with mock.patch('ai_assist.speech.requests.post', return_value=self.reply(403, {'error': {}})):
            res = self.post(self.wav())
        self.assertEqual(res.status_code, 502)
        self.assertIn('API キー', res.json()['error'])
        import requests
        with mock.patch('ai_assist.speech.requests.post', side_effect=requests.ConnectionError('x')):
            res = self.post(self.wav())
        self.assertIn('つながりませんでした', res.json()['error'])

    @override_settings(GOOGLE_SPEECH_API_KEY='')
    def test_not_configured(self):
        res = self.post(self.wav())
        self.assertEqual(res.status_code, 502)
        self.assertIn('GOOGLE_SPEECH_API_KEY', res.json()['error'])

    def test_login_required(self):
        self.client.logout()
        self.assertEqual(self.post(self.wav()).status_code, 302)

    def test_page_tells_script_whether_server_is_on(self):
        from django.urls import reverse
        with override_settings(GOOGLE_SPEECH_API_KEY='k'):
            self.assertContains(self.client.get(reverse('minutes:index')), "window.VOICE_INPUT_SERVER = {url: '/ai/transcribe/'}")
        with override_settings(GOOGLE_SPEECH_API_KEY=''):
            self.assertContains(self.client.get(reverse('minutes:index')), 'window.VOICE_INPUT_SERVER = null')

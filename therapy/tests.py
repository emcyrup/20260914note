"""療育記録のテスト"""
import datetime
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import StaffAccount
from beneficiaries.models import Beneficiary
from facilities.models import Facility

from .models import TherapyProfile, TherapyRecord


class TherapyTests(TestCase):
    def setUp(self):
        self.f = Facility.objects.create(name='発達支援ルーム　ゆあーず', use_therapy_record=True)
        self.user = StaffAccount.objects.create_user('ryo', password='pw12345678', facility=self.f,
                                                     role=StaffAccount.ROLE_ADMIN, display_name='永山')
        self.client.login(username='ryo', password='pw12345678')
        self.kid = Beneficiary.objects.create(facility=self.f, last_name='青木', first_name='子',
                                              last_name_kana='あおき', date_of_birth=datetime.date(2019, 4, 1))
        self.url = reverse('therapy:child', args=[self.kid.pk])

    def test_disabled_facility_redirects(self):
        self.f.use_therapy_record = False
        self.f.save()
        res = self.client.get(reverse('therapy:index'))
        self.assertRedirects(res, reverse('facilities:dashboard'))
        res = self.client.get(reverse('facilities:dashboard'))
        self.assertNotContains(res, '療育記録')

    def test_menu_and_index(self):
        res = self.client.get(reverse('therapy:index'))
        self.assertContains(res, '療育記録')
        self.assertContains(res, '青木 子')

    def test_cautions_voice_input_button(self):
        res = self.client.get(self.url)
        self.assertContains(res, 'data-voice-target="cautions"')
        self.assertContains(res, 'js/voice-input.js')

    def test_cautions_add_edit_delete(self):
        res = self.client.post(self.url, {'action': 'cautions', 'cautions': '大きな音が苦手。'})
        self.assertRedirects(res, self.url)
        self.assertEqual(TherapyProfile.objects.get(beneficiary=self.kid).cautions, '大きな音が苦手。')

        res = self.client.post(self.url, {'action': 'add', 'date': '2026-10-03', 'time': '10:00', 'staff': self.user.pk,
                                          'activity_1': 'ウレタン棒', 'activity_2': 'アンパンマンブロック',
                                          'activity_3': '', 'activity_4': 'シール貼り', 'body': 'よく集中していた。'})
        self.assertRedirects(res, self.url)
        rec = TherapyRecord.objects.get(beneficiary=self.kid)
        self.assertEqual((rec.date, rec.time, rec.staff, rec.staff_label), (datetime.date(2026, 10, 3), datetime.time(10, 0), self.user, '永山'))
        self.assertEqual(rec.activity_list, ['①ウレタン棒', '②アンパンマンブロック', '③シール貼り'])
        self.assertEqual(rec.date_label, '2026年10月3日 土曜日')
        self.assertEqual(rec.time_label, '10時00分')

        res = self.client.get(self.url)
        for chip in ('①ウレタン棒', '②アンパンマンブロック', '③シール貼り'):
            self.assertContains(res, f'<span class="th-chip">{chip}</span>', html=True)
        self.assertContains(res, '担当 永山')

        res = self.client.post(self.url, {'action': 'edit', 'record': rec.pk, 'date': '2026-10-04', 'time': '',
                                          'staff': '', 'staff_name': '池田', 'activity_1': 'トランポリン', 'body': '直した。'})
        rec.refresh_from_db()
        self.assertEqual((rec.date, rec.time, rec.staff, rec.staff_label, rec.body),
                         (datetime.date(2026, 10, 4), None, None, '池田', '直した。'))

        res = self.client.post(self.url, {'action': 'delete', 'record': rec.pk})
        self.assertFalse(TherapyRecord.objects.exists())

    def test_pdf_pages_five_per_sheet(self):
        for i in range(7):
            TherapyRecord.objects.create(facility=self.f, beneficiary=self.kid, date=datetime.date(2026, 10, 1 + i),
                                         time=datetime.time(10, 0), staff=self.user, activities=['えほん'], body=f'記録{i}')
        res = self.client.get(reverse('therapy:pdf', args=[self.kid.pk]) + '?fmt=html')
        self.assertContains(res, '療育記録')
        self.assertContains(res, '1/2')
        self.assertContains(res, '記録6')
        res = self.client.get(reverse('therapy:pdf', args=[self.kid.pk]) + '?fmt=html&ym=2026-10')
        self.assertContains(res, '2026-10')
        res = self.client.get(reverse('therapy:pdf', args=[self.kid.pk]) + '?fmt=html&blank=1')
        self.assertNotContains(res, '記録0')
        self.assertContains(res, '担当')

    def test_other_facility_child_is_404(self):
        other = Facility.objects.create(name='ほか', use_therapy_record=True)
        kid = Beneficiary.objects.create(facility=other, last_name='他', first_name='子', date_of_birth=datetime.date(2019, 4, 1))
        self.assertEqual(self.client.get(reverse('therapy:child', args=[kid.pk])).status_code, 404)

    def test_index_lists_todays_reservations(self):
        self.f.use_reservation = True
        self.f.save()
        from reservations import services
        s = services.get_setting(self.f)
        s.slot_mode = True
        s.save()
        day = datetime.date.today()
        while not s.slot_hours(day):
            day += datetime.timedelta(days=1)
        services.create_reservation(self.f, self.kid, day, start_time=s.slot_hours(day)[0])
        res = self.client.get(reverse('therapy:index') + f'?date={day.isoformat()}')
        self.assertContains(res, '未記録')
        self.assertContains(res, f'time={s.slot_hours(day)[0]:02d}:00')


class TherapySuggestAndFilterTests(TestCase):
    def setUp(self):
        self.f = Facility.objects.create(name='発達支援ルーム　ゆあーず', use_therapy_record=True)
        StaffAccount.objects.create_user('ryo', password='pw12345678', facility=self.f, role=StaffAccount.ROLE_ADMIN)
        self.client.login(username='ryo', password='pw12345678')
        dob = datetime.date(2019, 4, 1)
        self.kid = Beneficiary.objects.create(facility=self.f, last_name='青木', first_name='子', date_of_birth=dob)
        self.other = Beneficiary.objects.create(facility=self.f, last_name='井上', first_name='子', date_of_birth=dob)
        self.url = reverse('therapy:child', args=[self.kid.pk])

    def rec(self, kid, day, acts):
        return TherapyRecord.objects.create(facility=self.f, beneficiary=kid, date=day, activities=acts)

    def test_suggestions_are_shared_across_children_by_frequency(self):
        from .views import activity_suggestions
        self.rec(self.other, datetime.date(2026, 9, 1), ['ウレタン棒', 'ブロック', ''])
        self.rec(self.other, datetime.date(2026, 9, 2), ['ブロック', ' 太鼓 '])
        self.rec(self.kid, datetime.date(2026, 9, 3), ['ブロック', 'ウレタン棒'])
        ghost = Facility.objects.create(name='ほか', use_therapy_record=True)
        g = Beneficiary.objects.create(facility=ghost, last_name='他', first_name='子', date_of_birth=datetime.date(2019, 4, 1))
        TherapyRecord.objects.create(facility=ghost, beneficiary=g, date=datetime.date(2026, 9, 3), activities=['秘密の活動'])
        # ブロック 3回 → ウレタン棒 2回 → 太鼓 1回（前後の空白は除く・空欄は数えない・ほかの事業所は出さない）
        self.assertEqual(activity_suggestions(self.f), ['ブロック', 'ウレタン棒', '太鼓'])
        res = self.client.get(reverse('therapy:child', args=[self.kid.pk]))
        self.assertContains(res, '<datalist id="activity-options">')
        self.assertContains(res, '<option value="太鼓">')
        self.assertContains(res, 'list="activity-options"')
        self.assertContains(res, 'data-activity="ブロック"')
        self.assertNotContains(res, '秘密の活動')

    def test_no_chips_without_history(self):
        res = self.client.get(self.url)
        self.assertNotContains(res, 'class="th-suggest"')

    def test_filter_by_date_range(self):
        for d in (1, 10, 20, 30):
            self.rec(self.kid, datetime.date(2026, 9, d), [f'活動{d}'])
        self.rec(self.kid, datetime.date(2026, 10, 5), ['十月'])
        res = self.client.get(self.url + '?from=2026-09-10&to=2026-09-20')
        self.assertEqual([r.date.day for r in res.context['records']], [20, 10])
        self.assertContains(res, '（9/10〜9/20）')
        self.assertContains(res, 'from=2026-09-10&amp;to=2026-09-20')   # 印刷にも同じ絞り込み
        # 逆に入れても入れ替えて絞る・片側だけでもよい
        res = self.client.get(self.url + '?from=2026-09-20&to=2026-09-10')
        self.assertEqual(len(res.context['records']), 2)
        res = self.client.get(self.url + '?from=2026-09-25')
        self.assertEqual([r.date for r in res.context['records']], [datetime.date(2026, 10, 5), datetime.date(2026, 9, 30)])
        # 月と組み合わせる
        res = self.client.get(self.url + '?ym=2026-09&from=2026-09-15')
        self.assertEqual([r.date.day for r in res.context['records']], [30, 20])
        # 正しくない日付は無視
        res = self.client.get(self.url + '?from=abc')
        self.assertEqual(len(res.context['records']), 5)
        # 当てはまらないとき
        res = self.client.get(self.url + '?from=2027-01-01')
        self.assertContains(res, 'この期間の記録はありません')

    def test_pdf_uses_date_range(self):
        for d in (1, 10, 20):
            self.rec(self.kid, datetime.date(2026, 9, d), [f'活動{d}'])
        res = self.client.get(reverse('therapy:pdf', args=[self.kid.pk]) + '?fmt=html&from=2026-09-05&to=2026-09-15')
        self.assertContains(res, '活動10')
        self.assertNotContains(res, '活動20')
        self.assertNotContains(res, '活動1<')


class CautionsFigureTests(TestCase):
    """留意点の見取り図と表（AI を使わず、文の形から作る）"""

    DETAIL = ('【概要】\nはじめは母から離れにくいが、好きな遊びに誘うと入れる。\n\n【ボールプール】\n・入り方：はじめは母と一緒。「一緒に入ろう」と誘うと笑顔で入った\n'
              '・注意：飛び込むと危ないので、そばで見守る\n\n【ひも通し】\n・できたこと：3つまで自分で通せた\n\n【全体のまとめ】\n母から離れて遊べる時間が長くなっている。\n\n'
              '【気をつけること】\n・大きな音が苦手。太鼓の前に「音が鳴るよ」と伝える\n')

    def setUp(self):
        self.f = Facility.objects.create(name='発達支援ルーム　ゆあーず', use_therapy_record=True)
        StaffAccount.objects.create_user('ryo', password='pw12345678', facility=self.f, role=StaffAccount.ROLE_ADMIN)
        self.client.login(username='ryo', password='pw12345678')
        self.kid = Beneficiary.objects.create(facility=self.f, last_name='青木', first_name='子', date_of_birth=datetime.date(2019, 4, 1))

    def test_parse_sections_and_table(self):
        from .figure import build, parse_cautions
        p = parse_cautions(self.DETAIL)
        self.assertEqual([sc['title'] for sc in p['scenes']], ['ボールプール', 'ひも通し'])
        self.assertEqual(p['scenes'][0]['items'][0], {'label': '入り方', 'text': 'はじめは母と一緒。「一緒に入ろう」と誘うと笑顔で入った'})
        self.assertEqual(p['overview'], 'はじめは母から離れにくいが、好きな遊びに誘うと入れる。')
        self.assertEqual(len(p['cautions']), 1)
        b = build(self.DETAIL, '青木 子')
        self.assertFalse(b['empty'])
        rows = b['rows']
        self.assertEqual([r['title'] for r in rows], ['ボールプール', 'ひも通し', '全体'])
        self.assertEqual([i['label'] for i in rows[0]['care']], ['注意'])       # 「危ない」「見守る」は気をつけること
        self.assertEqual([i['label'] for i in rows[0]['rest']], ['入り方'])
        self.assertEqual(rows[2]['rest'][0]['text'], '母から離れて遊べる時間が長くなっている。')
        self.assertIn('<svg', b['svg'])
        self.assertIn('ボールプール', b['svg'])
        self.assertIn('気をつけること', b['svg'])
        self.assertIn('青木 子', b['svg'])
        # 見出しの無い箇条書きは1つの枝
        b2 = build('・大きな音が苦手\n・電車が好き', '青木 子')
        self.assertEqual([r['title'] for r in b2['rows']], ['留意点'])
        self.assertEqual(b2['rows'][0]['care'], [{'label': '', 'text': '大きな音が苦手'}])
        self.assertIn('>大きな音が苦手<', b2['svg'])
        self.assertTrue(build('', '青木 子')['empty'])
        # 「<」などは SVG の中でエスケープされる
        self.assertIn('&lt;b&gt;', build('【場面】\n・<b>：x', 'A')['svg'])

    def test_child_page_shows_figure_and_print(self):
        url = reverse('therapy:child', args=[self.kid.pk])
        res = self.client.get(url)
        self.assertContains(res, '見取り図と表')
        self.assertTrue(res.context['fig']['empty'])
        TherapyProfile.objects.create(beneficiary=self.kid, cautions=self.DETAIL)
        res = self.client.get(url)
        self.assertFalse(res.context['fig']['empty'])
        self.assertContains(res, 'aria-label="留意点の見取り図"')
        self.assertContains(res, '気をつけること・対応')
        res = self.client.get(reverse('therapy:cautions_figure_print', args=[self.kid.pk]))
        self.assertContains(res, '留意点の見取り図・表　青木 子')
        self.assertContains(res, '<svg')

    def test_figure_endpoint_renders_unsaved_text(self):
        res = self.client.post(reverse('therapy:cautions_figure', args=[self.kid.pk]), {'text': self.DETAIL})
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.json()['empty'])
        self.assertIn('ひも通し', res.json()['html'])
        res = self.client.post(reverse('therapy:cautions_figure', args=[self.kid.pk]), {'text': ''})
        self.assertTrue(res.json()['empty'])
        # ほかの事業所の利用者は 404
        g = Facility.objects.create(name='ほか', use_therapy_record=True)
        kid = Beneficiary.objects.create(facility=g, last_name='井上', first_name='花', date_of_birth=datetime.date(2019, 4, 1))
        self.assertEqual(self.client.post(reverse('therapy:cautions_figure', args=[kid.pk]), {'text': 'x'}).status_code, 404)
        self.assertEqual(self.client.get(reverse('therapy:cautions_figure_print', args=[kid.pk])).status_code, 404)


class TherapySearchTests(TestCase):
    """履歴は上限なし・事業所内の記録を横断して探す・ほかの利用者の記録を写して書く"""

    def setUp(self):
        self.f = Facility.objects.create(name='発達支援ルーム　ゆあーず', use_therapy_record=True)
        self.user = StaffAccount.objects.create_user('ryo', password='pw12345678', facility=self.f,
                                                     role=StaffAccount.ROLE_ADMIN, display_name='永山')
        self.suzuki = StaffAccount.objects.create_user('suzuki', password='pw12345678', facility=self.f, display_name='鈴木')
        self.client.login(username='ryo', password='pw12345678')
        dob = datetime.date(2019, 4, 1)
        self.kid = Beneficiary.objects.create(facility=self.f, last_name='青木', first_name='子', last_name_kana='あおき', date_of_birth=dob)
        self.other = Beneficiary.objects.create(facility=self.f, last_name='井上', first_name='太郎', last_name_kana='いのうえ', date_of_birth=dob)
        self.url = reverse('therapy:search')

    def rec(self, kid, day, acts, **kw):
        return TherapyRecord.objects.create(facility=self.f, beneficiary=kid, date=day, activities=acts, **kw)

    def test_history_has_no_cap(self):
        start = datetime.date(2025, 1, 1)
        for i in range(230):
            self.rec(self.kid, start + datetime.timedelta(days=i), [f'活動{i}'])
        res = self.client.get(reverse('therapy:child', args=[self.kid.pk]))
        self.assertEqual(len(res.context['records']), 230)
        self.assertNotContains(res, '新しい200件')

    def test_search_by_child_staff_date_and_keyword(self):
        a = self.rec(self.kid, datetime.date(2026, 9, 1), ['ウレタン棒', 'ブロック'], staff=self.suzuki, body='声かけで落ち着いた')
        b = self.rec(self.other, datetime.date(2026, 9, 2), ['ブロック'], staff_name='山田', body='最後まで集中')
        c = self.rec(self.other, datetime.date(2026, 10, 3), ['太鼓'], staff=self.user)
        res = self.client.get(self.url)
        self.assertFalse(res.context['searched'])
        self.assertContains(res, '条件を入れて')
        # 利用者名（姓・かな）
        self.assertEqual([r.pk for r in self.client.get(self.url + '?child=井上').context['records']], [c.pk, b.pk])
        self.assertEqual([r.pk for r in self.client.get(self.url + '?child=あおき').context['records']], [a.pk])
        # 職員名（表示名・名前欄）
        self.assertEqual([r.pk for r in self.client.get(self.url + '?staff=鈴木').context['records']], [a.pk])
        self.assertEqual([r.pk for r in self.client.get(self.url + '?staff=山田').context['records']], [b.pk])
        # 言葉（やったこと・記録）と日付
        self.assertEqual([r.pk for r in self.client.get(self.url + '?q=ブロック').context['records']], [b.pk, a.pk])
        self.assertEqual([r.pk for r in self.client.get(self.url + '?q=集中').context['records']], [b.pk])
        self.assertEqual([r.pk for r in self.client.get(self.url + '?ym=2026-10').context['records']], [c.pk])
        self.assertEqual([r.pk for r in self.client.get(self.url + '?from=2026-09-02&to=2026-09-30').context['records']], [b.pk])
        # 組み合わせ
        res = self.client.get(self.url + '?child=井上&q=ブロック')
        self.assertEqual([r.pk for r in res.context['records']], [b.pk])
        self.assertContains(res, '1 件')
        self.assertContains(res, 'この内容で書く')
        res = self.client.get(self.url + '?child=青木&staff=山田')
        self.assertContains(res, '当てはまる記録はありません')

    def test_search_excludes_other_facility(self):
        g = Facility.objects.create(name='ほか', use_therapy_record=True)
        kid = Beneficiary.objects.create(facility=g, last_name='井上', first_name='花', date_of_birth=datetime.date(2019, 4, 1))
        TherapyRecord.objects.create(facility=g, beneficiary=kid, date=datetime.date(2026, 9, 1), activities=['ブロック'])
        self.assertEqual(self.client.get(self.url + '?q=ブロック').context['records'], [])
        self.assertEqual(self.client.get(self.url + '?child=井上').context['records'], [])

    def test_copy_prefills_add_form_for_another_child(self):
        src = self.rec(self.other, datetime.date(2026, 9, 2), ['ブロック', '太鼓'], body='最後まで集中できた')
        res = self.client.get(reverse('therapy:child', args=[self.kid.pk]) + f'?copy={src.pk}')
        self.assertEqual(res.context['copy_rec'], src)
        self.assertContains(res, '井上 太郎さんの 9/2 の記録')
        self.assertContains(res, 'name="activity_1" class="form-control" maxlength="100" list="activity-options" autocomplete="off"\n               value="ブロック"')
        self.assertContains(res, '>最後まで集中できた</textarea>')
        # ほかの事業所の記録は写さない
        g = Facility.objects.create(name='ほか', use_therapy_record=True)
        kid = Beneficiary.objects.create(facility=g, last_name='井上', first_name='花', date_of_birth=datetime.date(2019, 4, 1))
        foreign = TherapyRecord.objects.create(facility=g, beneficiary=kid, date=datetime.date(2026, 9, 1), activities=['秘密'], body='秘密')
        res = self.client.get(reverse('therapy:child', args=[self.kid.pk]) + f'?copy={foreign.pk}')
        self.assertIsNone(res.context['copy_rec'])
        self.assertNotContains(res, '秘密')

    def test_links_on_index_and_child(self):
        self.assertContains(self.client.get(reverse('therapy:index')), '記録を検索')
        self.assertContains(self.client.get(reverse('therapy:child', args=[self.kid.pk])), '記録を検索')


@override_settings(ANTHROPIC_API_KEY='test-key')
class CautionsSummaryTests(TestCase):
    def setUp(self):
        self.f = Facility.objects.create(name='発達支援ルーム　ゆあーず', use_therapy_record=True)
        StaffAccount.objects.create_user('ryo', password='pw12345678', facility=self.f, role=StaffAccount.ROLE_ADMIN)
        self.client.login(username='ryo', password='pw12345678')
        self.kid = Beneficiary.objects.create(facility=self.f, last_name='青木', first_name='子',
                                              last_name_kana='あおき', date_of_birth=datetime.date(2019, 4, 1))
        self.url = reverse('therapy:cautions_summary', args=[self.kid.pk])

    @mock.patch('ai_assist.quick.anthropic.Anthropic')
    def test_summarizes_to_bullets(self, client_cls):
        client_cls.return_value.messages.create.return_value = SimpleNamespace(content=[
            SimpleNamespace(type='thinking', thinking='...'),
            SimpleNamespace(type='text', text='・大きな音が苦手\n\n- 疲れると手が出る。休憩を先に入れる\n* 電車の話が好き'),
        ])
        res = self.client.post(self.url, {'text': 'えーと、大きな音が苦手で、あの、疲れると手が出ることがあるので休憩を先に。電車の話が好き'})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['result'], '・大きな音が苦手\n・疲れると手が出る。休憩を先に入れる\n・電車の話が好き')
        kwargs = client_cls.return_value.messages.create.call_args.kwargs
        self.assertIn('箇条書き', kwargs['system'])
        self.assertIn('青木 子さん', kwargs['messages'][0]['content'])
        self.assertIn('電車の話が好き', kwargs['messages'][0]['content'])
        # 要約は返すだけで、保存はしない
        self.assertFalse(TherapyProfile.objects.filter(beneficiary=self.kid).exists())

    @mock.patch('ai_assist.quick.anthropic.Anthropic')
    def test_detail_mode_keeps_sections(self, client_cls):
        client_cls.return_value.messages.create.return_value = SimpleNamespace(content=[SimpleNamespace(type='text', text=(
            '## 概要\nグーの部屋とパンの部屋で遊んだ。\n'
            '【グーの部屋でのエアホッケー】\n- ルール設定：「シュートしたら勝ち」に「勝ち」と復唱した。\n'
            '* **片付け**：「片付けは」の声かけで全部片付けた。\n\n\n【全体のまとめ】\n着席して過ごせた。'))])
        res = self.client.post(self.url, {'text': 'グーの部屋でエアホッケー…', 'mode': 'detail'})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['result'], (
            '【概要】\nグーの部屋とパンの部屋で遊んだ。\n\n'
            '【グーの部屋でのエアホッケー】\n・ルール設定：「シュートしたら勝ち」に「勝ち」と復唱した。\n'
            '・片付け：「片付けは」の声かけで全部片付けた。\n\n【全体のまとめ】\n着席して過ごせた。'))
        kwargs = client_cls.return_value.messages.create.call_args.kwargs
        self.assertIn('【全体のまとめ】', kwargs['system'])
        self.assertIn('「」で、話したとおりに残す', kwargs['system'])
        self.assertEqual(kwargs['max_tokens'], 4096)

    def test_empty_text(self):
        res = self.client.post(self.url, {'text': '  '})
        self.assertEqual(res.status_code, 400)

    @override_settings(ANTHROPIC_API_KEY='')
    def test_missing_api_key(self):
        res = self.client.post(self.url, {'text': 'メモ'})
        self.assertEqual(res.status_code, 500)
        self.assertIn('ANTHROPIC_API_KEY', res.json()['error'])

    @mock.patch('ai_assist.quick.anthropic.Anthropic', side_effect=RuntimeError('boom'))
    def test_api_error(self, _):
        res = self.client.post(self.url, {'text': 'メモ'})
        self.assertEqual(res.status_code, 500)
        self.assertIn('エラー', res.json()['error'])

    def test_other_facility_child_is_404(self):
        other = Facility.objects.create(name='ほか', use_therapy_record=True)
        kid = Beneficiary.objects.create(facility=other, last_name='他', first_name='子', date_of_birth=datetime.date(2019, 4, 1))
        res = self.client.post(reverse('therapy:cautions_summary', args=[kid.pk]), {'text': 'メモ'})
        self.assertEqual(res.status_code, 404)

    def test_long_cautions_saved_and_shortened_on_later_pages(self):
        long_text = '【概要】\n' + 'あ' * 3000
        self.client.post(reverse('therapy:child', args=[self.kid.pk]), {'action': 'cautions', 'cautions': long_text})
        self.assertEqual(TherapyProfile.objects.get(beneficiary=self.kid).cautions, long_text)
        f = self.kid.facility
        for d in range(1, 8):   # 7回ぶん → 2枚
            TherapyRecord.objects.create(facility=f, beneficiary=self.kid, date=datetime.date(2026, 9, d))
        res = self.client.get(reverse('therapy:pdf', args=[self.kid.pk]) + '?fmt=html')
        self.assertContains(res, 'あ' * 3000, count=1)
        self.assertContains(res, '（1枚目のとおり）', count=1)
        self.assertContains(res, 'tr-cau long')

    def test_button_on_page(self):
        res = self.client.get(reverse('therapy:child', args=[self.kid.pk]))
        self.assertContains(res, 'id="cautions-detail"')
        self.assertContains(res, 'id="cautions-summary"')
        self.assertContains(res, self.url)


@override_settings(ANTHROPIC_API_KEY='test-key')
class RecordSummaryTests(TestCase):
    def setUp(self):
        self.f = Facility.objects.create(name='発達支援ルーム　ゆあーず', use_therapy_record=True)
        StaffAccount.objects.create_user('ryo', password='pw12345678', facility=self.f, role=StaffAccount.ROLE_ADMIN)
        self.client.login(username='ryo', password='pw12345678')
        self.kid = Beneficiary.objects.create(facility=self.f, last_name='青木', first_name='子',
                                              last_name_kana='あおき', date_of_birth=datetime.date(2019, 4, 1))
        self.url = reverse('therapy:record_summary', args=[self.kid.pk])
        self.post = {'date': '2026-09-24', 'activity_1': 'ウレタン棒', 'activity_2': 'ブロック',
                     'cautions': '・大きな音が苦手\n・疲れると手が出る。休憩を先に入れる'}

    def reply(self, client_cls, text):
        client_cls.return_value.messages.create.return_value = SimpleNamespace(content=[
            SimpleNamespace(type='thinking', thinking='...'), SimpleNamespace(type='text', text=text)])

    @mock.patch('ai_assist.quick.anthropic.Anthropic')
    def test_summary_uses_cautions_and_activities(self, client_cls):
        self.reply(client_cls, 'ウレタン棒では、疲れると手が出ることがあるため、\n休憩を先に入れて取り組む。\n\n'
                               'ブロックでは、崩れる音が大きくならないよう机の上で行う。')
        res = self.client.post(self.url, {**self.post, 'body': '今日は笑顔が多かった。'})
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()
        # 段落の中の改行はつなぎ、段落は1行ずつ
        self.assertEqual(data['result'], 'ウレタン棒では、疲れると手が出ることがあるため、休憩を先に入れて取り組む。\n'
                                         'ブロックでは、崩れる音が大きくならないよう机の上で行う。')
        self.assertEqual(data['length'], len(data['result']) - 1)
        kwargs = client_cls.return_value.messages.create.call_args.kwargs
        self.assertIn('100字以上500字以内', kwargs['system'])
        content = kwargs['messages'][0]['content']
        for word in ('青木 子さん', '大きな音が苦手', '・ウレタン棒', '・ブロック', '2026年9月24日', '今日は笑顔が多かった'):
            self.assertIn(word, content)
        self.assertFalse(TherapyRecord.objects.exists())   # 返すだけで保存しない

    @mock.patch('ai_assist.quick.anthropic.Anthropic')
    def test_saved_cautions_used_when_page_has_none(self, client_cls):
        TherapyProfile.objects.create(beneficiary=self.kid, cautions='・水が苦手')
        self.reply(client_cls, '水遊びは避ける。')
        res = self.client.post(self.url, {**self.post, 'cautions': ''})
        self.assertEqual(res.status_code, 200)
        self.assertIn('水が苦手', client_cls.return_value.messages.create.call_args.kwargs['messages'][0]['content'])

    @mock.patch('ai_assist.quick.anthropic.Anthropic')
    def test_long_reply_is_cut_at_sentence(self, client_cls):
        sentence = 'あ' * 99 + '。'           # 100字の文を6つ → 500字で切る
        self.reply(client_cls, sentence * 6)
        res = self.client.post(self.url, self.post)
        self.assertEqual(res.json()['result'], sentence * 5)
        self.assertEqual(res.json()['length'], 500)

    def test_needs_cautions_and_activities(self):
        res = self.client.post(self.url, {**self.post, 'cautions': ''})
        self.assertEqual(res.status_code, 400)
        self.assertIn('留意点', res.json()['error'])
        res = self.client.post(self.url, {'cautions': '・音が苦手', 'activity_1': ' '})
        self.assertEqual(res.status_code, 400)
        self.assertIn('やったこと', res.json()['error'])

    @override_settings(ANTHROPIC_API_KEY='')
    def test_missing_api_key(self):
        res = self.client.post(self.url, self.post)
        self.assertEqual(res.status_code, 500)
        self.assertIn('ANTHROPIC_API_KEY', res.json()['error'])

    @mock.patch('ai_assist.quick.anthropic.Anthropic', side_effect=RuntimeError('boom'))
    def test_api_error(self, _):
        res = self.client.post(self.url, self.post)
        self.assertEqual(res.status_code, 500)
        self.assertIn('エラー', res.json()['error'])

    def test_other_facility_child_is_404(self):
        other = Facility.objects.create(name='ほか', use_therapy_record=True)
        kid = Beneficiary.objects.create(facility=other, last_name='他', first_name='子', date_of_birth=datetime.date(2019, 4, 1))
        res = self.client.post(reverse('therapy:record_summary', args=[kid.pk]), self.post)
        self.assertEqual(res.status_code, 404)

    def test_button_only_on_add_form(self):
        TherapyRecord.objects.create(facility=self.f, beneficiary=self.kid, date=datetime.date(2026, 9, 1), body='前回')
        res = self.client.get(reverse('therapy:child', args=[self.kid.pk]))
        self.assertContains(res, 'id="record-summary"', count=1)
        self.assertContains(res, self.url)
        self.assertContains(res, 'id="record-body"', count=1)


class VoiceInputWiringTests(TestCase):
    """音声入力はアプリ共通の static/js/voice-input.js だけを使う（古い書き方が残っていない）"""

    def test_base_loads_shared_script_once_and_memo_uses_it(self):
        f = Facility.objects.create(name='発達支援ルーム　ゆあーず', use_therapy_record=True)
        StaffAccount.objects.create_user('ryo', password='pw12345678', facility=f, role=StaffAccount.ROLE_ADMIN)
        self.client.login(username='ryo', password='pw12345678')
        kid = Beneficiary.objects.create(facility=f, last_name='青木', first_name='子', date_of_birth=datetime.date(2019, 4, 1))
        for url in (reverse('therapy:child', args=[kid.pk]), reverse('minutes:index'), reverse('beneficiaries:detail', args=[kid.pk])):
            res = self.client.get(url)
            self.assertContains(res, 'js/voice-input.js?v=', count=1)
            self.assertContains(res, 'data-voice-target="globalMemoTextarea"')
            self.assertNotContains(res, 'new SR()')

    def test_no_other_speech_recognition_code_in_templates(self):
        from pathlib import Path
        from django.conf import settings
        hits = [str(p) for p in Path(settings.BASE_DIR, 'templates').rglob('*.html')
                if 'SpeechRecognition' in p.read_text(encoding='utf-8')]
        self.assertEqual(hits, [])

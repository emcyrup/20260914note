"""職員アプリの API のテスト"""
import datetime
import json
from types import SimpleNamespace
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import StaffAccount
from beneficiaries.models import Beneficiary, Guardian, RecipientCertificate
from facilities.models import Facility
from records.models import ActivityTag, DailyRecord
from reservations import services
from reservations.models import DayStaff, Reservation
from reservations.tests_ryoiku import ryoiku
from schedules.models import ScheduledVisit
from therapy.models import TherapyProfile, TherapyRecord

from .models import TOKEN_DAYS, MobileToken

BASE = '/api/mobile/v1/'


def child(facility, last, **kw):
    return Beneficiary.objects.create(facility=facility, last_name=last, first_name='子', last_name_kana=kw.pop('kana', ''),
                                      date_of_birth=datetime.date(2018, 4, 2), **kw)


class ApiCase(TestCase):
    def setUp(self):
        cache.clear()
        self.f = self.make_facility()
        self.user = StaffAccount.objects.create_user('app', password='pw12345678', facility=self.f, display_name='永山')
        _, self.key = MobileToken.issue(self.user, 'test')

    def make_facility(self):
        return Facility.objects.create(name='テスト事業所', use_schedule=True)

    def call(self, method, path, data=None, key=None, **params):
        key = self.key if key is None else key
        headers = {'HTTP_AUTHORIZATION': f'Bearer {key}'} if key else {}
        fn = getattr(self.client, method)
        if method == 'get':
            return fn(BASE + path, params, **headers)
        return fn(BASE + path, json.dumps(data or {}), content_type='application/json', **headers)


class AuthTests(ApiCase):
    def test_login_returns_token_and_features(self):
        res = self.call('post', 'auth/login/', {'username': 'app', 'password': 'pw12345678', 'device': 'iPhone'}, key='')
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body['facility']['name'], 'テスト事業所')
        self.assertEqual(body['features'], {'today': 'visit', 'day_staff': False, 'therapy': False,
                                            'journal': True, 'ai': False})
        self.assertEqual(self.call('get', 'me/', key=body['token']).json()['user']['name'], '永山')
        # サーバーにはトークンそのものを残さない
        self.assertFalse(MobileToken.objects.filter(key_hash=body['token']).exists())

    def test_wrong_password_and_lockout(self):
        for _ in range(10):
            res = self.call('post', 'auth/login/', {'username': 'app', 'password': 'x'}, key='')
            self.assertEqual(res.status_code, 401)
        res = self.call('post', 'auth/login/', {'username': 'app', 'password': 'pw12345678'}, key='')
        self.assertEqual((res.status_code, res.json()['code']), (429, 'locked'))

    def test_pending_account_is_told(self):
        StaffAccount.objects.create_user('wait', password='pw12345678', facility=self.f, is_active=False,
                                         signup_pending=True)
        res = self.call('post', 'auth/login/', {'username': 'wait', 'password': 'pw12345678'}, key='')
        self.assertEqual((res.status_code, res.json()['code']), (403, 'pending'))

    def test_token_required_logout_expiry_inactive(self):
        self.assertEqual(self.call('get', 'me/', key='').status_code, 401)
        self.assertEqual(self.call('get', 'me/', key='bogus').status_code, 401)
        self.assertEqual(self.call('post', 'auth/logout/').status_code, 200)
        self.assertEqual(self.call('get', 'me/').status_code, 401)
        _, key = MobileToken.issue(self.user)
        MobileToken.objects.update(last_used_at=timezone.now() - datetime.timedelta(days=TOKEN_DAYS + 1))
        self.assertEqual(self.call('get', 'me/', key=key).status_code, 401)
        _, key = MobileToken.issue(self.user)
        self.user.is_active = False
        self.user.save()
        self.assertEqual(self.call('get', 'me/', key=key).status_code, 401)

    def test_session_login_is_not_enough_and_get_only(self):
        self.client.login(username='app', password='pw12345678')
        self.assertEqual(self.call('get', 'me/', key='').status_code, 401)
        self.assertEqual(self.call('post', 'me/').status_code, 405)


class TodayVisitTests(ApiCase):
    def setUp(self):
        super().setUp()
        self.day = datetime.date(2026, 10, 2)
        self.a, self.b = child(self.f, '青木', kana='あおき'), child(self.f, '井上', kana='いのうえ')
        self.va = ScheduledVisit.objects.create(facility=self.f, beneficiary=self.a, date=self.day)
        ScheduledVisit.objects.create(facility=self.f, beneficiary=self.b, date=self.day, status='absent')
        DailyRecord.objects.create(facility=self.f, beneficiary=self.a, date=self.day, status='confirmed')

    def test_board_and_attendance(self):
        body = self.call('get', 'today/', d='2026-10-02').json()
        self.assertEqual((body['mode'], body['weekday']), ('visit', '金'))
        self.assertEqual([(i['name'], i['status'], i['journal']) for i in body['items']],
                         [('青木 子', 'scheduled', 'confirmed'), ('井上 子', 'absent', '')])
        self.assertEqual(body['items'][0]['journal_id'], DailyRecord.objects.get().pk)
        self.assertIsNone(body['items'][1]['journal_id'])
        self.assertEqual(body['counts'][0], {'label': '予定', 'value': 2})
        res = self.call('post', 'today/attendance/', {'kind': 'visit', 'id': self.va.pk, 'value': 'attended'})
        self.assertEqual(res.json()['label'], '来所')
        self.va.refresh_from_db()
        self.assertEqual(self.va.status, 'attended')
        self.assertEqual(self.call('post', 'today/attendance/',
                                   {'kind': 'visit', 'id': self.va.pk, 'value': 'x'}).status_code, 400)

    def test_other_facility_is_hidden(self):
        other = Facility.objects.create(name='よそ', use_schedule=True)
        v = ScheduledVisit.objects.create(facility=other, beneficiary=child(other, '他'), date=self.day)
        res = self.call('post', 'today/attendance/', {'kind': 'visit', 'id': v.pk, 'value': 'attended'})
        self.assertEqual(res.status_code, 404)
        self.assertEqual(self.call('post', 'today/staff/', {'date': '2026-10-02', 'text': 'x'}).status_code, 403)


class TodayReservationTests(ApiCase):
    def make_facility(self):
        f, self.s = ryoiku()
        return f

    def test_board_attendance_and_day_staff(self):
        day = datetime.date(2026, 10, 2)
        kid = child(self.f, '青木')
        res, _ = services.create_reservation(self.f, kid, day, start_time='10')
        TherapyRecord.objects.create(facility=self.f, beneficiary=kid, date=day)
        me = self.call('get', 'me/').json()['features']
        self.assertEqual((me['today'], me['day_staff'], me['therapy'], me['journal']), ('reservation', True, True, False))
        body = self.call('get', 'today/', d='2026-10-02').json()
        self.assertEqual([(i['name'], i['time'], i['status'], i['therapy']) for i in body['items']],
                         [('青木 子', '10:00', '', True)])
        self.assertEqual((body['prev'], body['next']), ('2026-09-30', '2026-10-03'))   # 木曜はお休み
        r = self.call('post', 'today/attendance/', {'kind': 'reservation', 'id': res.pk, 'value': 'attended'})
        self.assertEqual(r.json()['label'], '来た')
        res.refresh_from_db()
        self.assertEqual(res.attendance, Reservation.ATT_ATTENDED)
        r = self.call('post', 'today/staff/', {'date': '2026-10-02', 'text': '終日土田'})
        self.assertEqual(r.json()['staff'], '終日土田')
        self.assertEqual(self.call('get', 'today/', d='2026-10-02').json()['staff'], '終日土田')
        self.call('post', 'today/staff/', {'date': '2026-10-02', 'text': ''})
        self.assertFalse(DayStaff.objects.filter(facility=self.f).exists())
        # 日誌はこの型の事業所では使わない
        self.assertEqual(self.call('get', 'journal/options/').status_code, 403)


class BeneficiaryTests(ApiCase):
    def test_list_search_and_detail(self):
        a = child(self.f, '青木', kana='あおき', school_name='東小', mobile_phone='090')
        child(self.f, '井上', kana='いのうえ', status='inactive')
        other = child(Facility.objects.create(name='よそ'), '青木')
        Guardian.objects.create(beneficiary=a, last_name='青木', first_name='母', phone='080', is_primary=True)
        RecipientCertificate.objects.create(beneficiary=a, certificate_number='123', granted_days=10,
                                            valid_from=datetime.date(2026, 4, 1), valid_until=datetime.date(2099, 3, 31))
        rows = self.call('get', 'beneficiaries/').json()['beneficiaries']
        self.assertEqual([r['name'] for r in rows], ['青木 子'])
        self.assertEqual(len(self.call('get', 'beneficiaries/', all='1').json()['beneficiaries']), 2)
        self.assertEqual(len(self.call('get', 'beneficiaries/', q='あお').json()['beneficiaries']), 1)
        self.assertEqual(len(self.call('get', 'beneficiaries/', q='いない').json()['beneficiaries']), 0)
        d = self.call('get', f'beneficiaries/{a.pk}/').json()
        self.assertEqual((d['school'], d['guardians'][0]['phone'], d['certificate']['number'], d['certificate']['expired']),
                         ('東小', '080', '123', False))
        self.assertEqual(self.call('get', f'beneficiaries/{other.pk}/').status_code, 404)
        # 見るだけ：書き換える口は無い
        self.assertEqual(self.call('post', f'beneficiaries/{a.pk}/', {'last_name': 'x'}).status_code, 405)


class JournalTests(ApiCase):
    def setUp(self):
        super().setUp()
        self.kid = child(self.f, '青木')
        self.tag = ActivityTag.objects.create(facility=self.f, name='工作')
        self.other_tag = ActivityTag.objects.create(facility=Facility.objects.create(name='よそ'), name='外出')

    def test_create_list_update(self):
        res = self.call('post', f'beneficiaries/{self.kid.pk}/journal/', {
            'date': '2026-10-02', 'memo': 'ブロックで遊んだ', 'entry_time': '15:30', 'health_condition': 'normal',
            'activity_tags': [self.tag.pk, self.other_tag.pk]})
        self.assertEqual(res.status_code, 201)
        body = res.json()
        self.assertEqual((body['memo'], body['entry_time'], body['status'], body['author'], body['activity_tags']),
                         ('ブロックで遊んだ', '15:30', 'draft', '永山', [self.tag.pk]))
        # 同じ日は1つだけ
        res = self.call('post', f'beneficiaries/{self.kid.pk}/journal/', {'date': '2026-10-02'})
        self.assertEqual((res.status_code, res.json()['id']), (409, body['id']))
        rows = self.call('get', f'beneficiaries/{self.kid.pk}/journal/').json()['records']
        self.assertEqual([(r['date'], r['summary']) for r in rows], [('2026-10-02', 'ブロックで遊んだ')])
        res = self.call('put', f'journal/{body["id"]}/', {'version': body['version'], 'observation': '集中していた。',
                                                          'status': 'confirmed'})
        self.assertEqual((res.status_code, res.json()['status_label']), (200, '確定'))
        # 古い版のままの保存は止める。force なら通す
        res = self.call('put', f'journal/{body["id"]}/', {'version': body['version'], 'observation': 'x'})
        self.assertEqual((res.status_code, res.json()['code']), (409, 'conflict'))
        res = self.call('put', f'journal/{body["id"]}/', {'version': body['version'], 'observation': 'x', 'force': True})
        self.assertEqual(res.json()['observation'], 'x')

    def test_update_keeps_web_only_fields(self):
        rec = DailyRecord.objects.create(
            facility=self.f, beneficiary=self.kid, date=datetime.date(2026, 10, 2), activity_aim='順番を待つ',
            activity_viewpoints=[{'text': '待てるか', 'answer': 'yes'}], support_memo='Web で入れたメモ',
            domain_social=True, observation_text='前の文')
        rec.activity_tags.add(self.tag)
        res = self.call('put', f'journal/{rec.pk}/', {'reaction': '笑顔だった'})
        self.assertEqual(res.json()['web_only'], ['めあて・観点・考察'])
        rec.refresh_from_db()
        self.assertEqual((rec.activity_aim, rec.support_memo, rec.domain_social, rec.observation_text, rec.reaction_text),
                         ('順番を待つ', 'Web で入れたメモ', True, '前の文', '笑顔だった'))
        self.assertEqual(rec.activity_viewpoints[0]['answer'], 'yes')
        self.assertEqual(list(rec.activity_tags.all()), [self.tag])

    def test_validation_and_tenant(self):
        url = f'beneficiaries/{self.kid.pk}/journal/'
        self.assertEqual(self.call('post', url, {'date': ''}).status_code, 400)
        self.assertEqual(self.call('post', url, {'date': '2026-10-02', 'entry_time': 'abc'}).status_code, 400)
        self.assertEqual(self.call('post', url, {'date': '2026-10-02', 'status': 'zzz'}).status_code, 400)
        self.assertFalse(DailyRecord.objects.exists())
        other = Facility.objects.create(name='別')
        rec = DailyRecord.objects.create(facility=other, beneficiary=child(other, '他'), date=datetime.date(2026, 10, 2))
        self.assertEqual(self.call('get', f'journal/{rec.pk}/').status_code, 404)
        self.assertEqual(self.call('put', f'journal/{rec.pk}/', {'memo': 'x'}).status_code, 404)

    def test_options(self):
        body = self.call('get', 'journal/options/').json()
        self.assertEqual(body['activity_tags'], [{'id': self.tag.pk, 'name': '工作'}])
        self.assertEqual([s['key'] for s in body['sections']], ['observation', 'support', 'reaction', 'parent_message'])
        self.assertEqual(body['me'], self.user.pk)

    def test_ai_draft(self):
        self.assertEqual(self.call('post', 'journal/ai/', {'memo': 'x'}).json()['code'], 'ai_off')
        reply = SimpleNamespace(content=[SimpleNamespace(type='text', text='{"observation": "観察", "support": "支援"}')])
        with override_settings(ANTHROPIC_API_KEY='k'), mock.patch('records.views.anthropic.Anthropic') as m:
            m.return_value.messages.create.return_value = reply
            self.assertEqual(self.call('post', 'journal/ai/', {'memo': ''}).status_code, 400)
            res = self.call('post', 'journal/ai/', {'memo': 'ブロック', 'tags': ['工作', '体操']})
            sent = m.return_value.messages.create.call_args.kwargs['messages'][0]['content']
        self.assertEqual((res.json()['observation'], res.json()['support']), ('観察', '支援'))
        self.assertIn('ブロック', sent)
        self.assertIn('工作、体操', sent)


class TherapyTests(ApiCase):
    def make_facility(self):
        return Facility.objects.create(name='療育', use_therapy_record=True)

    def setUp(self):
        super().setUp()
        self.kid = child(self.f, '青木')

    def test_feature_off(self):
        self.f.use_therapy_record = False
        self.f.save()
        res = self.call('get', f'beneficiaries/{self.kid.pk}/therapy/')
        self.assertEqual((res.status_code, res.json()['code']), (403, 'feature_off'))

    def test_cautions_and_records(self):
        base = f'beneficiaries/{self.kid.pk}/therapy/'
        self.assertEqual(self.call('post', base + 'cautions/', {'cautions': ' 大きな音が苦手 '}).json()['cautions'],
                         '大きな音が苦手')
        self.assertEqual(TherapyProfile.objects.get(beneficiary=self.kid).cautions, '大きな音が苦手')
        res = self.call('post', base + 'records/', {'date': '2026-10-02', 'time': '10:00', 'staff_id': self.user.pk,
                                                    'activities': ['ウレタン棒', '', 'ブロック'], 'body': '落ち着いていた'})
        self.assertEqual(res.status_code, 201)
        rec = res.json()
        self.assertEqual((rec['time'], rec['staff'], rec['activity_list']), ('10:00', '永山', ['①ウレタン棒', '②ブロック']))
        body = self.call('get', base).json()
        self.assertEqual((body['cautions'], len(body['records']), body['months']), ('大きな音が苦手', 1, ['2026-10']))
        self.assertEqual(body['activity_chips'], ['ウレタン棒', 'ブロック'])
        self.assertEqual(self.call('get', base, ym='2026-09').json()['records'], [])
        res = self.call('put', f'therapy/records/{rec["id"]}/', {
            'version': rec['version'], 'date': '2026-10-03', 'staff_name': '土田', 'activities': ['トランポリン']})
        self.assertEqual((res.json()['date'], res.json()['staff'], res.json()['activities'][0]),
                         ('2026-10-03', '土田', 'トランポリン'))
        res = self.call('put', f'therapy/records/{rec["id"]}/', {'version': rec['version'], 'date': '2026-10-03'})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(self.call('post', base + 'records/', {'date': 'x'}).status_code, 400)
        self.assertEqual(self.call('delete', f'therapy/records/{rec["id"]}/').status_code, 200)
        self.assertFalse(TherapyRecord.objects.exists())

    def test_cautions_off(self):
        TherapyProfile.objects.create(beneficiary=self.kid, cautions='前に入れた文')
        self.f.therapy_no_cautions = True
        self.f.save()
        base = f'beneficiaries/{self.kid.pk}/therapy/'
        body = self.call('get', base).json()
        self.assertEqual((body['cautions_enabled'], body['cautions']), (False, ''))
        self.assertEqual(self.call('post', base + 'cautions/', {'cautions': 'x'}).status_code, 403)
        self.assertEqual(self.call('post', base + 'ai/record/', {'activities': ['a']}).status_code, 403)
        self.assertEqual(TherapyProfile.objects.get(beneficiary=self.kid).cautions, '前に入れた文')
        self.assertEqual(self.call('post', base + 'records/', {'date': '2026-10-02'}).status_code, 201)

    def test_tenant(self):
        other = Facility.objects.create(name='別', use_therapy_record=True)
        rec = TherapyRecord.objects.create(facility=other, beneficiary=child(other, '他'), date=datetime.date(2026, 10, 2))
        self.assertEqual(self.call('delete', f'therapy/records/{rec.pk}/').status_code, 404)
        self.assertEqual(self.call('get', f'beneficiaries/{rec.beneficiary_id}/therapy/').status_code, 404)
        self.assertEqual(self.call('post', f'beneficiaries/{rec.beneficiary_id}/therapy/ai/cautions/',
                                   {'text': 'x'}).status_code, 404)

    def test_ai(self):
        base = f'beneficiaries/{self.kid.pk}/therapy/ai/'
        reply = SimpleNamespace(content=[SimpleNamespace(type='text', text='- 大きな音が苦手\n- 見通しを伝える')])
        with override_settings(ANTHROPIC_API_KEY='k'), mock.patch('ai_assist.quick.anthropic.Anthropic') as m:
            m.return_value.messages.create.return_value = reply
            res = self.call('post', base + 'cautions/', {'text': 'えー大きな音が苦手で', 'mode': 'summary'})
            self.assertEqual(res.json()['result'], '・大きな音が苦手\n・見通しを伝える')
            self.assertEqual(self.call('post', base + 'record/', {'activities': []}).status_code, 400)
            res = self.call('post', base + 'record/', {'cautions': '大きな音が苦手', 'date': '2026-10-02',
                                                       'activities': ['ウレタン棒']})
            self.assertEqual(res.status_code, 200)
            self.assertIn('ウレタン棒', m.return_value.messages.create.call_args.kwargs['messages'][0]['content'])

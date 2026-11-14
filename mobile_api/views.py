"""
職員アプリ（iOS・Android）の API。

Web 版の機能のうち、現場でスマートフォンから使うものだけを JSON で出す：
きょうの予定・出欠 ／ 日誌 ／ 療育記録 ／ 利用者台帳（見るだけ）。
保存の決まり（事業所ごとの絞り込み・同時編集の確認・AI の文面）は Web の画面と同じ部品を使う。
"""
import datetime

from django.conf import settings
from django.contrib.auth import authenticate
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404

from accounts.models import StaffAccount
from beneficiaries.models import Beneficiary
from config.concurrency import saved_at_label, version_token
from config.jp_holidays import holiday_name
from config.utils import to_int
from facilities.models import Facility, SupportContentTag
from records.models import ActivityTag, DailyRecord
from schedules.models import ScheduledVisit
from therapy.models import ACTIVITY_MAX, TherapyProfile, TherapyRecord

from .auth import (ApiError, api, as_post, error, login_blocked, login_failed, login_succeeded)
from .models import MobileToken

WEEK_JP = ['月', '火', '水', '木', '金', '土', '日']
API_VERSION = 1
RECORD_TEXT_MAX = 4000
LIST_MAX = 200


# ---- 小さな部品 ---------------------------------------------------------
def _date(value, default=None):
    try:
        return datetime.date.fromisoformat(str(value or ''))
    except ValueError:
        return default


def _time(value):
    value = str(value or '').strip().replace('：', ':')
    if not value:
        return None
    try:
        h, _, m = value.partition(':')
        return datetime.time(int(h), int(m[:2] or 0))
    except ValueError:
        raise ApiError('時刻は 15:30 の形で入れてください。')


def _hm(t):
    return f'{t.hour}:{t.minute:02d}' if t else ''


def _text(data, key, limit=RECORD_TEXT_MAX):
    return str(data.get(key) or '').strip()[:limit]


def _age(born, today=None):
    today = today or datetime.date.today()
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def _choices(pairs):
    return [{'value': v, 'label': l} for v, l in pairs]


def _today_mode(facility):
    """きょうの予定の出どころ：reservation（ゆあーず：予約と実績）／visit（来所予定と出欠）／none"""
    if facility.is_ryoiku and facility.use_reservation:
        return 'reservation'
    if facility.use_schedule:
        return 'visit'
    return 'none'


def _me(user):
    f = user.facility
    return {
        'api_version': API_VERSION,
        'user': {'id': user.pk, 'username': user.username, 'name': str(user), 'role': user.get_role_display()},
        'facility': {'id': f.pk, 'name': f.name},
        'features': {
            'today': _today_mode(f),
            'day_staff': f.is_ryoiku and f.use_reservation,
            'therapy': bool(f.use_therapy_record),
            'journal': not f.is_ryoiku,
            'ai': bool(settings.ANTHROPIC_API_KEY),
        },
    }


# ---- ログイン -----------------------------------------------------------
@api(methods=('POST',), auth=False)
def login(request):
    username = str(request.json.get('username') or '').strip()
    password = str(request.json.get('password') or '')
    if not username or not password:
        return error('ユーザー名とパスワードを入れてください。')
    if login_blocked(request, username):
        return error('ログインの失敗が続いたため、しばらく受け付けません。15分ほどしてからお試しください。', 429, 'locked')
    user = authenticate(request, username=username, password=password)
    if user is None:
        login_failed(request, username)
        pending = StaffAccount.objects.filter(username__iexact=username, signup_pending=True).first()
        if pending is not None and pending.check_password(password):
            return error('このアカウントは管理者の承認待ちです。承認されるとログインできます。', 403, 'pending')
        return error('ユーザー名かパスワードが違います。', 401, 'credentials')
    login_succeeded(request, username)
    if user.facility_id is None and user.can_switch_facility:
        user.facility = Facility.objects.order_by('pk').first()
        user._facility_switched = True
    if user.facility is None:
        return error('このアカウントは事業所に所属していません。管理者に確認してください。', 403, 'no_facility')
    _, key = MobileToken.issue(user, str(request.json.get('device') or ''))
    return JsonResponse({'token': key, **_me(user)})


@api(methods=('POST',))
def logout(request):
    request.mobile_token.delete()
    return JsonResponse({'ok': True})


@api()
def me(request):
    return JsonResponse(_me(request.user))


# ---- きょうの予定・出欠 -------------------------------------------------
def _journal_status(facility, day):
    """{利用者ID: (日誌ID, 状態)}"""
    return {b: (pk, status) for b, pk, status in
            DailyRecord.objects.filter(facility=facility, date=day).values_list('beneficiary_id', 'pk', 'status')}


def _therapy_written(facility, day):
    if not facility.use_therapy_record:
        return set()
    return set(TherapyRecord.objects.filter(facility=facility, date=day).values_list('beneficiary_id', flat=True))


@api()
def today(request):
    facility = request.user.facility
    real_today = datetime.date.today()
    day = _date(request.GET.get('d'), real_today)
    mode = _today_mode(facility)
    out = {
        'mode': mode, 'date': day.isoformat(), 'today': real_today.isoformat(),
        'weekday': WEEK_JP[day.weekday()], 'holiday': holiday_name(day) or '',
        'closed': False, 'staff': '', 'staff_suggestions': [],
        'prev': (day - datetime.timedelta(days=1)).isoformat(),
        'next': (day + datetime.timedelta(days=1)).isoformat(),
        'items': [], 'waiting': [], 'choices': [], 'counts': [],
    }
    journal = _journal_status(facility, day) if not facility.is_ryoiku else {}
    therapy = _therapy_written(facility, day)

    def flags(beneficiary_id):
        journal_id, status = journal.get(beneficiary_id, (None, ''))
        return {'journal': status, 'journal_id': journal_id, 'therapy': beneficiary_id in therapy}

    if mode == 'reservation':
        from reservations import monthly, services
        from reservations.models import Reservation
        b = monthly.day_board(facility, day, services.get_setting(facility))

        def row(r):
            return {'kind': 'reservation', 'id': r.pk, 'beneficiary_id': r.beneficiary_id, 'name': r.display_name,
                    'time': r.time_label, 'status': r.attendance, 'note': r.note, **flags(r.beneficiary_id)}
        out.update({
            'closed': b['closed'], 'staff': b['staff'], 'prev': b['prev'].isoformat(), 'next': b['next'].isoformat(),
            'staff_suggestions': monthly.staff_suggestions(facility),
            'items': [row(r) for r in b['reservations']], 'waiting': [row(r) for r in b['waiting']],
            'choices': _choices(Reservation.ATT_CHOICES),
            'counts': [{'label': '予定', 'value': b['total']}, {'label': '来た', 'value': b['attended']},
                       {'label': '欠席', 'value': b['absent']}, {'label': 'キャンセル', 'value': b['cancelled']}],
        })
    elif mode == 'visit':
        visits = list(ScheduledVisit.objects.filter(facility=facility, date=day).select_related('beneficiary')
                      .order_by('beneficiary__last_name_kana', 'beneficiary__first_name_kana', 'pk'))
        out['items'] = [{
            'kind': 'visit', 'id': v.pk, 'beneficiary_id': v.beneficiary_id, 'name': v.beneficiary.full_name,
            'time': '', 'status': v.status, 'note': v.notes,
            'pickup': v.has_pickup, 'dropoff': v.has_dropoff, **flags(v.beneficiary_id),
        } for v in visits]
        out['choices'] = _choices(ScheduledVisit.STATUS_CHOICES)
        n = {k: sum(1 for v in visits if v.status == k) for k, _ in ScheduledVisit.STATUS_CHOICES}
        out['counts'] = [{'label': '予定', 'value': len(visits)},
                         {'label': '来所', 'value': n[ScheduledVisit.STATUS_ATTENDED]},
                         {'label': '欠席', 'value': n[ScheduledVisit.STATUS_ABSENT]}]
    return JsonResponse(out)


@api(methods=('POST',))
def today_attendance(request):
    """出欠（来所予定）・実績（予約）を1人ぶん保存する"""
    facility = request.user.facility
    kind, value = request.json.get('kind'), str(request.json.get('value') or '')
    pk = to_int(request.json.get('id'), -1)
    if kind == 'reservation':
        if _today_mode(facility) != 'reservation':
            raise ApiError('実績の入力はこの事業所では使えません。', 403)
        from reservations import monthly
        from reservations.models import Reservation
        res = get_object_or_404(Reservation, pk=pk, facility=facility)
        try:
            monthly.set_attendance(res, value)
        except ValueError as e:
            raise ApiError(str(e))
        return JsonResponse({'id': res.pk, 'status': res.attendance, 'label': res.get_attendance_display()})
    if kind == 'visit':
        if not facility.use_schedule:
            raise ApiError('出欠の入力はこの事業所では使えません。', 403)
        visit = get_object_or_404(ScheduledVisit, pk=pk, facility=facility)
        if value not in dict(ScheduledVisit.STATUS_CHOICES):
            raise ApiError('出欠の値が正しくありません。')
        if visit.status != value:
            visit.status = value
            visit.save(update_fields=['status', 'updated_at'])
        return JsonResponse({'id': visit.pk, 'status': visit.status, 'label': visit.get_status_display()})
    raise ApiError('操作を選んでください。')


@api(methods=('POST',))
def today_staff(request):
    """その日の担当（ゆあーず）"""
    facility = request.user.facility
    if not (facility.is_ryoiku and facility.use_reservation):
        raise ApiError('その日の担当はこの事業所では使えません。', 403)
    day = _date(request.json.get('date'))
    if day is None:
        raise ApiError('日付が正しくありません。')
    from reservations import monthly
    from reservations.models import DayStaff
    monthly.save_day_staff(facility, {f'staff_{day.isoformat()}': str(request.json.get('text') or '')}, day, day)
    saved = DayStaff.objects.filter(facility=facility, date=day).first()
    return JsonResponse({'date': day.isoformat(), 'staff': saved.text if saved else ''})


# ---- 利用者台帳（見るだけ） ---------------------------------------------
def _beneficiary_row(b):
    return {'id': b.pk, 'name': b.full_name, 'kana': b.full_name_kana.strip(), 'age': _age(b.date_of_birth),
            'grade': b.get_grade_display() if b.grade else '', 'school': b.school_name,
            'active': b.status == Beneficiary.STATUS_ACTIVE, 'severe': b.is_severe}


@api()
def beneficiaries(request):
    facility = request.user.facility
    qs = Beneficiary.objects.filter(facility=facility)
    if request.GET.get('all') != '1':
        qs = qs.filter(status=Beneficiary.STATUS_ACTIVE)
    for word in request.GET.get('q', '').replace('　', ' ').split()[:5]:
        qs = qs.filter(Q(last_name__icontains=word) | Q(first_name__icontains=word)
                       | Q(last_name_kana__icontains=word) | Q(first_name_kana__icontains=word))
    qs = qs.order_by('last_name_kana', 'first_name_kana', 'pk')
    return JsonResponse({'beneficiaries': [_beneficiary_row(b) for b in qs]})


@api()
def beneficiary_detail(request, pk):
    facility = request.user.facility
    b = get_object_or_404(Beneficiary.objects.prefetch_related('recipient_certificates', 'guardians'),
                          pk=pk, facility=facility)
    cert = b.latest_certificate
    today_ = datetime.date.today()
    return JsonResponse({
        **_beneficiary_row(b),
        'date_of_birth': b.date_of_birth.isoformat(), 'gender': b.get_gender_display() if b.gender else '',
        'disability_type': b.disability_type, 'notes': b.notes,
        'postal_code': b.postal_code, 'address': b.address,
        'mobile_phone': b.mobile_phone, 'home_phone': b.home_phone,
        'weekdays': b.scheduled_weekdays_display,
        'admission_date': b.admission_date.isoformat() if b.admission_date else '',
        'guardians': [{
            'id': g.pk, 'name': g.full_name, 'relation': g.get_relation_display(), 'phone': g.phone,
            'phone2': g.phone2, 'email': g.email, 'primary': g.is_primary, 'memo': g.memo,
        } for g in sorted(b.guardians.all(), key=lambda g: (not g.is_primary, g.pk))],
        'certificate': None if cert is None else {
            'number': cert.certificate_number, 'granted_days': cert.granted_days, 'monthly_cap': cert.monthly_cap,
            'valid_from': cert.valid_from.isoformat(), 'valid_until': cert.valid_until.isoformat(),
            'municipality': cert.municipality, 'support_office': cert.support_office,
            'expired': cert.valid_until < today_,
            'days_left': (cert.valid_until - today_).days,
        },
        'journal_count': b.daily_records.count() if not facility.is_ryoiku else 0,
        'therapy_count': b.therapy_records.count() if facility.use_therapy_record else 0,
    })


# ---- 日誌 ---------------------------------------------------------------
JOURNAL_TEXT_FIELDS = {     # アプリでの名前 → DailyRecord の欄
    'memo': 'observation_memo', 'observation': 'observation_text', 'support': 'support_text',
    'reaction': 'reaction_text', 'parent_message': 'parent_message_draft', 'health_note': 'health_note',
}


def _journal_row(r, full=False):
    out = {
        'id': r.pk, 'beneficiary_id': r.beneficiary_id, 'date': r.date.isoformat(),
        'weekday': WEEK_JP[r.date.weekday()], 'status': r.status, 'status_label': r.get_status_display(),
        'activity_name': r.activity_name, 'author': str(r.author) if r.author_id else '',
        'health_condition': r.health_condition, 'health_label': r.get_health_condition_display(),
        'summary': (r.observation_text or r.observation_memo or r.support_text or '')[:80],
    }
    if full:
        out.update({
            'version': version_token(r), 'saved_at': saved_at_label(r),
            'beneficiary_name': r.beneficiary.full_name, 'author_id': r.author_id,
            'entry_time': _hm(r.entry_time), 'exit_time': _hm(r.exit_time),
            'activity_tags': [t.pk for t in r.activity_tags.all()],
            'support_tags': [t.pk for t in r.support_tags.all()],
            **{k: getattr(r, f) for k, f in JOURNAL_TEXT_FIELDS.items()},
            'web_only': _journal_web_only(r),
        })
    return out


def _journal_web_only(r):
    """アプリでは直せないが、Web 版で入っている項目（消えていないことを画面で伝える）"""
    names = []
    if r.activity_aim or r.activity_viewpoints or r.activity_reflection:
        names.append('めあて・観点・考察')
    if r.is_severe and r.severe_care:
        names.append('重身の記録')
    if r.collaboration_note or r.has_special_support:
        names.append('加算に関する記録')
    if r.photos.exists():
        names.append('写真')
    return names


def _apply_journal(record, data, facility, creating):
    """届いた項目だけを書き換える（届かない項目＝アプリに無い項目は、Web 版で入れた内容を残す）"""
    for key, field in JOURNAL_TEXT_FIELDS.items():
        if key in data:
            setattr(record, field, _text(data, key))
    if 'activity_name' in data:
        record.activity_name = _text(data, 'activity_name', 100)
    if 'entry_time' in data:
        record.entry_time = _time(data.get('entry_time'))
    if 'exit_time' in data:
        record.exit_time = _time(data.get('exit_time'))
    if 'health_condition' in data:
        if data['health_condition'] not in dict(DailyRecord.HEALTH_CHOICES):
            raise ApiError('体調の値が正しくありません。')
        record.health_condition = data['health_condition']
    if 'status' in data:
        if data['status'] not in dict(DailyRecord.STATUS_CHOICES):
            raise ApiError('状態の値が正しくありません。')
        record.status = data['status']
    if data.get('author_id'):
        author = facility.staff_accounts.filter(pk=to_int(data.get('author_id'), -1)).first()
        if author is not None:
            record.author = author
    record.save()
    for key, model, rel in (('activity_tags', ActivityTag, record.activity_tags),
                            ('support_tags', SupportContentTag, record.support_tags)):
        if key in data:
            ids = [to_int(x, -1) for x in (data.get(key) or [])] if isinstance(data.get(key), list) else []
            rel.set(model.objects.filter(facility=facility, pk__in=ids).values_list('pk', flat=True))
    from records.views import _suggest_addons_after_save
    _suggest_addons_after_save(record)


def _journal_guard(facility):
    if facility.is_ryoiku:
        raise ApiError('この事業所では日誌の代わりに療育記録を使います。', 403, 'feature_off')


@api()
def journal_options(request):
    """日誌の入力に使う選択肢（タグ・職員・事業所で決めた項目の順番）"""
    facility = request.user.facility
    _journal_guard(facility)
    return JsonResponse({
        'activity_tags': [{'id': t.pk, 'name': t.name}
                          for t in ActivityTag.objects.filter(facility=facility, is_active=True)],
        'support_tags': [{'id': t.pk, 'name': t.name}
                         for t in SupportContentTag.objects.filter(facility=facility, is_active=True)],
        'staff': _staff_list(facility),
        'health': _choices(DailyRecord.HEALTH_CHOICES),
        'sections': [{'key': k, 'label': Facility.JOURNAL_SECTION_LABELS[k]} for k in facility.journal_text_keys()],
        'me': request.user.pk,
    })


def _staff_list(facility):
    return [{'id': s.pk, 'name': str(s)} for s in
            StaffAccount.objects.filter(facility=facility, is_active=True).order_by('display_name', 'username')]


@api(methods=('GET', 'POST'))
def journal_list(request, pk):
    facility = request.user.facility
    _journal_guard(facility)
    b = get_object_or_404(Beneficiary, pk=pk, facility=facility)
    if request.method == 'GET':
        limit = min(max(to_int(request.GET.get('limit'), 60), 1), LIST_MAX)
        qs = DailyRecord.objects.filter(beneficiary=b).select_related('author').order_by('-date')
        before = _date(request.GET.get('before'))
        if before:
            qs = qs.filter(date__lt=before)
        rows = list(qs[:limit + 1])
        return JsonResponse({'beneficiary': _beneficiary_row(b), 'records': [_journal_row(r) for r in rows[:limit]],
                             'more': len(rows) > limit})
    data = request.json
    day = _date(data.get('date'))
    if day is None:
        raise ApiError('記録日を入れてください。')
    existing = DailyRecord.objects.filter(beneficiary=b, date=day).first()
    if existing is not None:
        # 同じ日の日誌は1つだけ。黙って上書きせず、いまある日誌を開いてもらう
        return JsonResponse({'error': f'{day.month}月{day.day}日の日誌はすでにあります（{saved_at_label(existing)} 保存）。'
                                      'その日誌を開いて続きを書いてください。',
                             'code': 'exists', 'id': existing.pk}, status=409)
    record = DailyRecord(facility=facility, beneficiary=b, date=day, author=request.user,
                         record_kind=DailyRecord.KIND_SEVERE if b.is_severe else DailyRecord.KIND_STANDARD)
    _apply_journal(record, data, facility, creating=True)
    return JsonResponse(_journal_row(record, full=True), status=201)


@api(methods=('GET', 'PUT'))
def journal_detail(request, pk):
    facility = request.user.facility
    _journal_guard(facility)
    record = get_object_or_404(DailyRecord.objects.select_related('beneficiary', 'author'), pk=pk, facility=facility)
    if request.method == 'PUT':
        data = request.json
        sent = data.get('version')
        if sent and not data.get('force') and sent != version_token(record):
            when = saved_at_label(record)
            return error(f'この日誌を開いたあとに、他の職員が{f" {when} に" if when else ""}保存しています。'
                         '保存を止めました。開き直して最新の内容を確かめてください。', 409, 'conflict')
        _apply_journal(record, data, facility, creating=False)
    return JsonResponse(_journal_row(record, full=True))


@api(methods=('POST',))
def journal_ai(request):
    """メモとタグから、観察・支援・反応・保護者向けメッセージの下書きを作る（Web 版の一括生成と同じ）"""
    _journal_guard(request.user.facility)
    from records.views import AiGenerateAllView
    if not settings.ANTHROPIC_API_KEY:
        raise ApiError('AIを使う設定がサーバーにありません。管理者に設定を依頼してください。', 503, 'ai_off')
    data = request.json
    tags = data.get('tags')
    request.POST = as_post({'memo': data.get('memo'), 'tags': '、'.join(str(t) for t in tags)
                            if isinstance(tags, list) else tags})
    return AiGenerateAllView().post(request)


# ---- 療育記録 -----------------------------------------------------------
def _therapy_row(r):
    acts = [a.strip() if isinstance(a, str) else '' for a in (r.activities or [])][:ACTIVITY_MAX]
    return {
        'id': r.pk, 'beneficiary_id': r.beneficiary_id, 'date': r.date.isoformat(),
        'weekday': WEEK_JP[r.date.weekday()], 'time': _hm(r.time),
        'staff_id': r.staff_id, 'staff': r.staff_label,
        'activities': acts + [''] * (ACTIVITY_MAX - len(acts)), 'activity_list': r.activity_list, 'body': r.body,
        'version': version_token(r),
    }


def _therapy_fields(data, facility):
    day = _date(data.get('date'))
    if day is None:
        raise ApiError('日付を入れてください。')
    staff = StaffAccount.objects.filter(facility=facility, pk=to_int(data.get('staff_id'), -1)).first()
    acts = data.get('activities') if isinstance(data.get('activities'), list) else []
    acts = [str(a or '').strip()[:100] for a in acts][:ACTIVITY_MAX]
    return {
        'date': day, 'time': _time(data.get('time')), 'staff': staff,
        'staff_name': _text(data, 'staff_name', 50) if staff is None else '',
        'activities': acts + [''] * (ACTIVITY_MAX - len(acts)), 'body': _text(data, 'body'),
    }


@api(feature='therapy')
def therapy_child(request, pk):
    from therapy.views import ACTIVITY_CHIP_MAX, activity_suggestions, filter_records
    facility = request.user.facility
    b = get_object_or_404(Beneficiary, pk=pk, facility=facility)
    profile = TherapyProfile.objects.filter(beneficiary=b).first()
    records = TherapyRecord.objects.filter(beneficiary=b).select_related('staff')
    months = [f'{d.year}-{d.month:02d}' for d in records.dates('date', 'month', order='DESC')]
    records, ym, _, _ = filter_records(records, request.GET)
    suggestions = activity_suggestions(facility)
    return JsonResponse({
        'beneficiary': _beneficiary_row(b), 'cautions': profile.cautions if profile else '',
        'records': [_therapy_row(r) for r in records[:LIST_MAX]], 'months': months, 'ym': ym,
        'activity_suggestions': suggestions[:60], 'activity_chips': suggestions[:ACTIVITY_CHIP_MAX],
        'staff': _staff_list(facility), 'me': request.user.pk,
    })


@api(methods=('POST',), feature='therapy')
def therapy_cautions(request, pk):
    from therapy.views import CAUTIONS_MAX
    b = get_object_or_404(Beneficiary, pk=pk, facility=request.user.facility)
    profile, _ = TherapyProfile.objects.get_or_create(beneficiary=b)
    profile.cautions = _text(request.json, 'cautions', CAUTIONS_MAX)
    profile.save()
    return JsonResponse({'cautions': profile.cautions})


@api(methods=('POST',), feature='therapy')
def therapy_record_create(request, pk):
    facility = request.user.facility
    b = get_object_or_404(Beneficiary, pk=pk, facility=facility)
    fields = _therapy_fields(request.json, facility)
    reservation = None
    if facility.use_reservation:
        from reservations.models import Reservation
        reservation = Reservation.objects.filter(facility=facility, beneficiary=b, date=fields['date'],
                                                 status=Reservation.STATUS_CONFIRMED).first()
    rec = TherapyRecord.objects.create(facility=facility, beneficiary=b, reservation=reservation,
                                       created_by=request.user, **fields)
    return JsonResponse(_therapy_row(rec), status=201)


@api(methods=('PUT', 'DELETE'), feature='therapy')
def therapy_record_detail(request, pk):
    facility = request.user.facility
    rec = get_object_or_404(TherapyRecord.objects.select_related('staff'), pk=pk, facility=facility)
    if request.method == 'DELETE':
        rec.delete()
        return JsonResponse({'ok': True})
    data = request.json
    sent = data.get('version')
    if sent and not data.get('force') and sent != version_token(rec):
        return error('この記録を開いたあとに、他の職員が保存しています。保存を止めました。'
                     '開き直して最新の内容を確かめてください。', 409, 'conflict')
    for k, v in _therapy_fields(data, facility).items():
        setattr(rec, k, v)
    rec.save()
    return JsonResponse(_therapy_row(rec))


@api(methods=('POST',), feature='therapy')
def therapy_ai_cautions(request, pk):
    """留意点を「短く要約」（mode=summary）／「詳しくまとめる」（mode=detail）。保存はしない"""
    from therapy.views import CautionsSummaryView
    request.POST = as_post({'text': request.json.get('text'), 'mode': request.json.get('mode')})
    return CautionsSummaryView().post(request, pk)


@api(methods=('POST',), feature='therapy')
def therapy_ai_record(request, pk):
    """留意点を、その日のやったことに照らして「記録」に足す文にする。保存はしない"""
    from therapy.views import RecordSummaryView
    data = request.json
    acts = data.get('activities') if isinstance(data.get('activities'), list) else []
    post = {'date': data.get('date'), 'body': data.get('body'), 'cautions': data.get('cautions')}
    post.update({f'activity_{i + 1}': a for i, a in enumerate(acts[:ACTIVITY_MAX])})
    request.POST = as_post(post)
    return RecordSummaryView().post(request, pk)

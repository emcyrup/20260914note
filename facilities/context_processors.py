"""呼び方・ロゴ・配色をすべてのテンプレートに渡す"""
from django.conf import settings

from ai_assist import speech


DEFAULT_TERMS = {'staff': '職員', 'beneficiary': '利用者'}


def get_terms(user):
    """ビューの文言（メッセージやページタイトル）用：施設の呼び方を返す"""
    facility = getattr(user, 'facility', None) if user is not None and getattr(user, 'is_authenticated', False) else None
    if facility is None:
        return dict(DEFAULT_TERMS)
    return {'staff': facility.term_staff or '職員', 'beneficiary': facility.term_beneficiary or '利用者'}


def developer_context(user):
    """開発向けユーザー向け：切り替えられる事業所の一覧と、いまの事業所"""
    if user is None or not getattr(user, 'is_authenticated', False) or not user.can_switch_facility:
        return {'is_developer': False, 'dev_facilities': [], 'dev_home_facility_id': None}
    from .models import Facility
    return {
        'is_developer': True,
        'dev_facilities': list(Facility.objects.order_by('pk').only('id', 'name', 'form_set')),
        'dev_home_facility_id': getattr(user, 'home_facility_id', user.facility_id),
    }


def standalone_context():
    """予約管理だけを動かすサーバー（RESERVATION_ONLY）かどうか"""
    from config.utils import home_url
    only = settings.RESERVATION_ONLY
    return {'reservation_only': only, 'home_url': home_url() if only else ''}


# ログイン前の画面（ログイン・職員登録）の名前。サーバーの事業所の画面の型から決める（介護施設だけ・居宅介護支援だけのサーバー）
SITE_BRANDS = {
    'care': {'app_label': '介護施設の業務支援', 'name': '介護施設 業務支援', 'icon': 'bi-house-heart-fill'},
    'kyotaku': {'app_label': 'ケアマネ業務支援', 'name': 'ケアマネ業務支援', 'icon': 'bi-clipboard2-heart-fill'},
}
DEFAULT_SITE_BRAND = {'app_label': '放課後等デイサービス業務支援', 'name': '放デイ業務支援', 'icon': 'bi-heart-pulse-fill'}
# 型の違う事業所が同居するサーバー（2026-11-07 介護デモの環境に放デイ デモを同居）：どちらの名前も出さない
MIXED_SITE_BRAND = {'app_label': '業務支援', 'name': 'みちノート 業務支援', 'icon': 'bi-journal-text'}


def site_brand():
    """ログイン前の画面の名前・アイコン。事業所がすべて同じ型（介護施設／居宅介護支援）ならその名前、事業所が 1 つならその名前（法人名つき）も"""
    from .models import Facility
    rows = list(Facility.objects.order_by('pk').values_list('layout', 'name', 'company_name')[:2])
    layouts = set(Facility.objects.values_list('layout', flat=True).distinct()[:3])
    if len(layouts) == 1:
        brand = dict(SITE_BRANDS.get(layouts.pop(), DEFAULT_SITE_BRAND))
    elif layouts & set(SITE_BRANDS):          # 介護施設・居宅介護支援と放デイが同居
        brand = dict(MIXED_SITE_BRAND)
    else:
        brand = dict(DEFAULT_SITE_BRAND)
    if len(rows) == 1 and rows[0][0] in SITE_BRANDS:
        brand['org'] = f'{rows[0][2]}　{rows[0][1]}' if rows[0][2] else rows[0][1]
    return brand


def branding(request):
    user = getattr(request, 'user', None)
    facility = getattr(user, 'facility', None) if user is not None and user.is_authenticated else None
    if facility is None:
        site = site_brand()
        return {'terms': dict(DEFAULT_TERMS), 'branding': {'logo': None, 'color': ''}, 'login_brand': site, 'app_label': site['app_label'],
                'features': {'billing': True, 'schedule': True, 'line': True, 'reservation': False, 'form_set': 'standard',
                             'planbook': False, 'therapy': False, 'ryoiku': False, 'kyotaku': False, 'care': False, 'kaigo': False},
                 'journal_sections': [],
                **standalone_context(), **developer_context(user)}
    return {
        'terms': get_terms(user),
        'branding': {'logo': facility.logo if facility.logo else None, 'color': facility.brand_color or ''},
        'features': {'billing': facility.use_billing, 'schedule': facility.use_schedule, 'line': facility.use_line,
                     'reservation': facility.use_reservation, 'form_set': facility.form_set,
                     'planbook': facility.is_planbook, 'therapy': facility.use_therapy_record,
                     'transport': facility.use_transport, 'dev_assessment': facility.use_dev_assessment, 'survey': facility.use_survey, 'daily': facility.use_daily_ops, 'consultation': facility.use_consultation, 'reports': facility.use_reports,
                     'ryoiku': facility.is_ryoiku, 'kyotaku': facility.is_kyotaku, 'care': facility.is_care, 'kaigo': facility.is_kaigo},
        'app_label': ('ケアマネ業務支援' if facility.is_kyotaku else '介護施設の業務支援' if facility.is_care else '放課後等デイサービス業務支援'),
        'trial_ai': _trial_ai(facility),
        'planbook_unread': _planbook_unread(facility) if facility.is_planbook else 0,
        'pending_staff_count': _pending_staff(user, facility),
        'speech_server': speech.enabled(),
        'speech_diarization': speech.diarization(),
        'journal_sections': facility.journal_section_keys(),
        **standalone_context(),
        **developer_context(user),
    }


def _planbook_unread(facility):
    """計画書中心の画面：保護者からの未読の連絡帳（メニューのバッジ用）"""
    try:
        from planbook.services import unread_note_count
        return unread_note_count(facility)
    except Exception:  # noqa: BLE001
        return 0


def _trial_ai(facility):
    """お試しの AI（記録・支援計画）の残り回数（サイドバー・設定画面用）"""
    from ai_assist.trial import status
    return status(facility)


def _pending_staff(user, facility):
    """管理者向け：ログイン画面から登録して承認を待っている職員の数"""
    if not (getattr(user, 'is_admin', False) or getattr(user, 'is_superuser', False)):
        return 0
    from accounts.models import StaffAccount
    return StaffAccount.objects.filter(facility=facility, signup_pending=True).count()

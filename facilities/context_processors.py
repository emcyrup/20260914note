"""呼び方・ロゴ・配色をすべてのテンプレートに渡す"""
from django.conf import settings


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


def branding(request):
    user = getattr(request, 'user', None)
    facility = getattr(user, 'facility', None) if user is not None and user.is_authenticated else None
    if facility is None:
        return {'terms': dict(DEFAULT_TERMS), 'branding': {'logo': None, 'color': ''},
                'features': {'billing': True, 'line': True, 'reservation': False, 'form_set': 'standard'}, 'journal_sections': [],
                **standalone_context(), **developer_context(user)}
    return {
        'terms': get_terms(user),
        'branding': {'logo': facility.logo if facility.logo else None, 'color': facility.brand_color or ''},
        'features': {'billing': facility.use_billing, 'line': facility.use_line,
                     'reservation': facility.use_reservation, 'form_set': facility.form_set},
        'journal_sections': facility.journal_section_keys(),
        **standalone_context(),
        **developer_context(user),
    }

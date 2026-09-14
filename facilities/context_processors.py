"""呼び方・ロゴ・配色をすべてのテンプレートに渡す"""


DEFAULT_TERMS = {'staff': '職員', 'beneficiary': '利用者'}


def get_terms(user):
    """ビューの文言（メッセージやページタイトル）用：施設の呼び方を返す"""
    facility = getattr(user, 'facility', None) if user is not None and getattr(user, 'is_authenticated', False) else None
    if facility is None:
        return dict(DEFAULT_TERMS)
    return {'staff': facility.term_staff or '職員', 'beneficiary': facility.term_beneficiary or '利用者'}


def branding(request):
    user = getattr(request, 'user', None)
    facility = getattr(user, 'facility', None) if user is not None and user.is_authenticated else None
    if facility is None:
        return {'terms': dict(DEFAULT_TERMS), 'branding': {'logo': None, 'color': ''},
                'features': {'billing': True, 'line': True}, 'journal_sections': []}
    return {
        'terms': get_terms(user),
        'branding': {'logo': facility.logo if facility.logo else None, 'color': facility.brand_color or ''},
        'features': {'billing': facility.use_billing, 'line': facility.use_line},
        'journal_sections': facility.journal_section_keys(),
    }

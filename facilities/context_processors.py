"""呼び方・ロゴ・配色をすべてのテンプレートに渡す"""


def branding(request):
    user = getattr(request, 'user', None)
    facility = getattr(user, 'facility', None) if user is not None and user.is_authenticated else None
    if facility is None:
        return {'terms': {'staff': '職員', 'beneficiary': '利用者'}, 'branding': {'logo': None, 'color': ''}}
    return {
        'terms': {'staff': facility.term_staff or '職員', 'beneficiary': facility.term_beneficiary or '利用者'},
        'branding': {'logo': facility.logo if facility.logo else None, 'color': facility.brand_color or ''},
    }

"""
開発向けユーザーの事業所切り替え。

開発向けユーザー（is_developer）とスーパーユーザーは、セッションに保存した事業所IDを
「いまの事業所」として扱う。既存のビューはすべて request.user.facility を見ているので、
ここでメモリ上の所属だけを差し替えれば、画面全体がその事業所として動く（DBの所属は変えない）。
"""
from facilities.models import Facility

SESSION_KEY = 'dev_facility_id'


class DevFacilityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated and user.can_switch_facility:
            self.apply(request, user)
        return self.get_response(request)

    @staticmethod
    def apply(request, user):
        user.home_facility_id = user.facility_id
        facility = None
        fid = request.session.get(SESSION_KEY)
        if fid:
            facility = Facility.objects.filter(pk=fid).first()
            if facility is None:
                request.session.pop(SESSION_KEY, None)
        if facility is None and user.facility_id is None:
            # 所属が無い開発向けユーザー／スーパーユーザーは最初の事業所で動かす
            facility = Facility.objects.order_by('pk').first()
        if facility is not None and facility.pk != user.facility_id:
            user.facility = facility
            user._facility_switched = True



class IpRestrictionMiddleware:
    """
    事業所の「使える場所（IP アドレス）」に入っていない場所からの操作を止めてログアウトする（facilities/ip_rules.py）。
    ログインの画面でも止める（accounts/views.StaffLoginView）。ヘルスチェック・静的ファイルは見ない
    """
    SKIP = ('/healthz', '/static/')

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated and not request.path.startswith(self.SKIP):
            facility = getattr(user, 'facility', None)
            if facility is not None and (facility.allowed_ips or '').strip():
                from facilities import ip_rules

                from . import security
                ip = security.client_ip(request)
                if not ip_rules.allowed(facility, user, ip):
                    from django.contrib.auth import logout
                    from django.shortcuts import render

                    from .models import LoginEvent
                    security.record(LoginEvent.KIND_IP, request, user.get_username(), user)
                    logout(request)
                    return render(request, 'accounts/ip_blocked.html', {'ip': ip, 'facility': facility}, status=403)
        return self.get_response(request)

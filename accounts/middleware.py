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

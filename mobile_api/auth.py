"""職員アプリの API の共通部品：トークンでの本人確認、JSON の受け取り、エラーの返し方"""
import datetime
import functools
import json

from django.conf import settings
from django.core.cache import cache
from django.http import Http404, HttpResponse, JsonResponse, QueryDict
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from facilities.models import Facility

from .models import MobileToken

TOUCH_EVERY = datetime.timedelta(minutes=10)   # last_used_at を書き換える間隔（毎回は書かない）
LOGIN_FAIL_MAX = 10                            # 同じユーザー名・同じ接続元でこの回数まちがえたら
LOGIN_FAIL_SECONDS = 15 * 60                   # この時間は受け付けない


class ApiError(Exception):
    def __init__(self, message, status=400, code=''):
        super().__init__(message)
        self.message, self.status, self.code = message, status, code


def error(message, status=400, code=''):
    body = {'error': message}
    if code:
        body['code'] = code
    return JsonResponse(body, status=status)


def bearer(request):
    head = request.headers.get('Authorization', '')
    scheme, _, key = head.partition(' ')
    return key.strip() if scheme.lower() == 'bearer' else ''


def client_ip(request):
    return (request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip()
            or request.META.get('REMOTE_ADDR', ''))


def _fail_key(request, username):
    return f'mobile_api:login_fail:{client_ip(request)}:{username.lower()[:150]}'


def login_blocked(request, username):
    return (cache.get(_fail_key(request, username)) or 0) >= LOGIN_FAIL_MAX


def login_failed(request, username):
    key = _fail_key(request, username)
    cache.set(key, (cache.get(key) or 0) + 1, LOGIN_FAIL_SECONDS)


def login_succeeded(request, username):
    cache.delete(_fail_key(request, username))


def as_post(data):
    """JSON で届いた内容を、Web の画面と同じ部品（request.POST を読む）へ渡せる形にする"""
    q = QueryDict(mutable=True)
    for k, v in (data or {}).items():
        if isinstance(v, (list, tuple)):
            q.setlist(k, [str(x) for x in v])
        elif v is not None:
            q[k] = 'on' if v is True else '' if v is False else str(v)
    return q


def _cors(request, response):
    """
    ブラウザ版（michinoteapp の Web 版）からの通信。settings.MOBILE_API_CORS_ORIGINS にあるアドレスからだけ通す。
    Cookie は使わず（トークンだけ）、Access-Control-Allow-Credentials は付けない
    """
    origin = request.headers.get('Origin', '')
    if origin and origin in settings.MOBILE_API_CORS_ORIGINS:
        response['Access-Control-Allow-Origin'] = origin
        response['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
        response['Access-Control-Allow-Headers'] = 'Authorization, Content-Type, Accept'
        response['Access-Control-Max-Age'] = '600'
    response['Vary'] = 'Origin'
    return response


def api(methods=('GET',), auth=True, feature=None):
    """
    API のビューに付ける。
    - メソッドの確認、JSON の読み取り（request.json）
    - auth=True なら Authorization: Bearer のトークンで request.user を決める
    - feature：'therapy'（療育記録を使う事業所だけ）
    - ApiError・404 は {"error": …} の JSON で返す
    """
    def deco(view):
        @csrf_exempt
        @functools.wraps(view)
        def wrapper(request, *args, **kwargs):
            if request.method == 'OPTIONS':     # ブラウザが本番の通信の前に送る確認（preflight）
                return _cors(request, HttpResponse(status=204))
            return _cors(request, handle(request, *args, **kwargs))

        def handle(request, *args, **kwargs):
            if request.method not in methods:
                return error('この操作はできません。', 405)
            request.json = {}
            if request.method in ('POST', 'PUT', 'PATCH', 'DELETE') and request.body:
                try:
                    data = json.loads(request.body)
                except ValueError:
                    return error('送られた内容を読み取れませんでした。', 400)
                if not isinstance(data, dict):
                    return error('送られた内容を読み取れませんでした。', 400)
                request.json = data
            if auth:
                token = MobileToken.find(bearer(request))
                if token is None or not token.user.is_active:
                    return error('ログインし直してください。', 401, 'auth')
                user = token.user
                if user.facility_id is None and user.can_switch_facility:
                    # 所属の無い開発向けユーザーは、Web と同じく最初の事業所で動かす
                    user.facility = Facility.objects.order_by('pk').first()
                    user._facility_switched = True
                if user.facility is None:
                    return error('このアカウントは事業所に所属していません。管理者に確認してください。', 403, 'no_facility')
                if timezone.now() - token.last_used_at > TOUCH_EVERY:
                    MobileToken.objects.filter(pk=token.pk).update(last_used_at=timezone.now())
                request.user, request.mobile_token = user, token
                if feature == 'therapy' and not user.facility.use_therapy_record:
                    return error('この事業所では療育記録を使わない設定になっています。', 403, 'feature_off')
            try:
                return view(request, *args, **kwargs)
            except ApiError as e:
                return error(e.message, e.status, e.code)
            except Http404:
                return error('見つかりませんでした。', 404)
        return wrapper
    return deco

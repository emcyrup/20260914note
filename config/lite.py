"""
ライト版（LITE_MODE=True）。サーバー全体を、職員アプリ（michinoteapp）と同じ機能だけにする：
ホーム ／ 利用者台帳 ／ 予定・出欠 ／ 日誌（記録）／ 療育記録 ／ 職員の管理 ／ 設定。

しくみは2つ。
1. 事業所の「使う機能」のうちライト版に無いものを、読み込んだ時点でオフとして扱う（Facility.__init__）。
   データベースの値は書き換えない（保存のときにその項目を除く）ので、LITE_MODE を外せば元の設定に戻る。
2. ライト版に無い画面の URL は、直接開いてもホームへ戻す（LiteModeMiddleware）。
"""
from django.conf import settings
from django.contrib import messages
from django.http import Http404, JsonResponse
from django.shortcuts import redirect

# ライト版ではオフとして扱う「使う機能」（予定 use_schedule と療育記録 use_therapy_record は事業所の設定のまま）
FORCED = {
    'use_billing': False, 'use_line': False, 'line_auto_send': False, 'use_reservation': False,
    'use_transport': False, 'use_dev_assessment': False, 'use_survey': False, 'use_consultation': False,
    'use_reports': False, 'use_daily_ops': False,
    # 画面の型・帳票様式は標準に（計画書中心・療育（ゆあーず）・事業所様式はフル版の機能）
    'layout': 'standard', 'form_set': 'standard',
}

# 開ける画面（URL の名前空間）。ここに無いものはホームへ戻す
ALLOWED_APPS = {'accounts', 'facilities', 'beneficiaries', 'schedules', 'records', 'therapy', 'mobile_api', 'admin'}
# 名前空間のない URL（ヘルスチェック・アップロードファイル）
ALLOWED_NAMES = {'healthz', 'protected_media'}
# 開ける名前空間の中で、ライト版に無いもの（加算マスタ＝請求の設定）
BLOCKED = {('facilities', 'addon_setting'), ('facilities', 'addon_defaults')}
# 閉じた名前空間の中で、記録の入力に要るもの（音声入力・言いかえの確認）
EXTRA = {('ai_assist', 'transcribe'), ('ai_assist', 'voice_report'), ('ai_assist', 'wording_check')}

MESSAGE = 'この機能はライト版では使えません。'


def enabled():
    return bool(getattr(settings, 'LITE_MODE', False))


def apply(facility):
    """読み込んだ事業所に、ライト版の決まりをかぶせる（メモリの上だけ）"""
    for name, value in FORCED.items():
        setattr(facility, name, value)


def saveable_fields(model):
    """保存してよい項目（かぶせた項目は除く。データベースの元の値を残す）"""
    return [f.name for f in model._meta.concrete_fields if not f.primary_key and f.name not in FORCED]


def is_allowed(match):
    """URL の解決結果（ResolverMatch）が、ライト版で開ける画面か"""
    app = match.app_names[0] if match.app_names else ''
    key = (app, match.url_name or '')
    if key in BLOCKED:
        return False
    if key in EXTRA:
        return True
    if app:
        return app in ALLOWED_APPS
    return match.url_name in ALLOWED_NAMES


class LiteModeMiddleware:
    """ライト版に無い画面を開こうとしたら、ホームへ戻す（ログインしていなければ 404）"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        if not enabled():
            return None
        match = request.resolver_match
        if match is None or is_allowed(match):
            return None
        user = getattr(request, 'user', None)
        if user is None or not user.is_authenticated:
            raise Http404
        wants_json = (request.headers.get('X-Requested-With') == 'XMLHttpRequest'
                      or 'application/json' in request.headers.get('Accept', ''))
        if request.method != 'GET' or wants_json:
            return JsonResponse({'error': MESSAGE}, status=403)
        messages.info(request, MESSAGE)
        return redirect('facilities:dashboard')

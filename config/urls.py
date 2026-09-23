from django.contrib import admin
from django.urls import path, include
from django.http import HttpResponse

from facilities.media import ProtectedMediaView


def healthz(request):
    """コンテナ／ロードバランサーのヘルスチェック用（認証なし・DBに触らない）"""
    return HttpResponse('ok', content_type='text/plain')


urlpatterns = [
    path('healthz/', healthz, name='healthz'),
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('', include('facilities.urls')),
    path('users/', include('beneficiaries.urls')),
    path('schedules/', include('schedules.urls')),
    path('records/', include('records.urls')),
    path('plans/', include('support_plans.urls')),
    path('billing/', include('billing.urls')),
    path('reports/', include('reports.urls')),
    path('forms/', include('custom_forms.urls')),
    path('planbook/', include('planbook.urls')),
    path('reservations/', include('reservations.urls')),
    path('therapy/', include('therapy.urls')),
    path('minutes/', include('minutes.urls')),
    # 顧客向けの予定表（ログインなし。アドレスそのものが合い言葉）
    path('yoyaku/', include('reservations.public_urls')),
    path('esignatures/', include('esignatures.urls')),
    path('line/', include('line_integration.urls')),
    path('ai/', include('ai_assist.urls')),
    # アップロードファイルはログイン中の職員の事業所のものだけ返す（署名 URL なら外部からも可）。
    # 前段の nginx / Caddy で /media/ を直接配信しないこと。
    path('media/<path:path>', ProtectedMediaView.as_view(), name='protected_media'),
]

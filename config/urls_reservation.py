"""
予約管理だけを動かすときの URL（RESERVATION_ONLY=True）。

別サーバーでの横展開用。コードは同じまま、外から見える画面を
「予約カレンダー・顧客・公式LINE・職員」と、顧客向けの予定表だけに絞る。
記録・請求・支援計画などの画面は、この URL には載せない。
"""
from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path
from django.views.generic import RedirectView

from facilities.media import ProtectedMediaView


def healthz(request):
    """コンテナ／ロードバランサーのヘルスチェック用（認証なし・DBに触らない）"""
    return HttpResponse('ok', content_type='text/plain')


urlpatterns = [
    path('healthz/', healthz, name='healthz'),
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    # ログイン後の入口は予約カレンダー
    path('', RedirectView.as_view(pattern_name='reservations:calendar', permanent=False), name='home'),
    path('reservations/', include('reservations.urls')),
    # 顧客向けの予定表（ログインなし）
    path('yoyaku/', include('reservations.public_urls')),
    # 公式LINEの Webhook（事業所ごと）。配信履歴などの画面はこの構成には載せない
    path('line/', include('line_integration.urls_webhook')),
    path('media/<path:path>', ProtectedMediaView.as_view(), name='protected_media'),
]

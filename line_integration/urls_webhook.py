"""
Webhook だけの URL（予約管理を単体で動かすサーバー用）。

日誌の配信履歴など、記録アプリに結びついた画面はここには載せない。
URL の名前は通常構成と同じにしてあるので、テンプレートの {% url %} はそのまま使える。
"""
from django.urls import path

from . import views

app_name = 'line_integration'

urlpatterns = [
    path('webhook/<int:facility_pk>/', views.LineWebhookView.as_view(), name='webhook_facility'),
    path('webhook/', views.LineWebhookView.as_view(), name='webhook'),
]

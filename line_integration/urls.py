"""
LINE連携のURLルーティング
"""

from django.urls import path
from . import views

app_name = 'line_integration'

urlpatterns = [
    # LINE Webhookエンドポイント（LINE Developers Consoleに登録するURL）
    path('webhook/', views.LineWebhookView.as_view(), name='webhook'),
    # 保護者向けメッセージをLINEで送信する
    path('send/<int:record_pk>/', views.SendLineMessageView.as_view(), name='send'),
    # LINE配信履歴一覧
    path('logs/', views.DeliveryLogView.as_view(), name='delivery_log'),
]

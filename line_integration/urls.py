"""
LINE連携のURLルーティング
"""

from django.urls import path
from . import views

app_name = 'line_integration'

urlpatterns = [
    # LINE Webhookエンドポイント（LINE Developers Consoleに登録するURL）。事業所ごとに URL が違う
    path('webhook/<int:facility_pk>/', views.LineWebhookView.as_view(), name='webhook_facility'),
    # 旧 URL：LINE を設定している事業所が1つだけのときに限り、その事業所として扱う
    path('webhook/', views.LineWebhookView.as_view(), name='webhook'),
    # 保護者向けメッセージをLINEで送信する
    path('send/<int:record_pk>/', views.SendLineMessageView.as_view(), name='send'),
    # LINE配信履歴一覧
    path('logs/', views.DeliveryLogView.as_view(), name='delivery_log'),
]

"""
顧客向けの予定表（ログインなし）。アドレスそのものが合い言葉になる。

道の名前は「空き状況（aki）」と「マイページ（mypage）」で分けてある。
1字だけ違うアドレスにならないよう、似た綴りは使わない。
アドレスには署名が入っているので、1字でも書き換えると開けない（`reservations/tokens.py`）。
"""
from django.urls import path

from . import public_views

app_name = 'reservations_public'

urlpatterns = [
    path('aki/<str:token>/', public_views.PublicCalendarView.as_view(), name='calendar'),
    path('aki/<str:token>/<int:year>/<int:month>/', public_views.PublicCalendarView.as_view(),
         name='calendar_month'),
    path('mypage/<str:token>/', public_views.CustomerPageView.as_view(), name='customer'),
    path('mypage/<str:token>/<int:year>/<int:month>/', public_views.CustomerPageView.as_view(),
         name='customer_month'),
]

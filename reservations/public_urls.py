"""顧客向けの予定表（ログインなし）。アドレスそのものが合い言葉になる"""
from django.urls import path

from . import public_views

app_name = 'reservations_public'

urlpatterns = [
    path('f/<str:token>/', public_views.PublicCalendarView.as_view(), name='calendar'),
    path('f/<str:token>/<int:year>/<int:month>/', public_views.PublicCalendarView.as_view(),
         name='calendar_month'),
    path('c/<str:token>/', public_views.CustomerPageView.as_view(), name='customer'),
    path('c/<str:token>/<int:year>/<int:month>/', public_views.CustomerPageView.as_view(),
         name='customer_month'),
]

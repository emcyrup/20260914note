from django.urls import path

from . import views

app_name = 'reservations'

urlpatterns = [
    path('', views.CalendarView.as_view(), name='calendar'),
    path('<int:year>/<int:month>/', views.CalendarView.as_view(), name='calendar_month'),
    path('settings/', views.SettingView.as_view(), name='settings'),
    path('<int:year>/<int:month>/<int:day>/', views.DayView.as_view(), name='day'),
    path('<int:year>/<int:month>/csv/', views.CsvView.as_view(), name='csv'),
    path('customers/', views.CustomerListView.as_view(), name='customers'),
    path('customers/<int:pk>/save/', views.CustomerListView.as_view(), name='customer_save'),
    path('customers/<int:pk>/delete/', views.CustomerDeleteView.as_view(), name='customer_delete'),
    path('line/', views.LineView.as_view(), name='line'),
]

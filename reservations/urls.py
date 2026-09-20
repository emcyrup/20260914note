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
    path('customers/<int:pk>/reissue/', views.CustomerTokenView.as_view(), name='customer_reissue'),
    path('requests/', views.RequestListView.as_view(), name='requests'),
    path('requests/<int:pk>/', views.RequestListView.as_view(), name='request_action'),
    path('line/', views.LineView.as_view(), name='line'),
    # 時間枠で予約する事業所：月予約利用希望・月間予定表
    path('<int:year>/<int:month>/kibou/', views.MonthlyRequestListView.as_view(), name='monthly_requests'),
    path('<int:year>/<int:month>/kibou/form/', views.MonthlyRequestFormView.as_view(), name='monthly_request_form'),
    path('<int:year>/<int:month>/kibou/<int:pk>/', views.MonthlyRequestEditView.as_view(), name='monthly_request_edit'),
    path('<int:year>/<int:month>/yotei/', views.MonthlyScheduleView.as_view(), name='monthly_schedule'),
    path('<int:year>/<int:month>/yotei/pdf/', views.MonthlySchedulePdfView.as_view(), name='monthly_schedule_pdf'),
]

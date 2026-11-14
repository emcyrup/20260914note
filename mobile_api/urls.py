from django.urls import path

from . import views

app_name = 'mobile_api'

urlpatterns = [
    path('auth/login/', views.login, name='login'),
    path('auth/logout/', views.logout, name='logout'),
    path('me/', views.me, name='me'),
    path('today/', views.today, name='today'),
    path('today/attendance/', views.today_attendance, name='today_attendance'),
    path('today/staff/', views.today_staff, name='today_staff'),
    path('beneficiaries/', views.beneficiaries, name='beneficiaries'),
    path('beneficiaries/<int:pk>/', views.beneficiary_detail, name='beneficiary'),
    path('beneficiaries/<int:pk>/journal/', views.journal_list, name='journal_list'),
    path('journal/options/', views.journal_options, name='journal_options'),
    path('journal/ai/', views.journal_ai, name='journal_ai'),
    path('journal/<int:pk>/', views.journal_detail, name='journal'),
    path('beneficiaries/<int:pk>/therapy/', views.therapy_child, name='therapy_child'),
    path('beneficiaries/<int:pk>/therapy/cautions/', views.therapy_cautions, name='therapy_cautions'),
    path('beneficiaries/<int:pk>/therapy/records/', views.therapy_record_create, name='therapy_record_create'),
    path('beneficiaries/<int:pk>/therapy/ai/cautions/', views.therapy_ai_cautions, name='therapy_ai_cautions'),
    path('beneficiaries/<int:pk>/therapy/ai/record/', views.therapy_ai_record, name='therapy_ai_record'),
    path('therapy/records/<int:pk>/', views.therapy_record_detail, name='therapy_record'),
]

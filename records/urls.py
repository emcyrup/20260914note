from django.urls import path
from . import views

app_name = 'records'

urlpatterns = [
    path('', views.RecordsDashboardView.as_view(), name='dashboard'),
    path('simple/', views.SimpleHomeView.as_view(), name='simple_home'),
    path('simple/<int:beneficiary_pk>/', views.SimpleRecordView.as_view(), name='simple_record'),
    path('<int:beneficiary_pk>/', views.DailyRecordListView.as_view(), name='list'),
    path('<int:beneficiary_pk>/new/', views.DailyRecordCreateView.as_view(), name='create'),
    path('<int:pk>/edit/', views.DailyRecordUpdateView.as_view(), name='update'),
    path('ai/polish/', views.AiPolishView.as_view(), name='ai_polish'),
    path('ai/generate-all/', views.AiGenerateAllView.as_view(), name='ai_generate_all'),
    path('ai/activity-plan/', views.AiActivityPlanView.as_view(), name='ai_activity_plan'),
    path('photo/<int:photo_pk>/delete/', views.DailyRecordPhotoDeleteView.as_view(), name='photo_delete'),
    path('memo/create/', views.StaffMemoCreateView.as_view(), name='memo_create'),
    path('memo/<int:pk>/delete/', views.StaffMemoDeleteView.as_view(), name='memo_delete'),
    path('memo/<int:pk>/edit/', views.StaffMemoUpdateView.as_view(), name='memo_edit'),
]

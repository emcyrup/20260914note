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
    path('templates/', views.TemplateListView.as_view(), name='template_list'),
    path('templates/from/<int:record_pk>/', views.TemplateCreateFromRecordView.as_view(), name='template_from_record'),
    path('templates/<int:pk>/apply/', views.TemplateApplyView.as_view(), name='template_apply'),
    path('templates/<int:pk>/edit/', views.TemplateUpdateView.as_view(), name='template_edit'),
    path('templates/<int:pk>/delete/', views.TemplateDeleteView.as_view(), name='template_delete'),
    path('memo/create/', views.StaffMemoCreateView.as_view(), name='memo_create'),
    path('memo/<int:pk>/delete/', views.StaffMemoDeleteView.as_view(), name='memo_delete'),
    path('memo/<int:pk>/edit/', views.StaffMemoUpdateView.as_view(), name='memo_edit'),
]

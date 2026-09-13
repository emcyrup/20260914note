from django.urls import path

from . import views

app_name = 'support_plans'

urlpatterns = [
    path('', views.PlanListView.as_view(), name='list'),
    path('new/', views.PlanCreateView.as_view(), name='create'),
    path('<int:pk>/', views.PlanDetailView.as_view(), name='detail'),
    path('<int:pk>/print/', views.PlanPrintView.as_view(), name='print'),
    path('<int:pk>/pdf/', views.PlanPdfView.as_view(), name='pdf'),
    path('<int:pk>/monitoring-report/', views.MonitoringReportView.as_view(), name='monitoring_report'),
    path('<int:pk>/monitoring-report/pdf/', views.MonitoringReportPdfView.as_view(), name='monitoring_pdf'),
    path('<int:pk>/step/<int:n>/', views.PlanStepView.as_view(), name='step'),
    path('<int:pk>/step/<int:n>/reopen/', views.PlanStepReopenView.as_view(), name='step_reopen'),
    path('<int:pk>/ai/assessment/', views.AssessmentDraftView.as_view(), name='ai_assessment'),
    path('<int:pk>/ai/draft/', views.PlanDraftView.as_view(), name='ai_draft'),
    path('<int:pk>/goals/<int:goal_pk>/evidence/', views.GoalEvidenceView.as_view(), name='goal_evidence'),
    path('<int:pk>/goals/add/', views.GoalSaveView.as_view(), name='goal_add'),
    path('<int:pk>/goals/<int:goal_pk>/edit/', views.GoalSaveView.as_view(), name='goal_edit'),
    path('<int:pk>/goals/<int:goal_pk>/delete/', views.GoalDeleteView.as_view(), name='goal_delete'),
    path('<int:pk>/monitoring/new/', views.MonitoringRecordCreateView.as_view(), name='monitoring_add'),
    path('<int:pk>/monitoring/<int:record_pk>/delete/', views.MonitoringRecordDeleteView.as_view(), name='monitoring_delete'),
    path('<int:pk>/successor/', views.SuccessorCreateView.as_view(), name='successor'),
]

from django.urls import path

from . import views

app_name = 'planbook'

urlpatterns = [
    path('', views.StudentListView.as_view(), name='students'),
    path('students/import/', views.StudentImportView.as_view(), name='student_import'),
    path('students/import/template.csv', views.StudentImportTemplateView.as_view(), name='student_import_template'),
    path('students/new/', views.StudentCreateView.as_view(), name='student_create'),
    path('students/<int:pk>/', views.StudentView.as_view(), name='student'),
    path('students/<int:pk>/delete/', views.StudentDeleteView.as_view(), name='student_delete'),
    path('students/<int:pk>/periods/new/', views.PeriodCreateView.as_view(), name='period_create'),
    path('students/<int:pk>/plans/<int:plan_pk>/<slug:tab>/', views.PlanTabView.as_view(), name='plan_tab'),
    path('plans/<int:plan_pk>/goals/', views.GoalsSaveView.as_view(), name='goals_save'),
    path('plans/<int:plan_pk>/stage/<slug:stage>/complete/', views.StageCompleteView.as_view(), name='stage_complete'),
    path('plans/<int:plan_pk>/stage/<slug:stage>/reopen/', views.StageReopenView.as_view(), name='stage_reopen'),
    path('plans/<int:plan_pk>/monitoring/add/', views.MonitoringAddView.as_view(), name='monitoring_add'),
    path('plans/<int:plan_pk>/monitoring/<int:record_pk>/delete/', views.MonitoringDeleteView.as_view(), name='monitoring_delete'),
    path('deadlines/', views.DeadlineListView.as_view(), name='deadlines'),
    path('staff/', views.StaffListView.as_view(), name='staff'),
    path('guardians/', views.GuardianListView.as_view(), name='guardians'),
    path('guardians/<int:pk>/', views.GuardianDetailView.as_view(), name='guardian'),
    path('guardians/<int:pk>/delete/', views.GuardianDeleteView.as_view(), name='guardian_delete'),
    path('notes/', views.NoteListView.as_view(), name='notes'),
    path('notes/<int:guardian_pk>/', views.NoteThreadView.as_view(), name='note_thread'),
    path('facility/', views.FacilityInfoView.as_view(), name='facility'),
]

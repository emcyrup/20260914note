from django.urls import path

from . import views

app_name = 'kaigo'

urlpatterns = [
    path('providers/', views.ProviderListView.as_view(), name='providers'),
    path('monthly/', views.MonthlyView.as_view(), name='monthly_now'),
    path('monthly/<int:year>/<int:month>/', views.MonthlyView.as_view(), name='monthly'),
    path('users/<int:pk>/insurance/new/', views.InsuranceView.as_view(), name='insurance_new'),
    path('users/<int:pk>/insurance/<int:ins_pk>/', views.InsuranceView.as_view(), name='insurance_edit'),
    path('users/<int:pk>/logs/', views.LogListView.as_view(), name='logs'),
    path('users/<int:pk>/logs/print/', views.LogPrintView.as_view(), name='logs_print'),
    path('users/<int:pk>/uses/', views.UsesNowView.as_view(), name='uses_now'),
    path('users/<int:pk>/uses/<int:year>/<int:month>/', views.UsesView.as_view(), name='uses'),
    path('users/<int:pk>/uses/<int:year>/<int:month>/print/', views.UsesPrintView.as_view(), name='uses_print'),
    path('users/<int:pk>/plans/', views.PlanListView.as_view(), name='plans'),
    path('users/<int:pk>/plans/new/', views.PlanFormView.as_view(), name='plan_new'),
    path('users/<int:pk>/plans/<int:plan_pk>/', views.PlanFormView.as_view(), name='plan_edit'),
    path('users/<int:pk>/plans/<int:plan_pk>/print/', views.PlanPrintView.as_view(), name='plan_print'),
    path('users/<int:pk>/meetings/', views.MeetingListView.as_view(), name='meetings'),
    path('users/<int:pk>/meetings/new/', views.MeetingFormView.as_view(), name='meeting_new'),
    path('users/<int:pk>/meetings/<int:meeting_pk>/', views.MeetingFormView.as_view(), name='meeting_edit'),
    path('users/<int:pk>/meetings/<int:meeting_pk>/print/', views.MeetingPrintView.as_view(), name='meeting_print'),
    path('users/<int:pk>/assessments/', views.AssessmentListView.as_view(), name='assessments'),
    path('users/<int:pk>/assessments/new/', views.AssessmentFormView.as_view(), name='assessment_new'),
    path('users/<int:pk>/assessments/<int:a_pk>/', views.AssessmentFormView.as_view(), name='assessment_edit'),
    path('users/<int:pk>/assessments/<int:a_pk>/print/', views.AssessmentPrintView.as_view(), name='assessment_print'),
]

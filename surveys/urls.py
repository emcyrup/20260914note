from django.urls import path

from . import views

app_name = 'surveys'

urlpatterns = [
    path('', views.IndexView.as_view(), name='index'),
    path('new/', views.SurveyCreateView.as_view(), name='create'),
    path('<int:pk>/', views.SurveyDetailView.as_view(), name='detail'),
    path('<int:pk>/edit/', views.SurveyEditView.as_view(), name='edit'),
    path('<int:pk>/line/', views.SurveyLineView.as_view(), name='line'),
    path('<int:pk>/delete/', views.SurveyDeleteView.as_view(), name='delete'),
    path('<int:pk>/responses/<int:response_pk>/delete/', views.ResponseDeleteView.as_view(), name='response_delete'),
    path('<int:pk>/notice/pdf/', views.NoticePdfView.as_view(), name='notice_pdf'),
    path('<int:pk>/results/pdf/', views.ResultsPdfView.as_view(), name='results_pdf'),
    path('self/new/', views.SelfEvaluationCreateView.as_view(), name='self_create'),
    path('self/<int:pk>/', views.SelfEvaluationView.as_view(), name='self_edit'),
    path('self/<int:pk>/pdf/', views.SelfEvaluationPdfView.as_view(), name='self_pdf'),
]

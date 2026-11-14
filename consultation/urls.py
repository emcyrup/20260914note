from django.urls import path

from . import views

app_name = 'consultation'

urlpatterns = [
    path('', views.IndexView.as_view(), name='index'),
    path('new/', views.EditView.as_view(), name='create'),
    path('<int:pk>/', views.EditView.as_view(), name='edit'),
    path('<int:pk>/delete/', views.DeleteView.as_view(), name='delete'),
    path('<int:pk>/ai/', views.AiDraftView.as_view(), name='ai_draft'),
    path('<int:pk>/pdf/basic/', views.BasicPdfView.as_view(), name='pdf_basic'),
    path('<int:pk>/pdf/plan/', views.PlanPdfView.as_view(), name='pdf_plan'),
    path('<int:pk>/pdf/weekly/', views.WeeklyPdfView.as_view(), name='pdf_weekly'),
]

from django.urls import path

from . import views

app_name = 'therapy'

urlpatterns = [
    path('', views.IndexView.as_view(), name='index'),
    path('<int:pk>/', views.ChildView.as_view(), name='child'),
    path('<int:pk>/cautions/summary/', views.CautionsSummaryView.as_view(), name='cautions_summary'),
    path('<int:pk>/pdf/', views.PdfView.as_view(), name='pdf'),
]

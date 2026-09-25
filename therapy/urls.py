from django.urls import path

from . import views

app_name = 'therapy'

urlpatterns = [
    path('', views.IndexView.as_view(), name='index'),
    path('search/', views.SearchView.as_view(), name='search'),
    path('<int:pk>/', views.ChildView.as_view(), name='child'),
    path('<int:pk>/cautions/summary/', views.CautionsSummaryView.as_view(), name='cautions_summary'),
    path('<int:pk>/cautions/figure/', views.CautionsFigureView.as_view(), name='cautions_figure'),
    path('<int:pk>/cautions/figure/print/', views.CautionsFigurePrintView.as_view(), name='cautions_figure_print'),
    path('<int:pk>/record/summary/', views.RecordSummaryView.as_view(), name='record_summary'),
    path('<int:pk>/pdf/', views.PdfView.as_view(), name='pdf'),
]

from django.urls import path

from . import views

app_name = 'homecare'

urlpatterns = [
    path('', views.DayView.as_view(), name='day'),
    path('yotei/', views.MonthNowView.as_view(), name='month_now'),
    path('yotei/<int:year>/<int:month>/', views.MonthView.as_view(), name='month'),
    path('visit/new/', views.VisitFormView.as_view(), name='visit_new'),
    path('visit/<int:pk>/', views.VisitFormView.as_view(), name='visit_edit'),
    path('visit/<int:pk>/record/', views.RecordView.as_view(), name='record'),
    path('tidy/', views.TidyView.as_view(), name='tidy'),
    path('records/', views.RecordListView.as_view(), name='records'),
    path('records/<int:year>/<int:month>/<int:beneficiary_pk>/print/', views.RecordPrintView.as_view(), name='records_print'),
    path('users/<int:pk>/', views.PersonView.as_view(), name='person'),
    path('plans/', views.PlanListView.as_view(), name='plans'),
    path('plans/new/', views.PlanFormView.as_view(), name='plan_new'),
    path('plans/<int:pk>/', views.PlanFormView.as_view(), name='plan_edit'),
    path('plans/<int:pk>/print/', views.PlanPrintView.as_view(), name='plan_print'),
    path('jisseki/', views.JisseikiNowView.as_view(), name='jisseki_now'),
    path('jisseki/<int:year>/<int:month>/', views.JisseikiView.as_view(), name='jisseki'),
    path('jisseki/<int:year>/<int:month>/<int:beneficiary_pk>/print/', views.JisseikiPrintView.as_view(), name='jisseki_print'),
]

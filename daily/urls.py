from django.urls import path

from . import views

app_name = 'daily'

urlpatterns = [
    path('groups/', views.GroupsView.as_view(), name='groups'),
    path('groups/<int:pk>/', views.GroupWeekView.as_view(), name='group_week'),
    path('groups/<int:pk>/pdf/', views.GroupWeekPdfView.as_view(), name='group_week_pdf'),
    path('groups/<int:pk>/<str:day>/', views.SessionView.as_view(), name='session'),
    path('health/', views.HealthDayView.as_view(), name='health'),
    path('health/child/<int:pk>/', views.HealthChildView.as_view(), name='health_child'),
    path('health/child/<int:pk>/card/', views.HealthCardView.as_view(), name='health_card'),
]

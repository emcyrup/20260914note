from django.urls import path

from . import views

app_name = 'minutes'

urlpatterns = [
    path('', views.MinutesView.as_view(), name='index'),
    path('organize/', views.OrganizeView.as_view(), name='organize'),
    path('<int:pk>/print/', views.PrintView.as_view(), name='print'),
]

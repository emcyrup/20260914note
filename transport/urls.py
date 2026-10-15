from django.urls import path

from . import views

app_name = 'transport'

urlpatterns = [
    path('', views.DayView.as_view(), name='day'),
    path('pdf/', views.DayPdfView.as_view(), name='day_pdf'),
    path('vehicles/', views.VehiclesView.as_view(), name='vehicles'),
    path('users/<int:pk>/', views.ProfileView.as_view(), name='profile'),
]

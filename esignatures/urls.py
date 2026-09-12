from django.urls import path
from . import views

app_name = 'esignatures'

urlpatterns = [
    path('save/', views.SaveSignatureView.as_view(), name='save'),
]

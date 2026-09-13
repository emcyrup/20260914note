from django.urls import path
from . import views

app_name = 'accounts'

urlpatterns = [
    path('login/', views.StaffLoginView.as_view(), name='login'),
    path('logout/', views.StaffLogoutView.as_view(), name='logout'),
    path('theme/', views.ThemeView.as_view(), name='theme'),
    path('staff/', views.StaffListView.as_view(), name='staff'),
    path('staff/<int:pk>/update/', views.StaffUpdateView.as_view(), name='staff_update'),
]

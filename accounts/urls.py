from django.urls import path
from . import views

app_name = 'accounts'

urlpatterns = [
    path('login/', views.StaffLoginView.as_view(), name='login'),
    path('logout/', views.StaffLogoutView.as_view(), name='logout'),
    path('theme/', views.ThemeView.as_view(), name='theme'),
    path('switch-facility/', views.SwitchFacilityView.as_view(), name='switch_facility'),
    path('staff/', views.StaffListView.as_view(), name='staff'),
    path('staff/<int:pk>/update/', views.StaffUpdateView.as_view(), name='staff_update'),
    path('staff/invitations/add/', views.InvitationCreateView.as_view(), name='invitation_add'),
    path('staff/invitations/<int:pk>/revoke/', views.InvitationRevokeView.as_view(), name='invitation_revoke'),
    path('join/<str:token>/', views.JoinView.as_view(), name='join'),
    path('signup/', views.SignupView.as_view(), name='signup'),
    path('register/', views.StaffRegisterView.as_view(), name='register'),
    path('staff/signup-code/', views.SignupCodeView.as_view(), name='signup_code'),
    path('staff/<int:pk>/approve/', views.StaffApproveView.as_view(), name='staff_approve'),
]

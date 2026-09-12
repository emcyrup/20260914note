from django.urls import path
from . import views

app_name = 'facilities'

urlpatterns = [
    path('', views.DashboardView.as_view(), name='dashboard'),

    # 施設設定
    path('settings/', views.SettingsView.as_view(), name='settings'),
    path('settings/facility/update/', views.FacilityUpdateView.as_view(), name='facility_update'),

    # 活動タグ管理
    path('settings/activity-tags/add/', views.ActivityTagCreateView.as_view(), name='activity_tag_add'),
    path('settings/activity-tags/<int:pk>/edit/', views.ActivityTagUpdateView.as_view(), name='activity_tag_edit'),
    path('settings/activity-tags/<int:pk>/delete/', views.ActivityTagDeleteView.as_view(), name='activity_tag_delete'),
    path('settings/activity-tags/load-defaults/', views.ActivityTagLoadDefaultsView.as_view(), name='activity_tag_defaults'),

    # 支援内容タグ管理
    path('settings/support-tags/add/', views.SupportTagCreateView.as_view(), name='support_tag_add'),
    path('settings/support-tags/<int:pk>/edit/', views.SupportTagUpdateView.as_view(), name='support_tag_edit'),
    path('settings/support-tags/<int:pk>/delete/', views.SupportTagDeleteView.as_view(), name='support_tag_delete'),
    path('settings/support-tags/load-defaults/', views.SupportTagLoadDefaultsView.as_view(), name='support_tag_defaults'),

    # 加算マスタ・施設加算設定
    path('settings/addons/save/', views.AddonSettingView.as_view(), name='addon_setting'),
    path('settings/addons/load-defaults/', views.AddonLoadDefaultsView.as_view(), name='addon_defaults'),
]

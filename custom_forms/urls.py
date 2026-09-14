from django.urls import path

from . import views

app_name = 'custom_forms'

urlpatterns = [
    path('', views.IndexView.as_view(), name='index'),
    # 関係機関連携加算Ⅱ 報告書
    path('meetings/new/', views.MeetingEditView.as_view(), name='meeting_add'),
    path('meetings/<int:pk>/edit/', views.MeetingEditView.as_view(), name='meeting_edit'),
    path('meetings/<int:pk>/delete/', views.MeetingDeleteView.as_view(), name='meeting_delete'),
    path('meetings/<int:pk>/pdf/', views.MeetingPdfView.as_view(), name='meeting_pdf'),
    # 専門的支援実施計画書
    path('specialized/new/', views.SpecializedEditView.as_view(), name='specialized_add'),
    path('specialized/<int:pk>/edit/', views.SpecializedEditView.as_view(), name='specialized_edit'),
    path('specialized/<int:pk>/delete/', views.SpecializedDeleteView.as_view(), name='specialized_delete'),
    path('specialized/<int:pk>/pdf/', views.SpecializedPdfView.as_view(), name='specialized_pdf'),
    # 個別支援計画書（別紙1／詳細版）
    path('plans/<int:pk>/extra/', views.PlanExtraView.as_view(), name='plan_extra'),
    path('plans/<int:pk>/sheet1/', views.PlanSheet1View.as_view(), name='plan_sheet1'),
    path('plans/<int:pk>/detail/', views.PlanDetailFormView.as_view(), name='plan_detail'),
]

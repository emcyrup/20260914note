from django.urls import path

from . import views

app_name = 'nursing'

urlpatterns = [
    path('', views.HomeView.as_view(), name='home'),
    path('settings/', views.SettingView.as_view(), name='settings'),
    path('docs/', views.DocsView.as_view(), name='docs'),
    # 検温板
    path('vitals/', views.VitalsDayView.as_view(), name='vitals_day'),
    path('vitals/week/print/', views.VitalsWeekAllPdfView.as_view(), name='vitals_week_all_print'),
    path('users/<int:pk>/vitals/', views.VitalsWeekView.as_view(), name='vitals_week'),
    path('users/<int:pk>/vitals/print/', views.VitalsWeekPdfView.as_view(), name='vitals_week_print'),
    # 介護記録
    path('records/', views.RecordsView.as_view(), name='records'),
    path('records/review/', views.ReviewListView.as_view(), name='review'),
    path('records/<int:record_pk>/approve/', views.RecordApproveView.as_view(), name='record_approve'),
    path('users/<int:pk>/records/', views.PersonRecordsView.as_view(), name='person_records'),
    path('users/<int:pk>/records/print/', views.PersonRecordsPdfView.as_view(), name='person_records_print'),
    path('users/<int:pk>/records/edit/', views.RecordEditView.as_view(), name='record_edit'),
    path('users/<int:pk>/records/ai/', views.AiRecordDraftView.as_view(), name='ai_record'),
    path('ai/rewrite/', views.AiRewriteView.as_view(), name='ai_rewrite'),
    path('ai/incident/', views.AiIncidentView.as_view(), name='ai_incident'),
    # 連絡帳
    path('renraku/', views.RenrakuView.as_view(), name='renraku'),
    path('renraku/print/', views.RenrakuPdfView.as_view(), name='renraku_print'),
    # ヒヤリハット・事故報告
    path('incidents/', views.IncidentsView.as_view(), name='incidents'),
    path('incidents/new/', views.IncidentEditView.as_view(), name='incident_new'),
    path('incidents/<int:pk>/', views.IncidentEditView.as_view(), name='incident_edit'),
    path('incidents/<int:pk>/print/', views.IncidentPdfView.as_view(), name='incident_print'),
    # 薬
    path('medications/', views.MedicationsView.as_view(), name='medications'),
    path('users/<int:pk>/medications/', views.PersonMedicationsView.as_view(), name='person_meds'),
    path('users/<int:pk>/medications/print/', views.PersonMedicationsPdfView.as_view(), name='person_meds_print'),
    # 通所介護計画書
    path('users/<int:pk>/plans/', views.PersonPlansView.as_view(), name='person_plans'),
    path('users/<int:pk>/plans/new/', views.PlanEditView.as_view(), name='plan_new'),
    path('users/<int:pk>/plans/<int:plan_pk>/', views.PlanEditView.as_view(), name='plan_edit'),
    path('users/<int:pk>/plans/<int:plan_pk>/print/', views.PlanPdfView.as_view(), name='plan_print'),
    # 人員配置・送迎表
    path('staffing/', views.StaffingView.as_view(), name='staffing'),
    path('transport/', views.TransportDayView.as_view(), name='transport_day'),
    path('transport/print/', views.TransportDayPdfView.as_view(), name='transport_day_print'),
]

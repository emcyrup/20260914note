from django.urls import path

from . import views, views_days, views_forms

app_name = 'nursing'

urlpatterns = [
    path('', views.HomeView.as_view(), name='home'),
    path('settings/', views.SettingView.as_view(), name='settings'),
    path('docs/', views.DocsView.as_view(), name='docs'),
    # 検温板
    path('vitals/', views.VitalsDayView.as_view(), name='vitals_day'),
    path('vitals/week/print/', views.VitalsWeekAllPdfView.as_view(), name='vitals_week_all_print'),
    path('vitals/extra/', views.VitalsExtraView.as_view(), name='vitals_extra'),
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
    # やすらぎの森の様式（2026-11-11）：申し送り・所見／処方処置・上申書・温度板・処方箋
    path('handover/', views_forms.HandoverView.as_view(), name='handover'),
    path('timeline/', views_forms.TimelineDayView.as_view(), name='timeline_day'),
    path('users/<int:pk>/timeline/', views_forms.PersonTimelineView.as_view(), name='person_timeline'),
    path('users/<int:pk>/timeline/print/', views_forms.PersonTimelinePdfView.as_view(), name='person_timeline_print'),
    path('handover/print/', views_forms.HandoverPdfView.as_view(), name='handover_print'),
    path('joushin/', views_forms.JoushinView.as_view(), name='joushin'),
    path('joushin/print/', views_forms.JoushinPdfView.as_view(), name='joushin_print'),
    path('users/<int:pk>/notes/', views_forms.PersonNotesView.as_view(), name='person_notes'),
    path('users/<int:pk>/joushin/', views_forms.PersonJoushinView.as_view(), name='person_joushin'),
    path('users/<int:pk>/joushin/print/', views_forms.PersonJoushinPdfView.as_view(), name='person_joushin_print'),
    path('users/<int:pk>/notes/print/', views_forms.PersonNotesPdfView.as_view(), name='person_notes_print'),
    path('users/<int:pk>/vitals/board/', views_forms.TempBoardPdfView.as_view(), name='temp_board_print'),
    path('users/<int:pk>/room/', views_forms.PersonRoomView.as_view(), name='person_room'),
    path('users/<int:pk>/medications/prescription/', views_forms.PrescriptionPdfView.as_view(), name='prescription_print'),
    # 通所の利用日：提供票の読み取り・利用予定実績表（A）・送迎表（B）・週間通所予定表（C）
    path('days/', views_days.DayMonthView.as_view(), name='day_month'),
    path('days/print/', views_days.DayMonthPdfView.as_view(), name='day_month_print'),
    path('days/toggle/', views_days.DayToggleView.as_view(), name='day_toggle'),
    path('days/teikyo/', views_days.TeikyoView.as_view(), name='teikyo'),
    path('days/teikyo/read/', views_days.TeikyoReadView.as_view(), name='teikyo_read'),
    path('days/teikyo/apply/', views_days.TeikyoApplyView.as_view(), name='teikyo_apply'),
    path('days/week/', views_days.WeekPlanView.as_view(), name='week_plan'),
    path('days/week/print/', views_days.WeekPlanPdfView.as_view(), name='week_plan_print'),
    path('transport/board/', views_days.TransportBoardView.as_view(), name='transport_board'),
    path('transport/board/print/', views_days.TransportBoardPdfView.as_view(), name='transport_board_print'),
    # 人員配置・送迎表
    path('staffing/', views.StaffingView.as_view(), name='staffing'),
    path('transport/', views.TransportDayView.as_view(), name='transport_day'),
    path('transport/print/', views.TransportDayPdfView.as_view(), name='transport_day_print'),
]

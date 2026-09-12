from django.urls import path
from . import views

app_name = 'schedules'

urlpatterns = [
    # 月表示カレンダー（当月 / 指定月）
    path('',
         views.CalendarView.as_view(), name='calendar'),
    path('<int:year>/<int:month>/',
         views.CalendarView.as_view(), name='calendar_month'),

    # 日別出欠入力
    path('<int:year>/<int:month>/<int:day>/',
         views.DailyView.as_view(), name='daily'),
    path('<int:year>/<int:month>/<int:day>/save/',
         views.DailySaveView.as_view(), name='daily_save'),

    # 予定を一括作成
    path('<int:year>/<int:month>/bulk-create/',
         views.BulkCreateView.as_view(), name='bulk_create'),
]

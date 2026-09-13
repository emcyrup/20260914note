"""
予定管理のビュー
- calendar_view : 月表示カレンダー
- daily_view    : 日付別の出欠入力一覧
- daily_save    : 出欠状態の一括保存
- bulk_create   : 指定月の予定をまとめて作成
"""

import calendar
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from beneficiaries.models import Beneficiary
from .models import ScheduledVisit
from facilities.context_processors import get_terms


class CalendarView(LoginRequiredMixin, View):
    """
    月表示カレンダー画面。
    各日付に当日の利用予定人数を表示し、クリックで日別入力画面へ遷移する。
    """

    def get(self, request, year=None, month=None):
        today = date.today()
        if year is None:
            year = today.year
        if month is None:
            month = today.month

        if month == 1:
            prev_year, prev_month = year - 1, 12
        else:
            prev_year, prev_month = year, month - 1
        if month == 12:
            next_year, next_month = year + 1, 1
        else:
            next_year, next_month = year, month + 1

        facility = request.user.facility
        first_day = date(year, month, 1)
        last_day  = calendar.monthrange(year, month)[1]
        month_end = date(year, month, last_day)

        visits = ScheduledVisit.objects.filter(
            facility=facility,
            date__gte=first_day,
            date__lte=month_end,
        ).values('date', 'status')

        # 日付ごとにステータス別の件数を集計
        day_stats = {}
        for v in visits:
            d = v['date']
            if d not in day_stats:
                day_stats[d] = {'scheduled': 0, 'attended': 0, 'absent': 0, 'transferred': 0}
            status = v['status']
            if status in day_stats[d]:
                day_stats[d][status] += 1

        # カレンダーグリッドを生成（月曜始まり）
        cal   = calendar.monthcalendar(year, month)
        weeks = []
        for week in cal:
            week_data = []
            for day_num in week:
                if day_num == 0:
                    week_data.append(None)
                else:
                    d     = date(year, month, day_num)
                    stats = day_stats.get(d, {})
                    week_data.append({
                        'date':        d,
                        'day':         day_num,
                        'is_today':    d == today,
                        'is_sunday':   d.weekday() == 6,
                        'is_saturday': d.weekday() == 5,
                        'scheduled':   stats.get('scheduled', 0),
                        'attended':    stats.get('attended', 0),
                        'absent':      stats.get('absent', 0),
                        'transferred': stats.get('transferred', 0),
                    })
            weeks.append(week_data)

        # 当月の予定がすでに作成済みかチェック（一括作成ボタンの表示制御）
        current_month_has_visits = ScheduledVisit.objects.filter(
            facility=facility,
            date__year=year,
            date__month=month,
        ).exists()

        return render(request, 'schedules/calendar.html', {
            'year':                 year,
            'month':                month,
            'first_day':            first_day,
            'weeks':                weeks,
            'prev_year':            prev_year,
            'prev_month':           prev_month,
            'next_year':            next_year,
            'next_month':           next_month,
            'today':                today,
            'current_month_has_visits': current_month_has_visits,
        })


class DailyView(LoginRequiredMixin, View):
    """
    日別出欠入力画面。
    その日の在籍中の利用者全員の出欠状態・送迎を一覧表示・編集できる。
    """

    def get(self, request, year, month, day):
        try:
            target_date = date(year, month, day)
        except ValueError:
            return redirect('schedules:calendar')

        facility = request.user.facility
        beneficiaries = Beneficiary.objects.filter(
            facility=facility, status='active',
        ).order_by('last_name_kana', 'first_name_kana')

        visits_qs = ScheduledVisit.objects.filter(
            facility=facility,
            date=target_date,
        ).select_related('beneficiary')
        visit_map = {v.beneficiary_id: v for v in visits_qs}

        rows = []
        for b in beneficiaries:
            visit = visit_map.get(b.pk)
            rows.append({
                'beneficiary': b,
                'visit':       visit,
                'status':      visit.status      if visit else '',
                'has_pickup':  visit.has_pickup  if visit else False,
                'has_dropoff': visit.has_dropoff if visit else False,
                'notes':       visit.notes       if visit else '',
            })

        prev_date = target_date - timedelta(days=1)
        next_date = target_date + timedelta(days=1)

        return render(request, 'schedules/daily.html', {
            'target_date':    target_date,
            'rows':           rows,
            'status_choices': ScheduledVisit.STATUS_CHOICES,
            'prev_date':      prev_date,
            'next_date':      next_date,
            'year':           year,
            'month':          month,
        })


class DailySaveView(LoginRequiredMixin, View):
    """
    日別出欠状態の一括保存。
    フォームから送信された全利用者の状態・送迎情報を保存する。
    """

    def post(self, request, year, month, day):
        try:
            target_date = date(year, month, day)
        except ValueError:
            return redirect('schedules:calendar')

        facility      = request.user.facility
        beneficiaries = Beneficiary.objects.filter(
            facility=facility, status='active',
        )

        for b in beneficiaries:
            prefix = f'b_{b.pk}_'
            status = request.POST.get(f'{prefix}status', '').strip()

            # 状態が選択されていない場合は既存レコードを削除
            if not status:
                ScheduledVisit.objects.filter(
                    facility=facility,
                    beneficiary=b,
                    date=target_date,
                ).delete()
                continue

            valid_statuses = [s[0] for s in ScheduledVisit.STATUS_CHOICES]
            if status not in valid_statuses:
                continue

            has_pickup  = f'{prefix}has_pickup'  in request.POST
            has_dropoff = f'{prefix}has_dropoff' in request.POST
            notes       = request.POST.get(f'{prefix}notes', '').strip()

            ScheduledVisit.objects.update_or_create(
                facility=facility,
                beneficiary=b,
                date=target_date,
                defaults={
                    'status':      status,
                    'has_pickup':  has_pickup,
                    'has_dropoff': has_dropoff,
                    'notes':       notes,
                },
            )

        messages.success(
            request,
            f'{target_date.strftime("%Y年%m月%d日")} の出欠情報を保存しました。',
        )
        return redirect('schedules:daily', year=year, month=month, day=day)


class BulkCreateView(LoginRequiredMixin, View):
    """
    指定した月の予定をまとめて作成する。
    在籍中の利用者の登録曜日をもとに1ヶ月分の予定を「予定」状態で作成する。
    すでにレコードが存在する日はスキップする（上書きしない）。
    """

    def post(self, request, year, month):
        facility = request.user.facility
        beneficiaries = Beneficiary.objects.filter(
            facility=facility, status='active',
        )

        if not beneficiaries.exists():
            messages.warning(request, f'在籍中の{get_terms(request.user)["beneficiary"]}がいないため、予定を作成できませんでした。')
            return redirect('schedules:calendar_month', year=year, month=month)

        last_day  = calendar.monthrange(year, month)[1]
        all_dates = [date(year, month, d) for d in range(1, last_day + 1)]

        created_count = 0
        for b in beneficiaries:
            weekday_nums = b.scheduled_weekday_numbers  # [0, 2, 4] = 月水金

            # 通所曜日が1つも設定されていない場合は全日（日曜除く）作成
            has_any_day = bool(weekday_nums)

            for d in all_dates:
                if d.weekday() == 6:  # 日曜は常にスキップ
                    continue
                if has_any_day and d.weekday() not in weekday_nums:
                    continue

                _, created = ScheduledVisit.objects.get_or_create(
                    facility=facility,
                    beneficiary=b,
                    date=d,
                    defaults={'status': ScheduledVisit.STATUS_SCHEDULED},
                )
                if created:
                    created_count += 1

        if created_count > 0:
            messages.success(
                request,
                f'{year}年{month}月の予定を {created_count} 件作成しました（既存分はそのままです）。',
            )
        else:
            messages.info(request, f'{year}年{month}月の予定はすでに全て作成済みです。')

        return redirect('schedules:calendar_month', year=year, month=month)

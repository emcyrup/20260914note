"""予約管理の画面（なゆた由来）"""
import calendar
import csv
import datetime
from io import StringIO

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View

from beneficiaries.models import Beneficiary
from config.concurrency import check_conflict
from config.utils import date_or_404, month_or_404, to_int

from . import services
from .models import ClosedDate, Customer, LineInbox, Reservation, ReservationNotice


class ReservationEnabledMixin(LoginRequiredMixin):
    """施設設定で予約管理を使わない場合はホームへ戻す"""

    def dispatch(self, request, *args, **kwargs):
        facility = getattr(request.user, 'facility', None)
        if request.user.is_authenticated and (facility is None or not facility.use_reservation):
            messages.info(request, 'この事業所では予約管理を使わない設定になっています。')
            return redirect('facilities:dashboard')
        return super().dispatch(request, *args, **kwargs)


def _month_links(year, month):
    prev_y, prev_m = (year - 1, 12) if month == 1 else (year, month - 1)
    next_y, next_m = (year + 1, 1) if month == 12 else (year, month + 1)
    return {'prev_year': prev_y, 'prev_month': prev_m, 'next_year': next_y, 'next_month': next_m}


class CalendarView(ReservationEnabledMixin, View):
    """予約カレンダー：月の残枠一覧"""
    template_name = 'reservations/calendar.html'

    def get(self, request, year=None, month=None):
        today = datetime.date.today()
        year, month = month_or_404(year or today.year, month or today.month)
        facility = request.user.facility
        setting = services.get_setting(facility)
        first = datetime.date(year, month, 1)
        last = datetime.date(year, month, calendar.monthrange(year, month)[1])
        states = services.month_states(facility, first, last)

        weeks = []
        for week in calendar.monthcalendar(year, month):
            row = []
            for day_num in week:
                if day_num == 0:
                    row.append(None)
                    continue
                d = datetime.date(year, month, day_num)
                st = dict(states[d])
                st.update({'day': day_num, 'is_today': d == today,
                           'is_sunday': d.weekday() == 6, 'is_saturday': d.weekday() == 5})
                row.append(st)
            weeks.append(row)

        pending = ReservationNotice.objects.filter(facility=facility, status=ReservationNotice.STATUS_PENDING).count()
        inbox = LineInbox.objects.filter(facility=facility, status=LineInbox.STATUS_PENDING).count()
        return render(request, self.template_name, {
            'year': year, 'month': month, 'weeks': weeks, 'today': today, 'setting': setting,
            'pending_notices': pending, 'pending_inbox': inbox,
            'weekday_rows': setting.weekday_rows(),
            **_month_links(year, month),
        })


class SettingView(ReservationEnabledMixin, View):
    """枠・キャンセル待ち・休業曜日・署名の保存（管理者のみ）"""

    def post(self, request):
        if not (request.user.is_admin or request.user.is_superuser):
            messages.error(request, 'この設定を変えられるのは管理者だけです。')
            return redirect('reservations:calendar')
        facility = request.user.facility
        setting = services.get_setting(facility)
        conflict = check_conflict(request, setting)
        if conflict:
            messages.error(request, conflict)
            return redirect('reservations:calendar')
        capacity = to_int(request.POST.get('capacity'), setting.capacity)
        if not 1 <= capacity <= 99:
            messages.error(request, '1日の枠は1〜99人で指定してください。')
            return redirect('reservations:calendar')
        setting.capacity = capacity
        setting.allow_waitlist = 'allow_waitlist' in request.POST
        setting.auto_send = 'auto_send' in request.POST
        setting.closed_weekdays = sorted({n for n in (to_int(v) for v in request.POST.getlist('closed_weekdays'))
                                          if n is not None and 0 <= n <= 6})
        setting.signature = request.POST.get('signature', '').strip()[:100]
        if 'clear_group' in request.POST:
            setting.notify_group_id = setting.notify_group_label = ''
        setting.save()
        messages.success(request, '予約の設定を保存しました。')
        return redirect('reservations:calendar')


class DayView(ReservationEnabledMixin, View):
    """その日の予約：一覧・追加・取消・臨時休業・前日のお知らせ"""
    template_name = 'reservations/day.html'

    def get(self, request, year, month, day):
        d = date_or_404(year, month, day)
        facility = request.user.facility
        setting = services.get_setting(facility)
        state = services.day_state(facility, d, setting)
        rows = (Reservation.objects.filter(facility=facility, date=d)
                .select_related('beneficiary', 'customer').order_by('status', 'created_at'))
        taken = {r.beneficiary_id for r in rows if r.is_active}
        candidates = [b for b in Beneficiary.objects.filter(facility=facility, status=Beneficiary.STATUS_ACTIVE)
                      if b.pk not in taken]
        return render(request, self.template_name, {
            'date': d, 'state': state, 'setting': setting,
            'reservations': [r for r in rows if r.is_active],
            'history': [r for r in rows if not r.is_active],
            'candidates': candidates,
            'closed_date': ClosedDate.objects.filter(facility=facility, date=d).first(),
            'vacancy_text': services.vacancy_text(facility, d),
            'prev_day': d - datetime.timedelta(days=1), 'next_day': d + datetime.timedelta(days=1),
        })

    def post(self, request, year, month, day):
        d = date_or_404(year, month, day)
        facility = request.user.facility
        action = request.POST.get('action', 'add')
        back = redirect('reservations:day', year=d.year, month=d.month, day=d.day)

        if action == 'add':
            beneficiary = Beneficiary.objects.filter(facility=facility,
                                                     pk=to_int(request.POST.get('beneficiary'), -1)).first()
            if beneficiary is None:
                messages.error(request, '利用者を選んでください。')
                return back
            try:
                res, notice = services.create_reservation(facility, beneficiary, d,
                                                          note=request.POST.get('note', ''))
            except services.ReservationError as e:
                messages.error(request, str(e))
                return back
            label = res.get_status_display()
            messages.success(request, f'{beneficiary.full_name} さんを「{label}」で登録しました。'
                                      f'通知は送信待ちに入れています。')
            return back

        if action == 'cancel':
            res = get_object_or_404(Reservation, pk=to_int(request.POST.get('reservation'), -1), facility=facility)
            promoted = services.cancel_reservation(res)
            msg = f'{res.beneficiary.full_name} さんの予約を取り消しました。'
            if promoted:
                msg += 'キャンセル待ちから ' + '、'.join(p.beneficiary.full_name for p in promoted) + ' さんを繰り上げました。'
            messages.success(request, msg)
            return back

        if action == 'close':
            ClosedDate.objects.get_or_create(facility=facility, date=d,
                                             defaults={'reason': request.POST.get('reason', '').strip()[:100]})
            messages.success(request, f'{services.jp_date(d)} を臨時休業にしました。予約は受け付けません。')
            return back

        if action == 'open':
            ClosedDate.objects.filter(facility=facility, date=d).delete()
            messages.success(request, f'{services.jp_date(d)} の臨時休業を取り消しました。')
            return back

        if action == 'reminder':
            made = services.queue_reminders(facility, d)
            messages.success(request, f'前日のお知らせを {len(made)} 件、送信待ちに入れました。'
                             if made else '送信待ちに入れる相手がいませんでした（すでに作成ずみか、予約がありません）。')
            return back

        messages.error(request, '操作が正しくありません。')
        return back


class CustomerListView(ReservationEnabledMixin, View):
    """顧客台帳（予約の連絡先）"""
    template_name = 'reservations/customers.html'

    def get(self, request):
        facility = request.user.facility
        return render(request, self.template_name, {
            'customers': Customer.objects.filter(facility=facility).prefetch_related('children'),
            'beneficiaries': Beneficiary.objects.filter(facility=facility, status=Beneficiary.STATUS_ACTIVE),
        })

    def post(self, request, pk=None):
        facility = request.user.facility
        obj = get_object_or_404(Customer, pk=pk, facility=facility) if pk else Customer(facility=facility)
        conflict = check_conflict(request, obj) if pk else None
        if conflict:
            messages.error(request, conflict)
            return redirect('reservations:customers')
        p = request.POST
        name = p.get('name', '').strip()[:100]
        if not name:
            messages.error(request, 'お名前を入れてください。')
            return redirect('reservations:customers')
        obj.name = name
        obj.kana = p.get('kana', '').strip()[:100]
        obj.phone = p.get('phone', '').strip()[:20]
        obj.line_user_id = p.get('line_user_id', '').strip()[:100]
        obj.notify_enabled = 'notify_enabled' in p
        obj.note = p.get('note', '').strip()[:200]
        if obj.line_user_id and Customer.objects.filter(facility=facility, line_user_id=obj.line_user_id).exclude(pk=obj.pk).exists():
            messages.error(request, 'そのLINEのユーザーIDは、ほかの顧客に登録されています。')
            return redirect('reservations:customers')
        obj.save()
        obj.children.set(Beneficiary.objects.filter(facility=facility, pk__in=p.getlist('children')))
        messages.success(request, f'顧客「{obj.name}」を保存しました。')
        return redirect('reservations:customers')


class CustomerDeleteView(ReservationEnabledMixin, View):
    def post(self, request, pk):
        obj = get_object_or_404(Customer, pk=pk, facility=request.user.facility)
        name = obj.name
        obj.delete()
        messages.success(request, f'顧客「{name}」を削除しました。')
        return redirect('reservations:customers')


class LineView(ReservationEnabledMixin, View):
    """公式LINE：受信の取り込みと反映、送信待ちの通知"""
    template_name = 'reservations/line.html'

    def get(self, request):
        facility = request.user.facility
        inbox = (LineInbox.objects.filter(facility=facility, source_type=LineInbox.SOURCE_USER)
                 .exclude(status=LineInbox.STATUS_DONE)[:30])
        groups = (LineInbox.objects.filter(facility=facility)
                  .exclude(source_type=LineInbox.SOURCE_USER).exclude(group_id='')[:10])
        return render(request, self.template_name, {
            'setting': services.get_setting(facility),
            'rows': [services.inbox_row(e, facility) for e in inbox],
            'group_rows': groups,
            'pending': ReservationNotice.objects.filter(
                facility=facility, status__in=(ReservationNotice.STATUS_PENDING, ReservationNotice.STATUS_MANUAL)
            ).select_related('customer')[:50],
            'sent': ReservationNotice.objects.filter(
                facility=facility, status__in=(ReservationNotice.STATUS_SENT, ReservationNotice.STATUS_FAILED)
            ).select_related('customer')[:20],
            'beneficiaries': Beneficiary.objects.filter(facility=facility, status=Beneficiary.STATUS_ACTIVE),
        })

    def post(self, request):
        facility = request.user.facility
        action = request.POST.get('action', '')
        back = redirect('reservations:line')

        if action in ('apply', 'ignore', 'register'):
            entry = get_object_or_404(LineInbox, pk=to_int(request.POST.get('entry'), -1), facility=facility)
            if action == 'ignore':
                entry.status = LineInbox.STATUS_IGNORED
                entry.handled_at, entry.handled_by = timezone.now(), request.user
                entry.result_note = '見送り'
                entry.redact()
                entry.save()
                messages.success(request, 'この受信を見送りにしました。')
                return back
            if action == 'register':
                return self._register_customer(request, entry, back)
            return self._apply(request, entry, back)

        if action == 'send':
            return self._send(request, back)

        if action == 'set_group':
            entry = get_object_or_404(LineInbox, pk=to_int(request.POST.get('entry'), -1), facility=facility)
            if not entry.group_id:
                messages.error(request, 'この受信にはグループの情報がありません。')
                return back
            setting = services.get_setting(facility)
            setting.notify_group_id = entry.group_id
            setting.notify_group_label = entry.display_name or 'スタッフのグループ'
            setting.save(update_fields=['notify_group_id', 'notify_group_label', 'updated_at'])
            messages.success(request, 'この投稿のグループを、予約の増減のお知らせ先にしました。')
            return back

        messages.error(request, '操作が正しくありません。')
        return back

    # -- 受信の反映 -------------------------------------------------
    def _apply(self, request, entry, back):
        facility = request.user.facility
        parsed = services.parse_message(entry.text)
        d = parsed['date']
        if request.POST.get('date'):
            try:
                d = datetime.date.fromisoformat(request.POST['date'])
            except ValueError:
                d = None
        beneficiary = Beneficiary.objects.filter(facility=facility,
                                                 pk=to_int(request.POST.get('beneficiary'), -1)).first()
        if d is None or beneficiary is None:
            messages.error(request, '日付と利用者が決まらないため反映しませんでした。画面で選んでからもう一度押してください。')
            return back
        intent = request.POST.get('intent') or parsed['intent']
        customer = (Customer.objects.filter(facility=facility, line_user_id=entry.line_user_id).first()
                    if entry.line_user_id else None)
        if intent == 'cancel':
            res = Reservation.objects.filter(facility=facility, beneficiary=beneficiary, date=d,
                                             status__in=Reservation.ACTIVE_STATUSES).first()
            if res is None:
                messages.error(request, 'その日のその利用者の予約が見つかりませんでした。')
                return back
            services.cancel_reservation(res)
            note = f'{services.jp_date(d)} {beneficiary.full_name} 取消'
        else:
            try:
                res, _ = services.create_reservation(facility, beneficiary, d,
                                                     source=Reservation.SOURCE_LINE, customer=customer)
            except services.ReservationError as e:
                messages.error(request, str(e))
                return back
            note = f'{services.jp_date(d)} {beneficiary.full_name} {res.get_status_display()}'
        entry.status = LineInbox.STATUS_DONE
        entry.handled_at, entry.handled_by, entry.result_note = timezone.now(), request.user, note[:200]
        entry.redact()
        entry.save()
        messages.success(request, f'{note} として反映しました。通知は送信待ちに入れています。')
        if services.get_setting(facility).auto_send:
            self._send(request, back, quiet=True)
        return back

    def _register_customer(self, request, entry, back):
        facility = request.user.facility
        parsed = services.parse_message(entry.text)
        fields = parsed['fields']
        name = (request.POST.get('name') or parsed['name'] or entry.display_name or '').strip()
        if not name:
            messages.error(request, 'お名前が読み取れませんでした。顧客台帳から手で登録してください。')
            return back
        if entry.line_user_id and Customer.objects.filter(facility=facility, line_user_id=entry.line_user_id).exists():
            messages.info(request, 'この方はすでに顧客台帳にいます。')
        else:
            Customer.objects.create(
                facility=facility, name=name[:100],
                kana=(fields.get('ふりがな', '') or '')[:100], phone=(fields.get('電話番号', '') or '')[:20],
                line_user_id=entry.line_user_id, note='公式LINEの登録フォームから',
            )
            messages.success(request, f'{name} さんを顧客台帳に追加しました。担当する利用者は台帳で選んでください。')
        entry.status = LineInbox.STATUS_DONE
        entry.handled_at, entry.handled_by, entry.result_note = timezone.now(), request.user, f'顧客登録 {name}'[:200]
        entry.redact()
        entry.save()
        return back

    # -- 通知の送信 -------------------------------------------------
    def _send(self, request, back, quiet=False):
        from line_integration.sending import send_reservation_notices
        facility = request.user.facility
        sent, failed = send_reservation_notices(facility)
        if not quiet:
            if sent or failed:
                messages.success(request, f'送信待ちの通知を {sent} 件送りました。'
                                          + (f'{failed} 件は送れませんでした。' if failed else ''))
            else:
                messages.info(request, '送る通知がありませんでした（LINE未連携ぶんは「コピーして送る」に残ります）。')
        return back


class CsvView(ReservationEnabledMixin, View):
    """その月の予約を CSV で出す"""

    def get(self, request, year, month):
        year, month = month_or_404(year, month)
        facility = request.user.facility
        first = datetime.date(year, month, 1)
        last = datetime.date(year, month, calendar.monthrange(year, month)[1])
        buf = StringIO()
        w = csv.writer(buf)
        w.writerow(['日付', '利用者', '状態', '入口', '連絡先', '備考', '登録日時'])
        for r in (Reservation.objects.filter(facility=facility, date__gte=first, date__lte=last)
                  .select_related('beneficiary', 'customer').order_by('date', 'created_at')):
            w.writerow([r.date, r.beneficiary.full_name, r.get_status_display(), r.get_source_display(),
                        r.customer.name if r.customer else '', r.note,
                        timezone.localtime(r.created_at).strftime('%Y-%m-%d %H:%M')])
        resp = HttpResponse(buf.getvalue().encode('utf-8-sig'), content_type='text/csv; charset=utf-8-sig')
        resp['Content-Disposition'] = f'attachment; filename="reservations_{year}{month:02d}.csv"'
        return resp

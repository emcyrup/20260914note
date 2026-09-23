"""予約管理の画面（なゆた由来）"""
import calendar
import csv
import datetime
import logging
from io import StringIO

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from beneficiaries.models import Beneficiary
from config.concurrency import check_conflict
from facilities.context_processors import get_terms
from config.utils import date_or_404, home_url, month_or_404, reservation_enabled, to_int

from . import monthly, services
from . import scan as scan_mod
from .models import (BookingRequest, ClosedDate, Customer, LineInbox, MonthlyRequest, RequestScan, Reservation,
                     ReservationNotice, ReservationSetting)

logger = logging.getLogger(__name__)


class ReservationEnabledMixin(LoginRequiredMixin):
    """
    施設設定で予約管理を使わない場合はホームへ戻す。
    予約管理だけを動かすサーバー（RESERVATION_ONLY）では、その確認はしない。
    """

    def dispatch(self, request, *args, **kwargs):
        facility = getattr(request.user, 'facility', None)
        if request.user.is_authenticated and not reservation_enabled(facility):
            messages.info(request, 'この事業所では予約管理を使わない設定になっています。')
            return redirect(home_url())
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
        requests_count = BookingRequest.objects.filter(facility=facility,
                                                       status=BookingRequest.STATUS_PENDING).count()
        return render(request, self.template_name, {
            'year': year, 'month': month, 'weeks': weeks, 'today': today, 'setting': setting,
            'pending_notices': pending, 'pending_inbox': inbox, 'pending_requests': requests_count,
            'weekday_rows': setting.weekday_rows(), 'facility': facility,
            'is_admin': request.user.can_manage_settings,
            'public_url': request.build_absolute_uri(
                reverse('reservations_public:calendar', args=[setting.public_token])),
            **_month_links(year, month),
        })


class SettingView(ReservationEnabledMixin, View):
    """枠・キャンセル待ち・休業曜日・署名の保存（管理者のみ）"""

    def post(self, request):
        if not request.user.can_manage_settings:
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
        old_capacity = setting.capacity
        setting.capacity = capacity
        setting.allow_waitlist = 'allow_waitlist' in request.POST
        setting.auto_send = 'auto_send' in request.POST
        setting.closed_weekdays = sorted({n for n in (to_int(v) for v in request.POST.getlist('closed_weekdays'))
                                          if n is not None and 0 <= n <= 6})
        setting.signature = request.POST.get('signature', '').strip()[:100]

        # 顧客向けの予定表（ログインなしで見えるページ）
        setting.public_calendar = 'public_calendar' in request.POST
        setting.public_booking = 'public_booking' in request.POST
        setting.public_request = 'public_request' in request.POST
        from_days = to_int(request.POST.get('booking_from_days'), setting.booking_from_days)
        until_days = to_int(request.POST.get('booking_until_days'), setting.booking_until_days)
        if not (0 <= from_days <= 30 and 1 <= until_days <= 365 and from_days < until_days):
            messages.error(request, '受け付ける範囲は「0〜30日先から」「1〜365日先まで」で、'
                                    'はじめの日が終わりの日より前になるように指定してください。')
            return redirect('reservations:calendar')
        setting.booking_from_days, setting.booking_until_days = from_days, until_days

        # LINE から直接反映するか
        setting.notify_vacancy = 'notify_vacancy' in request.POST
        mode = request.POST.get('booking_mode', ReservationSetting.MODE_AUTO)
        if mode in dict(ReservationSetting.MODE_CHOICES):
            setting.booking_mode = mode
        setting.group_auto_apply = 'group_auto_apply' in request.POST

        # 時間枠で予約する（ゆあーず）
        setting.slot_mode = 'slot_mode' in request.POST
        slot_capacity = to_int(request.POST.get('slot_capacity'), setting.slot_capacity)
        slot_minutes = to_int(request.POST.get('slot_minutes'), setting.slot_minutes)
        hours = {k: to_int(request.POST.get(k), getattr(setting, k))
                 for k in ('weekday_first_hour', 'weekday_last_hour', 'holiday_first_hour', 'holiday_last_hour')}
        if setting.slot_mode:
            if not (1 <= slot_capacity <= 20 and 10 <= slot_minutes <= 180):
                messages.error(request, '1枠の人数は1〜20人、1枠の長さは10〜180分で指定してください。')
                return redirect('reservations:calendar')
            if not all(0 <= v <= 23 for v in hours.values()) or \
                    hours['weekday_first_hour'] > hours['weekday_last_hour'] or \
                    hours['holiday_first_hour'] > hours['holiday_last_hour']:
                messages.error(request, '枠の時間帯は 0〜23 時で、最初の枠が最後の枠より前になるように指定してください。')
                return redirect('reservations:calendar')
        setting.slot_capacity, setting.slot_minutes = slot_capacity, slot_minutes
        for k, v in hours.items():
            setattr(setting, k, v)
        setting.break_hours = sorted({n for n in (to_int(v) for v in request.POST.get('break_hours', '').replace('、', ',').split(','))
                                      if n is not None and 0 <= n <= 23})

        if 'clear_group' in request.POST:
            setting.notify_group_id = setting.notify_group_label = ''
        if 'reissue_public_token' in request.POST:
            setting.reissue_public_token()
            messages.info(request, '予定表の公開アドレスを作り直しました。前のアドレスは使えません。')
        setting.save()
        messages.success(request, '予約の設定を保存しました。')
        made = services.announce_after_capacity_change(facility, old_capacity, setting,
                                                       base=request.build_absolute_uri('/'))
        if made:
            messages.info(request, f'枠を増やしたので、満席だった日の空きのお知らせを {len(made)} 件、'
                                   '送信待ちに入れました。送信は公式LINEの画面から行います。')
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
            'slot_hours': setting.slot_hours(d) if setting.slot_mode else [],
            'default_hour': to_int(request.GET.get('hour')),
            'therapy': getattr(facility, 'use_therapy_record', False),
            'closed_date': ClosedDate.objects.filter(facility=facility, date=d).first(),
            'vacancy_text': services.vacancy_text(facility, d),
            'prev_day': d - datetime.timedelta(days=1), 'next_day': d + datetime.timedelta(days=1),
        })

    def post(self, request, year, month, day):
        d = date_or_404(year, month, day)
        facility = request.user.facility
        action = request.POST.get('action', 'add')
        if 'do_delete' in request.POST:
            action = 'delete'   # 変更フォームの「削除する」
        back = redirect('reservations:day', year=d.year, month=d.month, day=d.day)
        base = request.build_absolute_uri('/')   # お知らせに載せる顧客ページのアドレス

        if action == 'add':
            beneficiary = Beneficiary.objects.filter(facility=facility,
                                                     pk=to_int(request.POST.get('beneficiary'), -1)).first()
            if beneficiary is None:
                messages.error(request, '利用者を選んでください。')
                return back
            try:
                res, notice = services.create_reservation(facility, beneficiary, d,
                                                          note=request.POST.get('note', ''),
                                                          start_time=request.POST.get('start_time'))
            except services.ReservationError as e:
                messages.error(request, str(e))
                return back
            label = res.get_status_display()
            messages.success(request, f'{beneficiary.full_name} さんを「{label}」で登録しました。'
                                      f'通知は送信待ちに入れています。')
            return back

        if action == 'cancel':
            res = get_object_or_404(Reservation, pk=to_int(request.POST.get('reservation'), -1), facility=facility)
            promoted = services.cancel_reservation(res, base=base)
            msg = f'{res.display_name} さんの予約を取り消しました。'
            if promoted:
                msg += 'キャンセル待ちから ' + '、'.join(p.display_name for p in promoted) + ' さんを繰り上げました。'
            msg += self._vacancy_note(facility, d)
            messages.success(request, msg)
            self._auto_send(request, facility)
            return back

        if action == 'edit':
            res = get_object_or_404(Reservation, pk=to_int(request.POST.get('reservation'), -1), facility=facility)
            try:
                new_day = datetime.date.fromisoformat(request.POST.get('date', ''))
            except ValueError:
                messages.error(request, '日にちが正しくありません。')
                return back
            old_time = res.start_time
            try:
                services.move_reservation(res, new_day, note=request.POST.get('note', ''), base=base,
                                          start_time=request.POST.get('start_time') or None)
            except services.ReservationError as e:
                messages.error(request, str(e))
                return back
            if new_day == d:
                if res.start_time != old_time:
                    messages.success(request, f'{res.display_name} さんの予約を {res.time_label} の枠に移しました'
                                              f'（{res.get_status_display()}）。')
                else:
                    messages.success(request, f'{res.display_name} さんの備考を保存しました。')
                return back
            messages.success(request, f'{res.display_name} さんの予約を '
                                      f'{services.jp_date(new_day)} に移しました（{res.get_status_display()}）。'
                                      '変更のお知らせは送信待ちに入れています。')
            return redirect('reservations:day', year=new_day.year, month=new_day.month, day=new_day.day)

        if action == 'link':
            res = get_object_or_404(Reservation, pk=to_int(request.POST.get('reservation'), -1), facility=facility)
            beneficiary = Beneficiary.objects.filter(facility=facility,
                                                     pk=to_int(request.POST.get('beneficiary'), -1)).first()
            if beneficiary is None:
                messages.error(request, f"{get_terms(request.user)['beneficiary']}を選んでください。")
                return back
            try:
                services.link_reservation(res, beneficiary)
            except services.ReservationError as e:
                messages.error(request, str(e))
                return back
            messages.success(request, f'この予約を {beneficiary.full_name} さんに結びつけました。')
            return back

        if action == 'delete':
            res = get_object_or_404(Reservation, pk=to_int(request.POST.get('reservation'), -1), facility=facility)
            name = res.display_name
            promoted = services.delete_reservation(res, base=base)
            msg = f'{name} さんの予約を削除しました（本人へのお知らせは送りません）。'
            if promoted:
                msg += 'キャンセル待ちから ' + '、'.join(p.display_name for p in promoted) + ' さんを繰り上げました。'
            msg += self._vacancy_note(facility, d)
            messages.success(request, msg)
            self._auto_send(request, facility)
            return back

        if action == 'vacancy':
            made = services.offer_vacancy(facility, d, base=base, force=True)
            if made:
                messages.success(request, f'空きのお知らせを {len(made)} 件、送信待ちに入れました。'
                                          '送信は公式LINEの画面から行います。')
                self._auto_send(request, facility)
            else:
                messages.info(request, 'お知らせを入れる相手がいませんでした'
                                       '（空きが無いか、LINE連携ずみで その日に予約のない顧客がいません）。')
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

    @staticmethod
    def _vacancy_note(facility, day):
        """その日に積んだ「空きのお知らせ」の件数を、職員へのメッセージに足す"""
        n = ReservationNotice.objects.filter(facility=facility, date=day,
                                             kind=ReservationNotice.KIND_VACANCY,
                                             status=ReservationNotice.STATUS_PENDING).count()
        return f'空きのお知らせを {n} 件、送信待ちに入れました。' if n else ''

    @staticmethod
    def _auto_send(request, facility):
        """設定「反映したら送信待ちをその場で送る」が入っていれば、まとめて送る"""
        if not services.get_setting(facility).auto_send:
            return
        from line_integration.sending import send_reservation_notices
        sent, failed = send_reservation_notices(facility)
        if sent or failed:
            messages.info(request, f'送信待ちの通知を {sent} 件送りました。'
                                   + (f'{failed} 件は送れませんでした。' if failed else ''))


class CustomerListView(ReservationEnabledMixin, View):
    """顧客台帳（予約の連絡先）"""
    template_name = 'reservations/customers.html'

    def get(self, request):
        facility = request.user.facility
        customers = list(Customer.objects.filter(facility=facility).prefetch_related('children'))
        for c in customers:
            c.page_url = request.build_absolute_uri(
                reverse('reservations_public:customer', args=[c.token]))
        return render(request, self.template_name, {
            'customers': customers,
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


class CustomerTokenView(ReservationEnabledMixin, View):
    """顧客ページのアドレスを作り直す（前のアドレスを知っている人は開けなくなる）"""

    def post(self, request, pk):
        obj = get_object_or_404(Customer, pk=pk, facility=request.user.facility)
        obj.reissue_token()
        obj.save(update_fields=['token', 'updated_at'])
        messages.success(request, f'{obj.name} さんのページのアドレスを作り直しました。'
                                  '新しいアドレスをお知らせしてください。')
        return redirect('reservations:customers')


class CustomerDeleteView(ReservationEnabledMixin, View):
    def post(self, request, pk):
        obj = get_object_or_404(Customer, pk=pk, facility=request.user.facility)
        name = obj.name
        obj.delete()
        messages.success(request, f'顧客「{name}」を削除しました。')
        return redirect('reservations:customers')


class RequestListView(ReservationEnabledMixin, View):
    """空き状況のページから届いた申し込み（職員が確かめて予約にする）"""
    template_name = 'reservations/requests.html'

    def get(self, request):
        facility = request.user.facility
        pending = BookingRequest.objects.filter(facility=facility, status=BookingRequest.STATUS_PENDING)
        rows = []
        for req in pending:
            state = services.day_state(facility, req.date)
            rows.append({'req': req, 'state': state,
                         'suggested': services.request_matches(facility, req)})
        handled = (BookingRequest.objects.filter(facility=facility)
                   .exclude(status=BookingRequest.STATUS_PENDING)[:20])
        return render(request, self.template_name, {
            'rows': rows, 'handled': handled,
            'setting': services.get_setting(facility),
            'beneficiaries': Beneficiary.objects.filter(facility=facility, status=Beneficiary.STATUS_ACTIVE),
            'customers': Customer.objects.filter(facility=facility),
        })

    def post(self, request, pk=None):
        facility = request.user.facility
        req = get_object_or_404(BookingRequest, pk=pk or to_int(request.POST.get('request'), -1),
                                facility=facility)
        action = request.POST.get('action', '')
        back = redirect('reservations:requests')

        if action == 'apply':
            beneficiary = Beneficiary.objects.filter(
                facility=facility, pk=to_int(request.POST.get('beneficiary'), -1)).first()
            if beneficiary is None:
                messages.error(request, f"{get_terms(request.user)['beneficiary']}を選んでください。")
                return back
            customer = Customer.objects.filter(
                facility=facility, pk=to_int(request.POST.get('customer'), -1)).first()
            try:
                res = services.apply_request(req, beneficiary, customer=customer, staff=request.user)
            except services.ReservationError as e:
                messages.error(request, str(e))
                return back
            messages.success(request, f'{services.jp_date(req.date)} {beneficiary.full_name} さんを'
                                      f'「{res.get_status_display()}」で登録しました。'
                                      '通知は送信待ちに入れています。')
            return back

        if action == 'decline':
            req.status = BookingRequest.STATUS_DECLINED
            req.handled_at, req.handled_by = timezone.now(), request.user
            req.result_note = request.POST.get('reason', '').strip()[:200] or '見送り'
            req.save(update_fields=['status', 'handled_at', 'handled_by', 'result_note'])
            messages.success(request, 'この申し込みを見送りにしました。'
                                      'お断りの連絡は、電話などで直接お願いします。')
            return back

        if action == 'register':
            if not req.name:
                messages.error(request, 'お名前が空のため、顧客台帳に追加できません。')
                return back
            customer = Customer.objects.create(
                facility=facility, name=req.name, kana=req.kana, phone=req.phone,
                note='空き状況ページのお申し込みから',
            )
            req.customer = customer
            req.save(update_fields=['customer'])
            messages.success(request, f'{customer.name} さんを顧客台帳に追加しました。'
                                      '担当する利用者は顧客の画面で選んでください。')
            return back

        if action == 'delete':
            req.delete()
            messages.success(request, 'この申し込みを削除しました（連絡先も消えます）。')
            return back

        messages.error(request, '操作が正しくありません。')
        return back


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
            'webhook_url': request.build_absolute_uri(
                reverse('line_integration:webhook_facility', args=[facility.pk])),
            'is_admin': request.user.can_manage_settings,
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

        if action == 'channel':
            # 予約管理だけを動かすサーバーには施設設定の画面がないので、ここで公式LINEのつなぎ先を保存する
            if not request.user.can_manage_settings:
                messages.error(request, 'この設定を変えられるのは管理者だけです。')
                return back
            name = request.POST.get('facility_name', '').strip()[:100]
            if name:
                facility.name = name
            secret = request.POST.get('line_channel_secret', '').strip()
            token = request.POST.get('line_channel_access_token', '').strip()
            if secret:
                facility.line_channel_secret = secret
            if token:
                facility.line_channel_access_token = token
            facility.save(update_fields=['name', 'line_channel_secret', 'line_channel_access_token', 'updated_at'])
            messages.success(request, '公式LINEのつなぎ先を保存しました。')
            return back

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
        w.writerow(['日付', '時刻', '利用者', '状態', '入口', '連絡先', '備考', '登録日時'])
        for r in (Reservation.objects.filter(facility=facility, date__gte=first, date__lte=last)
                  .select_related('beneficiary', 'customer').order_by('date', 'created_at')):
            w.writerow([r.date, r.time_label, r.display_name, r.get_status_display(), r.get_source_display(),
                        r.customer.name if r.customer else '', r.note,
                        timezone.localtime(r.created_at).strftime('%Y-%m-%d %H:%M')])
        resp = HttpResponse(buf.getvalue().encode('utf-8-sig'), content_type='text/csv; charset=utf-8-sig')
        resp['Content-Disposition'] = f'attachment; filename="reservations_{year}{month:02d}.csv"'
        return resp


# =============================================
# 月予約利用希望・月間予定表（時間枠で予約する事業所）
# =============================================
class SlotModeMixin(ReservationEnabledMixin):
    """時間枠で予約する設定になっていなければ、予約カレンダーへ戻す"""

    def dispatch(self, request, *args, **kwargs):
        facility = getattr(request.user, 'facility', None)
        if request.user.is_authenticated and facility is not None and reservation_enabled(facility) \
                and not services.get_setting(facility).slot_mode:
            messages.info(request, '月予約利用希望と月間予定表は、予約の設定で「時間枠で予約する」を入れると使えます。')
            return redirect('reservations:calendar')
        return super().dispatch(request, *args, **kwargs)


def _month_ctx(year, month):
    py, pm = monthly.prev_month(year, month)
    ny, nm = monthly.next_month(year, month)
    return {'year': year, 'month': month, 'prev_year': py, 'prev_month': pm, 'next_year': ny, 'next_month': nm}


class MonthlyRequestListView(SlotModeMixin, View):
    """月予約利用希望の一覧（利用者ごとの希望回数・○の枠数・確定数）"""
    template_name = 'reservations/monthly_requests.html'

    def get(self, request, year, month):
        year, month = month_or_404(year, month)
        facility = request.user.facility
        setting = services.get_setting(facility)
        rows = monthly.request_rows(facility, year, month, setting)
        base = request.build_absolute_uri('/')
        targets, no_contact = monthly.wish_targets(facility, year, month, base)
        for t in targets:
            ask = t['ask']
            t['open_ask'] = ask if ask and ask.status in monthly.WISH_NOTICE_OPEN else None
            t['body'] = t['open_ask'].body if t['open_ask'] else monthly.wish_ask_body(
                setting, t['customer'], t['missing'] or t['kids'], year, month, t['url'])
        return render(request, self.template_name, {
            'rows': rows, 'setting': setting, 'facility': facility,
            'with_request': sum(1 for r in rows if r['request']),
            'from_web': sum(1 for r in rows if r['request'] and r['request'].source == MonthlyRequest.SOURCE_WEB),
            'short': sum(1 for r in rows if r['request'] and r['remaining']),
            'targets': targets, 'no_contact': no_contact,
            'scans': (RequestScan.objects.filter(facility=facility, year=year, month=month)
                      .exclude(status=RequestScan.STATUS_IMPORTED).select_related('beneficiary')),
            'scans_done': RequestScan.objects.filter(facility=facility, year=year, month=month,
                                                     status=RequestScan.STATUS_IMPORTED).count(),
            'beneficiaries': Beneficiary.objects.filter(facility=facility, status=Beneficiary.STATUS_ACTIVE),
            'ai_enabled': bool(settings.ANTHROPIC_API_KEY),
            'targets_missing': sum(1 for t in targets if t['missing']),
            'default_deadline': max(datetime.date(*monthly.prev_month(year, month), 20), datetime.date.today()),
            **_month_ctx(year, month),
        })

    def post(self, request, year, month):
        """「月間予定表を作る」：利用希望から予約を割り当てる"""
        year, month = month_or_404(year, month)
        facility = request.user.facility
        setting = services.get_setting(facility)
        result = monthly.assign_month(facility, year, month, setting, base=request.build_absolute_uri('/'))
        if result.made or result.short:
            messages.success(request, result.summary + (
                ' 通知は送信待ちに入れています。' if result.notices else ''))
        else:
            messages.info(request, '割り当てるものがありません（利用希望が無いか、希望回数ぶんの予約がすでにあります）。')
        return redirect('reservations:monthly_schedule', year=year, month=month)


class MonthlyWishAskView(SlotModeMixin, View):
    """
    保護者に利用希望を入力してもらう（顧客ページの URL を配る）。
    action=ask は連絡先ごとにお願いを積み、LINE でつながっている方にはその場で送る。
    action=contacts は連絡先の無い利用者に、保護者台帳から顧客を作る。
    action=handed は「コピーして送る」のお願いを、渡しおえた（送信ずみ）にする。
    """

    def post(self, request, year, month):
        year, month = month_or_404(year, month)
        facility = request.user.facility
        setting = services.get_setting(facility)
        back = redirect(reverse('reservations:monthly_requests', args=[year, month]) + '#ask')
        action = request.POST.get('action')
        base = request.build_absolute_uri('/')

        if action == 'contacts':
            _, no_contact = monthly.wish_targets(facility, year, month, base)
            made, joined = monthly.make_contacts(facility, no_contact)
            if made or joined:
                messages.success(request, f'連絡先を {len(made)} 件作りました' + (
                    f'（{len(joined)} 名はきょうだいの連絡先に足しました）' if joined else '') + '。')
            else:
                messages.info(request, '連絡先の無い方はいません。')
            return back

        if action == 'handed':
            notice = get_object_or_404(ReservationNotice, pk=to_int(request.POST.get('notice'), -1), facility=facility,
                                       kind=ReservationNotice.KIND_WISH, status__in=monthly.WISH_NOTICE_OPEN)
            notice.status, notice.sent_at = ReservationNotice.STATUS_SENT, timezone.now()
            notice.error_message = '職員が文面をコピーして送りました'
            notice.save(update_fields=['status', 'sent_at', 'error_message'])
            messages.success(request, f'{notice.customer.name if notice.customer else ""} 様へのお願いを「送った」にしました。')
            return back

        if action != 'ask':
            messages.error(request, '操作を選んでください。')
            return back
        try:
            deadline = datetime.date.fromisoformat(request.POST.get('deadline', '')) if request.POST.get('deadline') else None
        except ValueError:
            deadline = None
        only = None
        if request.POST.get('customer'):
            only = {to_int(request.POST.get('customer'), -1)}
        notices = monthly.ask_for_wishes(facility, year, month, setting, base=base, deadline=deadline,
                                         only_missing=request.POST.get('scope', 'missing') != 'all', customers=only)
        if not notices:
            messages.info(request, 'お願いを送る相手がいません（全員の利用希望が届いているか、連絡先がありません）。')
            return back
        line_ids = [n.pk for n in notices if n.status == ReservationNotice.STATUS_PENDING]
        sent = failed = 0
        if line_ids:
            from line_integration.sending import send_reservation_notices
            sent, failed = send_reservation_notices(facility, ids=line_ids)
        manual = len(notices) - len(line_ids)
        text = f'{month}月の利用希望の入力のお願いを {len(notices)} 件作りました。'
        if sent:
            text += f' LINE で {sent} 件送りました。'
        if failed:
            text += f' {failed} 件は LINE で送れませんでした（公式LINEの設定を確かめてください）。'
        if manual:
            text += f' LINE でつながっていない {manual} 件は、下の一覧の「文面をコピー」で送ってください。'
        messages.success(request, text)
        return back


class MonthlyRequestEditView(SlotModeMixin, View):
    """利用者1人の月予約利用希望（紙の用紙を転記する画面）"""
    template_name = 'reservations/monthly_request_edit.html'

    @staticmethod
    def _scan(request, facility, year, month):
        """?scan=<ID>（または送信された scan）の、この月の用紙の写真"""
        pk = to_int(request.GET.get('scan') or request.POST.get('scan'), 0)
        if not pk:
            return None
        return get_object_or_404(RequestScan, pk=pk, facility=facility, year=year, month=month)

    def get(self, request, year, month, pk):
        year, month = month_or_404(year, month)
        facility = request.user.facility
        beneficiary = get_object_or_404(Beneficiary, pk=pk, facility=facility)
        setting = services.get_setting(facility)
        req = MonthlyRequest.objects.filter(beneficiary=beneficiary, year=year, month=month).first()
        scan = self._scan(request, facility, year, month)
        shown = req
        if scan is not None and scan.status == RequestScan.STATUS_EXTRACTED:
            shown = scan_mod.as_request(scan)
            if shown.desired_count == 0 and req is not None and not (scan.extracted or {}).get('desired_count'):
                shown.desired_count = req.desired_count     # 回数が読めなければ、いまの値を残す
        grid = monthly.request_grid(facility, year, month, setting, request=shown)
        other_name = ''
        if scan is not None and scan.read_name:
            found, _ = scan_mod.match_beneficiary(facility, scan.read_name)
            if found is not None and found.pk != beneficiary.pk:
                other_name = found.full_name
        return render(request, self.template_name, {
            'beneficiary': beneficiary, 'req': req, 'shown': shown, 'setting': setting, 'grid': grid,
            'scan': scan, 'scan_other_name': other_name,
            'ai_enabled': bool(settings.ANTHROPIC_API_KEY),
            'confirmed': [r for r in monthly.month_reservations(facility, year, month) if r.beneficiary_id == pk],
            **_month_ctx(year, month),
        })

    def post(self, request, year, month, pk):
        year, month = month_or_404(year, month)
        facility = request.user.facility
        beneficiary = get_object_or_404(Beneficiary, pk=pk, facility=facility)
        setting = services.get_setting(facility)
        back = redirect('reservations:monthly_requests', year=year, month=month)
        if request.POST.get('action') == 'delete':
            MonthlyRequest.objects.filter(beneficiary=beneficiary, year=year, month=month).delete()
            messages.success(request, f'{beneficiary.full_name} さんの {month}月の利用希望を消しました。')
            return back
        wishes = monthly.wishes_from_post(request.POST, facility, year, month, setting)
        desired = to_int(request.POST.get('desired_count'), 0)
        scan = self._scan(request, facility, year, month)
        req = monthly.save_request(facility, beneficiary, year, month, desired, wishes,
                                   note=request.POST.get('note', '').strip(), user=request.user,
                                   source=MonthlyRequest.SOURCE_PHOTO if scan else MonthlyRequest.SOURCE_STAFF)
        if scan is not None:
            scan.status, scan.request, scan.beneficiary = RequestScan.STATUS_IMPORTED, req, beneficiary
            scan.save(update_fields=['status', 'request', 'beneficiary'])
        messages.success(request, f'{beneficiary.full_name} さんの {month}月の利用希望を保存しました'
                                  f'（希望 {req.desired_count} 回・○ {req.slot_count(setting)} 枠）。')
        if scan is not None and request.POST.get('action') != 'assign':
            nxt = (RequestScan.objects.filter(facility=facility, year=year, month=month)
                   .exclude(status=RequestScan.STATUS_IMPORTED).exclude(pk=scan.pk).first())
            if nxt is not None and nxt.beneficiary_id and nxt.status == RequestScan.STATUS_EXTRACTED:
                messages.info(request, '次の用紙の写真です。')
                return redirect(reverse('reservations:monthly_request_edit', args=[year, month, nxt.beneficiary_id])
                                + f'?scan={nxt.pk}')
            return redirect(reverse('reservations:monthly_requests', args=[year, month]) + '#scans')
        if request.POST.get('action') == 'assign':
            result = monthly.assign_month(facility, year, month, setting, only=[req],
                                          base=request.build_absolute_uri('/'))
            messages.success(request, result.summary)
            return redirect('reservations:monthly_schedule', year=year, month=month)
        return back


MAX_SCAN_UPLOAD = 20


class RequestScanUploadView(SlotModeMixin, View):
    """紙の利用希望の写真を取り込む（1回に20枚まで）。利用者を選べば、その子の用紙として扱う"""

    def post(self, request, year, month):
        year, month = month_or_404(year, month)
        facility = request.user.facility
        back = redirect(reverse('reservations:monthly_requests', args=[year, month]) + '#scans')
        files = [f for f in request.FILES.getlist('images')[:MAX_SCAN_UPLOAD]
                 if (f.content_type or '').startswith('image/')]
        if not files:
            messages.error(request, '用紙の写真（JPEG・PNG・HEIC など）を選んでください。')
            return back
        beneficiary = None
        if request.POST.get('beneficiary'):
            beneficiary = get_object_or_404(Beneficiary, pk=to_int(request.POST.get('beneficiary'), -1), facility=facility)
        made = [RequestScan.objects.create(facility=facility, year=year, month=month, beneficiary=beneficiary,
                                           image=f, uploaded_by=request.user) for f in files]
        if beneficiary is not None and len(made) == 1 and request.POST.get('next') == 'edit':
            return redirect(reverse('reservations:monthly_request_edit', args=[year, month, beneficiary.pk])
                            + f'?scan={made[0].pk}')
        messages.success(request, f'{len(made)} 枚を取り込みました。「AIで読み取る」を押すと、○の位置を読み取ります。')
        return back


class RequestScanExtractView(SlotModeMixin, View):
    """用紙の写真1枚を AI で読み取る（画面から fetch で呼ぶ）"""

    def post(self, request, year, month, pk):
        year, month = month_or_404(year, month)
        scan = get_object_or_404(RequestScan, pk=pk, facility=request.user.facility, year=year, month=month)
        if not settings.ANTHROPIC_API_KEY:
            return JsonResponse({'error': 'AIを使う設定（ANTHROPIC_API_KEY）がサーバーにありません。'}, status=500)
        try:
            data = scan_mod.extract(scan)
        except Exception as e:  # noqa: BLE001
            logger.exception('利用希望の用紙の読み取りに失敗（scan %s）', scan.pk)
            scan.error = f'{type(e).__name__}: {e}'[:500]
            scan.save(update_fields=['error'])
            return JsonResponse({'error': f'読み取りに失敗しました（{type(e).__name__}）。写真を撮り直すか、もう一度お試しください。'},
                                status=500)
        return JsonResponse({
            'ok': True, 'status': scan.get_status_display(), 'name': data.get('name', ''),
            'beneficiary_id': scan.beneficiary_id,
            'beneficiary_name': scan.beneficiary.full_name if scan.beneficiary else '',
            'desired_count': data.get('desired_count'), 'days': len(data.get('wishes') or {}),
            'slots': data.get('slot_count', 0), 'unreadable': data.get('unreadable', ''),
            'month_mismatch': data.get('month_mismatch', False),
            'review_url': (reverse('reservations:monthly_request_edit', args=[year, month, scan.beneficiary_id])
                           + f'?scan={scan.pk}') if scan.beneficiary_id else '',
        })


class RequestScanActionView(SlotModeMixin, View):
    """用紙の写真の利用者を選ぶ（action=assign → 確認画面へ）・消す（action=delete）"""

    def post(self, request, year, month, pk):
        year, month = month_or_404(year, month)
        facility = request.user.facility
        scan = get_object_or_404(RequestScan, pk=pk, facility=facility, year=year, month=month)
        back = redirect(reverse('reservations:monthly_requests', args=[year, month]) + '#scans')
        if request.POST.get('action') == 'delete':
            scan.image.delete(save=False)
            scan.delete()
            messages.success(request, '用紙の写真を消しました。')
            return back
        beneficiary = get_object_or_404(Beneficiary, pk=to_int(request.POST.get('beneficiary'), -1), facility=facility)
        scan.beneficiary = beneficiary
        scan.save(update_fields=['beneficiary'])
        return redirect(reverse('reservations:monthly_request_edit', args=[year, month, beneficiary.pk]) + f'?scan={scan.pk}')


class MonthlyRequestFormView(SlotModeMixin, View):
    """月予約利用希望の用紙（PDF）。?b=<利用者ID> で名前と○入り、無ければ空の用紙"""

    def get(self, request, year, month):
        from config.pdf import pdf_or_html
        year, month = month_or_404(year, month)
        facility = request.user.facility
        setting = services.get_setting(facility)
        beneficiary = None
        req = None
        if request.GET.get('b'):
            beneficiary = get_object_or_404(Beneficiary, pk=to_int(request.GET.get('b'), -1), facility=facility)
            req = MonthlyRequest.objects.filter(beneficiary=beneficiary, year=year, month=month).first()
        ctx = {'facility': facility, 'setting': setting, 'beneficiary': beneficiary, 'req': req,
               'grid': monthly.request_grid(facility, year, month, setting, request=req),
               'year': year, 'month': month}
        name = f'{month}月予約利用希望' + (f'_{beneficiary.full_name}' if beneficiary else '')
        return pdf_or_html(request, 'reservations/pdf/request_form.html', ctx, name)


class MonthlyScheduleView(SlotModeMixin, View):
    """月間予定表（週×時間枠×1枠の人数）。空の箱を押すとその日の画面で追加できる"""
    template_name = 'reservations/monthly_schedule.html'

    def get(self, request, year, month):
        year, month = month_or_404(year, month)
        facility = request.user.facility
        setting = services.get_setting(facility)
        rows = monthly.request_rows(facility, year, month, setting)
        return render(request, self.template_name, {
            'schedule': monthly.month_schedule(facility, year, month, setting), 'setting': setting,
            'rows': [r for r in rows if r['request'] or r['confirmed']],
            'facility': facility, 'today': datetime.date.today(),
            'therapy': getattr(facility, 'use_therapy_record', False),
            **_month_ctx(year, month),
        })


class MonthlyScheduleSwapView(SlotModeMixin, View):
    """
    月間予定表の手直し。action=swap は2人の枠を入れ替え、action=move は空いている枠へ移す。
    保護者へのお知らせは積まず、送信待ちの「ご利用日が決まりました」はいまの予約に合わせて書き直す。
    """

    def post(self, request, year, month):
        year, month = month_or_404(year, month)
        facility = request.user.facility
        setting = services.get_setting(facility)
        back = redirect('reservations:monthly_schedule', year=year, month=month)
        res_a = get_object_or_404(Reservation, pk=to_int(request.POST.get('a'), -1), facility=facility)
        action = request.POST.get('action')
        try:
            if action == 'swap':
                res_b = get_object_or_404(Reservation, pk=to_int(request.POST.get('b'), -1), facility=facility)
                touched = [(res_a.beneficiary, res_a.date), (res_b.beneficiary, res_b.date)]
                monthly.swap_reservations(res_a, res_b)
                msg = (f'{res_a.display_name} さんと {res_b.display_name} さんを入れ替えました'
                       f'（{services.jp_date(res_a.date)} {res_a.time_label} ⇄ '
                       f'{services.jp_date(res_b.date)} {res_b.time_label}）。')
            elif action == 'move':
                try:
                    day = datetime.date.fromisoformat(request.POST.get('date', ''))
                except ValueError:
                    messages.error(request, '移す先の日にちが正しくありません。')
                    return back
                touched = [(res_a.beneficiary, res_a.date)]
                res_a = monthly.move_on_schedule(res_a, day, to_int(request.POST.get('hour'), -1))
                msg = f'{res_a.display_name} さんを {services.jp_date(res_a.date)} {res_a.time_label} へ移しました。'
            else:
                messages.error(request, '操作を選んでください。')
                return back
        except services.ReservationError as e:
            messages.error(request, str(e))
            return back
        refreshed = 0
        for beneficiary, day in touched + [(res_a.beneficiary, res_a.date)]:
            if beneficiary is not None and monthly.refresh_month_notice(facility, beneficiary, day.year, day.month, setting):
                refreshed += 1
        if refreshed:
            msg += ' 送信待ちの「ご利用日が決まりました」も書き直しました。'
        messages.success(request, msg)
        return back


class MonthlySchedulePdfView(SlotModeMixin, View):
    """月間予定表の PDF（A4 横）"""

    def get(self, request, year, month):
        from config.pdf import pdf_or_html
        year, month = month_or_404(year, month)
        facility = request.user.facility
        setting = services.get_setting(facility)
        rows = monthly.request_rows(facility, year, month, setting)
        ctx = {'schedule': monthly.month_schedule(facility, year, month, setting), 'setting': setting,
               'rows': [r for r in rows if r['request'] or r['confirmed']], 'facility': facility,
               'year': year, 'month': month}
        return pdf_or_html(request, 'reservations/pdf/monthly_schedule.html', ctx, f'{year}年{month}月_月間予定表')

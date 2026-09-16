"""
顧客向けの予定表（ログインなしで見えるページ）。

- /yoyaku/aki/<公開アドレス>/       …… 事業所の空き状況だけ（名前は出さない）
- /yoyaku/mypage/<顧客のアドレス>/  …… その顧客の予約の確認・申し込み・取り消し

どちらもアドレスそのものが合い言葉なので、名前や連絡先は出さない。
顧客ページで見えるのは、その顧客が担当する利用者のぶんだけ。

**アドレスを1字でも書き換えたら開けない。** アドレスには種類ごとの署名が入っていて、
DB を引く前に確かめる（`reservations/tokens.py`）。
署名が合わないアドレスは、空き状況ページのものを顧客ページに貼った場合も含めて、
すべて同じ 404 を返す（当たっているかどうかを応答の違いから測れない）。
開けないアドレスが続いた相手は記録に残す（総当たりへの気づきのため）。
"""
import calendar
import datetime
import logging

from django.contrib import messages
from django.core.cache import cache
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from config.utils import month_or_404, reservation_enabled, to_int

from . import services, tokens
from .models import Customer, Reservation, ReservationSetting

logger = logging.getLogger(__name__)

POST_LIMIT = 30           # 1つのアドレスから
POST_WINDOW = 60 * 60     # 1時間に送れる操作の数
BAD_LIMIT = 10            # 開けないアドレスがこの回数続いたら記録に残す
BAD_WINDOW = 60 * 60      # その数え直しまでの秒数


def client_key(request):
    """回数を数える相手の見分け（前段の nginx が付ける転送元があればそれを使う）"""
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    ip = forwarded.split(',')[0].strip() if forwarded else request.META.get('REMOTE_ADDR', '')
    return ip or 'unknown'


def _month_links(year, month):
    prev_y, prev_m = (year - 1, 12) if month == 1 else (year, month - 1)
    next_y, next_m = (year + 1, 1) if month == 12 else (year, month + 1)
    return {'prev_year': prev_y, 'prev_month': prev_m, 'next_year': next_y, 'next_month': next_m}


def _month_weeks(facility, year, month, today, bookable_of=None):
    """月のマス目。空き数だけを入れ、予約している人の名前は入れない"""
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
            cell = dict(states[d])
            cell.update({'day': day_num, 'is_today': d == today, 'is_past': d < today,
                         'is_sunday': d.weekday() == 6, 'is_saturday': d.weekday() == 5,
                         'iso': d.isoformat()})
            cell.update(bookable_of(d, cell) if bookable_of else {})
            row.append(cell)
        weeks.append(row)
    return weeks


class PublicPageMixin(View):
    """
    アドレスの確かめ方と、検索に載せない・キャッシュさせない設定。

    アドレスは DB を引く前に署名を確かめる。1字でも違えば（打ち間違いでも、
    別のページのアドレスを貼った場合でも）DB に触れずに 404 を返す。
    """
    token_kind = None

    def dispatch(self, request, *args, **kwargs):
        token = kwargs.get('token', '')
        if self.token_kind and not self.check_token(request, token):
            raise Http404('アドレスが正しくありません')
        response = super().dispatch(request, *args, **kwargs)
        response['X-Robots-Tag'] = 'noindex, nofollow'
        response['Cache-Control'] = 'no-store'
        return response

    def check_token(self, request, token):
        """
        このページ用の、書き換えられていないアドレスか。

        正しいアドレスはいつでも開ける（打ち間違いが続いても、本人を締め出さない）。
        開けないアドレスは数えておき、続けて試された相手は記録に残す（総当たりの気づきのため）。
        """
        if tokens.is_valid(self.token_kind, token):
            return True
        key = f'reservation_public_bad:{client_key(request)}'
        try:
            count = cache.incr(key)
        except ValueError:
            cache.set(key, 1, BAD_WINDOW)
            count = 1
        if count == BAD_LIMIT:
            logger.warning('予約の公開ページ：開けないアドレスが %s 回続いた（%s）', count, client_key(request))
        return False

    def throttled(self, request, token):
        key = f'reservation_public_post:{token[:24]}'
        count = cache.get(key, 0)
        if count >= POST_LIMIT:
            return True
        cache.set(key, count + 1, POST_WINDOW)
        return False


class PublicCalendarView(PublicPageMixin):
    """事業所の空き状況（だれでも見られるが、アドレスを知っている人だけ）"""
    template_name = 'reservations/public/calendar.html'
    token_kind = tokens.CALENDAR

    def get(self, request, token, year=None, month=None):
        setting = get_object_or_404(ReservationSetting.objects.select_related('facility'),
                                    public_token=token, public_calendar=True)
        facility = setting.facility
        if not reservation_enabled(facility):
            raise Http404('この事業所では予約管理を使っていません')
        today = datetime.date.today()
        year, month = month_or_404(year or today.year, month or today.month)
        return render(request, self.template_name, {
            'setting': setting, 'facility': facility, 'year': year, 'month': month, 'today': today,
            'weeks': _month_weeks(facility, year, month, today),
            'month_url': 'reservations_public:calendar_month', 'token': token,
            **_month_links(year, month),
        })


class CustomerPageView(PublicPageMixin):
    """顧客専用ページ：予定表・自分の予約・申し込み・取り消し"""
    template_name = 'reservations/public/customer.html'
    token_kind = tokens.CUSTOMER

    def get_customer(self, token):
        customer = get_object_or_404(
            Customer.objects.select_related('facility').prefetch_related('children'), token=token)
        if not reservation_enabled(customer.facility):
            raise Http404('この事業所では予約管理を使っていません')
        return customer

    def get(self, request, token, year=None, month=None):
        customer = self.get_customer(token)
        facility = customer.facility
        setting = services.get_setting(facility)
        today = datetime.date.today()
        year, month = month_or_404(year or today.year, month or today.month)
        start, end = setting.booking_window(today)

        def mark(day, cell):
            ok = (setting.public_booking and start <= day <= end and not cell['closed']
                  and (not cell['full'] or setting.allow_waitlist))
            return {'can_book': ok, 'waitlist_only': ok and cell['full']}

        children = list(customer.children.all())
        mine = (Reservation.objects.filter(facility=facility, beneficiary__in=children, date__gte=today,
                                           status__in=Reservation.ACTIVE_STATUSES)
                .select_related('beneficiary').order_by('date') if children else [])
        return render(request, self.template_name, {
            'customer': customer, 'facility': facility, 'setting': setting, 'children': children,
            'year': year, 'month': month, 'today': today,
            'weeks': _month_weeks(facility, year, month, today, bookable_of=mark),
            'my_reservations': mine, 'book_from': start, 'book_until': end,
            'token': token, 'month_url': 'reservations_public:customer_month',
            **_month_links(year, month),
        })

    def post(self, request, token, year=None, month=None):
        customer = self.get_customer(token)
        facility = customer.facility
        setting = services.get_setting(facility)
        back = redirect('reservations_public:customer', token=token)
        if self.throttled(request, token):
            messages.error(request, '短い時間に操作が続いたため、しばらく受け付けられません。'
                                    'お急ぎの場合は事業所へお電話ください。')
            return back

        action = request.POST.get('action', 'book')
        children = {b.pk: b for b in customer.children.all()}
        if not children:
            messages.error(request, 'お子さまの登録がまだありません。事業所へご連絡ください。')
            return back

        if action == 'cancel':
            # 自分が担当する利用者のぶんなら、職員が入れた予約も取り消せる
            res = Reservation.objects.filter(pk=to_int(request.POST.get('reservation'), -1),
                                             facility=facility,
                                             status__in=Reservation.ACTIVE_STATUSES).first()
            if res is None or res.beneficiary_id not in children:
                messages.error(request, 'そのご予約は見つかりませんでした。')
                return back
            if res.date < datetime.date.today():
                messages.error(request, '過ぎた日のご予約は取り消せません。')
                return back
            services.cancel_reservation(res)
            messages.success(request, f'{services.jp_date(res.date)} '
                                      f'{res.beneficiary.full_name}さんのご予約を取り消しました。')
            return back

        beneficiary = children.get(to_int(request.POST.get('beneficiary'), -1))
        try:
            day = datetime.date.fromisoformat(request.POST.get('date', ''))
        except ValueError:
            day = None
        if beneficiary is None or day is None:
            messages.error(request, 'お子さまと日にちを選んでください。')
            return back

        ok, reason = services.bookable(facility, day, setting)
        if not ok:
            messages.error(request, reason)
            return back
        try:
            res, _ = services.create_reservation(facility, beneficiary, day,
                                                 source=Reservation.SOURCE_WEB, customer=customer)
        except services.ReservationError as e:
            messages.error(request, str(e))
            return back
        if res.status == Reservation.STATUS_CONFIRMED:
            messages.success(request, f'{services.jp_date(day)} {beneficiary.full_name}さんの'
                                      'ご予約を承りました。')
        else:
            messages.success(request, f'{services.jp_date(day)} {beneficiary.full_name}さんは'
                                      'キャンセル待ちでお預かりしました。空きが出ましたらお知らせします。')
        return back

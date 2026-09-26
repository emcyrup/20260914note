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
from django.urls import reverse
from django.views import View

from config.utils import month_or_404, reservation_enabled, to_int

from . import monthly, services, tokens
from .models import Customer, MonthlyRequest, Reservation, ReservationSetting

logger = logging.getLogger(__name__)

POST_LIMIT = 30           # 1つのアドレスから1時間に送れる操作の数
POST_WINDOW = 60 * 60     # その数え直しまでの秒数
REQUEST_LIMIT = 5         # 同じ相手が1時間に送れる申し込みの数
BAD_LIMIT = 10            # 開けないアドレスがこの回数続いたら記録に残す
BAD_WINDOW = 60 * 60      # その数え直しまでの秒数


def client_key(request):
    """回数を数える相手の見分け（前段の nginx が付ける転送元があればそれを使う）"""
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    ip = forwarded.split(',')[0].strip() if forwarded else request.META.get('REMOTE_ADDR', '')
    return ip or 'unknown'


def today_first():
    today = datetime.date.today()
    return datetime.date(today.year, today.month, 1)


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

    def throttled(self, request, token, limit=POST_LIMIT):
        """同じアドレス（申し込みは同じ相手）から、短い時間に操作が続いていないか"""
        key = f'reservation_public_post:{token[:40]}'
        count = cache.get(key, 0)
        if count >= limit:
            return True
        cache.set(key, count + 1, POST_WINDOW)
        return False


class PublicCalendarView(PublicPageMixin):
    """
    事業所の空き状況（アドレスを知っている人なら誰でも開ける）。

    申し込みを受け付ける設定のときは、この画面から申し込める。
    申し込みは **その場では予約にならない**。職員が「どの利用者か」を確かめて反映したときだけ予約になる
    （知らない名前で枠が埋まらないように）。
    """
    template_name = 'reservations/public/calendar.html'
    token_kind = tokens.CALENDAR

    def get_setting(self, token):
        setting = get_object_or_404(ReservationSetting.objects.select_related('facility'),
                                    public_token=token, public_calendar=True)
        if not reservation_enabled(setting.facility):
            raise Http404('この事業所では予約管理を使っていません')
        return setting

    def get(self, request, token, year=None, month=None):
        setting = self.get_setting(token)
        facility = setting.facility
        today = datetime.date.today()
        year, month = month_or_404(year or today.year, month or today.month)
        start, end = setting.booking_window(today)

        def mark(day, cell):
            ok = (setting.public_request and start <= day <= end and not cell['closed']
                  and (not cell['full'] or setting.allow_waitlist))
            return {'can_book': ok, 'waitlist_only': ok and cell['full']}

        return render(request, self.template_name, {
            'setting': setting, 'facility': facility, 'year': year, 'month': month, 'today': today,
            'weeks': _month_weeks(facility, year, month, today,
                                  bookable_of=mark if setting.public_request else None),
            'month_url': 'reservations_public:calendar_month', 'token': token,
            'book_from': start, 'book_until': end,
            **_month_links(year, month),
        })

    def post(self, request, token, year=None, month=None):
        """申し込みを受け取る（予約にはしない）"""
        setting = self.get_setting(token)
        facility = setting.facility
        back = redirect('reservations_public:calendar', token=token)
        if not setting.public_request:
            raise Http404('このページからの申し込みは受け付けていません')
        if request.POST.get('website'):
            return back   # 見えない欄。人は書かない（自動投稿よけ）
        if self.throttled(request, f'req:{client_key(request)}', limit=REQUEST_LIMIT):
            messages.error(request, '短い時間にお申し込みが続いたため、しばらく受け付けられません。'
                                    'お急ぎの場合は事業所へお電話ください。')
            return back

        p = request.POST
        name = p.get('name', '').strip()
        child_name = p.get('child_name', '').strip()
        try:
            day = datetime.date.fromisoformat(p.get('date', ''))
        except ValueError:
            day = None
        if not name or not child_name or day is None:
            messages.error(request, 'お名前・お子さまのお名前・日にちを入れてください。')
            return back

        ok, reason = services.bookable(facility, day, setting)
        if not ok:
            messages.error(request, reason)
            return back

        try:
            req, res = services.receive_request(
                facility, day, name=name, kana=p.get('kana', '').strip(), phone=p.get('phone', '').strip(),
                child_name=child_name, note=p.get('note', '').strip(), setting=setting)
        except services.ReservationError as e:
            messages.error(request, str(e))
            return back
        self.tell_result(request, day, res, child_name)
        return back

    @staticmethod
    def tell_result(request, day, res, who):
        """申し込みの結果をその場で伝える（満席で受けられなかったときも、はっきり伝える）"""
        d = services.jp_date(day)
        if res is None:
            messages.success(request, f'{d} のお申し込みを承りました。'
                                      '事業所で確認のうえ、あらためてご連絡します。')
        elif res.status == Reservation.STATUS_CONFIRMED:
            messages.success(request, f'{d} {res.display_name or who}さんのご予約を承りました。')
        elif res.status == Reservation.STATUS_WAITLIST:
            messages.warning(request, f'{d} はちょうど満席になりました。'
                                      f'{res.display_name or who}さんはキャンセル待ちでお預かりします。'
                                      '空きが出ましたらお知らせします。')
        else:
            messages.error(request, f'{d} は満席のため、ご予約をお受けできませんでした。'
                                    'ほかの日でご検討いただくか、事業所へお問い合わせください。')


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
                .select_related('beneficiary').order_by('date', 'start_time') if children else [])
        ctx = {
            'customer': customer, 'facility': facility, 'setting': setting, 'children': children,
            'year': year, 'month': month, 'today': today,
            'weeks': _month_weeks(facility, year, month, today, bookable_of=mark),
            'my_reservations': mine, 'book_from': start, 'book_until': end,
            'token': token, 'month_url': 'reservations_public:customer_month',
            **_month_links(year, month),
        }
        if setting.slot_mode and children:
            ctx.update(self.wish_context(request, facility, setting, children, today))
        return render(request, self.template_name, ctx)

    @staticmethod
    def wish_context(request, facility, setting, children, today):
        """月予約利用希望の入力（来月ぶんが既定。?wish=YYYY-MM と ?child=<ID> で切り替え）"""
        ny, nm = monthly.next_month(today.year, today.month)
        choices = [(today.year, today.month), (ny, nm), monthly.next_month(ny, nm)]
        wish_year, wish_month = ny, nm
        try:
            y, m = request.GET.get('wish', '').split('-')
            if (int(y), int(m)) in choices:
                wish_year, wish_month = int(y), int(m)
        except ValueError:
            pass
        sent = {r.beneficiary_id: r for r in MonthlyRequest.objects.filter(
            beneficiary__in=children, year=wish_year, month=wish_month)}
        chosen = next((b for b in children if str(b.pk) == request.GET.get('child', '')), None)
        # 選ばれていなければ、まだ送っていないお子さまを先に出す
        child = chosen or next((b for b in children if b.pk not in sent), children[0])
        req = sent.get(child.pk)
        first, last = monthly.month_range(wish_year, wish_month)
        return {
            'wish_year': wish_year, 'wish_month': wish_month, 'wish_child': child, 'wish_req': req,
            'wish_sent_ids': set(sent),
            'wish_waiting': [b for b in children if b.pk not in sent and b.pk != child.pk],
            'wish_decided': Reservation.objects.filter(
                beneficiary=child, date__gte=first, date__lte=last, status=Reservation.STATUS_CONFIRMED).exists(),
            'wish_choices': [{'year': y, 'month': m, 'value': f'{y}-{m:02d}', 'selected': (y, m) == (wish_year, wish_month)}
                             for y, m in choices],
            'wish_grid': monthly.request_grid(facility, wish_year, wish_month, setting, request=req),
            'slot_hours': setting.all_slot_hours(),
        }

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
                                      f'{res.display_name}さんのご予約を取り消しました。')
            return back

        beneficiary = children.get(to_int(request.POST.get('beneficiary'), -1))

        if action == 'wish':
            # 月予約利用希望（時間枠で予約する事業所だけ）
            if not setting.slot_mode or beneficiary is None:
                messages.error(request, 'お子さまを選んでください。')
                return back
            year, month = to_int(request.POST.get('wish_year'), 0), to_int(request.POST.get('wish_month'), 0)
            if not (1 <= month <= 12 and 2000 <= year <= 2100) or datetime.date(year, month, 1) < today_first():
                messages.error(request, 'その月のご希望は受け付けられません。')
                return back
            wishes = monthly.wishes_from_post(request.POST, facility, year, month, setting)
            again = MonthlyRequest.objects.filter(beneficiary=beneficiary, year=year, month=month).exists()
            req = monthly.save_request(facility, beneficiary, year, month,
                                       to_int(request.POST.get('desired_count'), 0), wishes,
                                       note=request.POST.get('note', '').strip(),
                                       source=MonthlyRequest.SOURCE_WEB, customer=customer,
                                       wish_mode=(monthly.wish_mode_from_post(request.POST) if facility.is_ryoiku
                                                  else MonthlyRequest.WISH_OK),
                                       ng_dates=monthly.ng_from_post(request.POST, year, month))
            first, last = monthly.month_range(year, month)
            decided = Reservation.objects.filter(beneficiary=beneficiary, date__gte=first, date__lte=last,
                                                 status=Reservation.STATUS_CONFIRMED).exists()
            monthly.tell_staff_wish(facility, setting, req, again=again, decided=decided)
            text = (f'{month}月の {beneficiary.full_name}さんのご希望を承りました'
                    f'（{monthly.wish_summary(req, setting)}）。')
            text += ('この月の予定はすでに組んでいるため、変更は事業所で確かめてからご連絡します。' if decided
                     else '事業所で予定を組み、決まりましたらお知らせします。')
            messages.success(request, text)
            nxt = next((b for pk, b in children.items() if pk != beneficiary.pk and not MonthlyRequest.objects.filter(
                beneficiary=b, year=year, month=month).exists()), beneficiary)
            return redirect(f"{reverse('reservations_public:customer', args=[token])}"
                            f"?wish={year}-{month:02d}&child={nxt.pk}#wishCard")

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
                                                 source=Reservation.SOURCE_WEB, customer=customer,
                                                 start_time=request.POST.get('start_time'))
        except services.ReservationError as e:
            messages.error(request, str(e))
            return back
        PublicCalendarView.tell_result(request, day, res, beneficiary.full_name)
        return back

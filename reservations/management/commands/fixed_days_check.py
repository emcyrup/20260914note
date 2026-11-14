"""
「利用予定曜日から予定を作る」（オウルの予定）が正しく動いているかを、サーバーの上で確かめる（2026-11-09）。
  python manage.py fixed_days_check --facility 1

数だけを出す（利用者の名前は出さない。Actions のログは公開リポジトリで見えるため）。
確かめること：
  1. 設定がオンか・在籍中の利用者と利用予定曜日
  2. 今月・来月の予定の数（予定・来所・欠席・キャンセル・待）、休業日の予定、利用予定曜日でない日の予定、同じ人の二重
  3. 「まとめて作成」をもう一度押したら何件増えるか（試しに作って取り消す。DB は変えない）
  4. 出欠入力：今日（無ければ予定のある直近の日）の行を読み、同じ値で保存しても変わらないか（試して取り消す）
  5. 画面：予定のカレンダーと出欠入力を、ログインせずに組み立てて開けるか（ステータス 200・印の有無）
  6. 送迎を使う事業所：その日の配車表の行と、迎え・送りの印の数が合うか
"""
import calendar
import datetime

from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.db import SessionStore
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.test import RequestFactory
from django.utils import timezone

from accounts.models import StaffAccount
from beneficiaries.models import Beneficiary
from facilities.models import Facility

from reservations import fixed, services
from reservations.models import Reservation


class _Rollback(Exception):
    pass


class Command(BaseCommand):
    help = '利用予定曜日から予定を作る（オウルの予定）の動作確認。数だけを出す'

    def add_arguments(self, parser):
        parser.add_argument('--facility', type=int, required=True)

    def ok(self, label, cond, detail=''):
        self.results.append(cond)
        self.stdout.write(f'  [{"OK" if cond else "NG"}] {label}' + (f'：{detail}' if detail else ''))

    def handle(self, *args, **o):
        try:
            facility = Facility.objects.get(pk=o['facility'])
        except Facility.DoesNotExist:
            raise CommandError(f'施設 ID {o["facility"]} はありません')
        self.results = []
        setting = services.get_setting(facility)
        today = timezone.localdate()
        self.stdout.write(f'施設 ID {facility.pk}（予約を使う: {facility.use_reservation}・送迎: {facility.use_transport}）  {today}')

        self.stdout.write('1. 設定と利用者')
        self.ok('利用予定曜日から予定を作る がオン', setting.fixed_days)
        self.ok('予約（予定）を使う', facility.use_reservation)
        bens = list(Beneficiary.objects.filter(facility=facility, status=Beneficiary.STATUS_ACTIVE))
        with_days = [b for b in bens if b.scheduled_weekday_numbers]
        self.stdout.write(f'  在籍中 {len(bens)} 人（利用予定曜日あり {len(with_days)} 人・なし {len(bens) - len(with_days)} 人）'
                          f'  時間枠: {setting.slot_mode}・まとめて作る時刻: {setting.bulk_start_time}・休業曜日: {setting.closed_weekday_numbers()}')

        self.stdout.write('2. 今月・来月の予定')
        months = [(today.year, today.month)]
        months.append((today.year + (today.month == 12), today.month % 12 + 1))
        for y, m in months:
            first, last = datetime.date(y, m, 1), datetime.date(y, m, calendar.monthrange(y, m)[1])
            counts = fixed.month_counts(facility, first, last)
            tot = {k: sum(c[k] for c in counts.values()) for k in ('scheduled', 'attended', 'absent', 'cancelled', 'waiting')}
            active = list(Reservation.objects.filter(facility=facility, date__gte=first, date__lte=last,
                                                     status__in=Reservation.ACTIVE_STATUSES, beneficiary__isnull=False)
                          .select_related('beneficiary'))
            closed = services.closed_dates(facility, first, last)
            on_closed = sum(1 for r in active if services.is_closed(facility, r.date, setting, closed))
            off_weekday = sum(1 for r in active if r.date.weekday() not in r.beneficiary.scheduled_weekday_numbers)
            dup = len(active) - len({(r.beneficiary_id, r.date) for r in active})
            past_unentered = sum(1 for r in active if r.date < today and r.status == Reservation.STATUS_CONFIRMED and not r.attendance)
            self.stdout.write(f'  {y}年{m}月：予定 {tot["scheduled"]}・来所 {tot["attended"]}・欠席 {tot["absent"]}・キャンセル {tot["cancelled"]}'
                              f'・待 {tot["waiting"]}（休業日の予定 {on_closed}・利用予定曜日でない日 {off_weekday}・過ぎた日で出欠未入力 {past_unentered}）')
            self.ok(f'{y}年{m}月：同じ人・同じ日の予定が二重になっていない', dup == 0, f'{dup} 件')
            self.ok(f'{y}年{m}月：カレンダーの人数の合計が予定の数と合う', sum(tot.values()) == len(active) + Reservation.objects.filter(
                facility=facility, date__gte=first, date__lte=last, status__in=Reservation.ACTIVE_STATUSES, beneficiary__isnull=True).count())

        self.stdout.write('3. まとめて作成（試しに作って取り消す）')
        for y, m in months:
            try:
                with transaction.atomic():
                    made, skipped, no_days = fixed.bulk_create(facility, y, m, setting)
                    again = fixed.bulk_create(facility, y, m, setting)[0]
                    raise _Rollback()
            except _Rollback:
                pass
            self.stdout.write(f'  {y}年{m}月：押すと {made} 件増える（飛ばす {skipped} 件・曜日なし {no_days} 人）')
            self.ok(f'{y}年{m}月：2 回目は増えない', again == 0, f'{again} 件')

        self.stdout.write('4. 出欠入力（試しに保存して取り消す）')
        day = today
        if not Reservation.objects.filter(facility=facility, date=day, status__in=Reservation.ACTIVE_STATUSES).exists():
            nxt = Reservation.objects.filter(facility=facility, date__gte=today, status__in=Reservation.ACTIVE_STATUSES).order_by('date').first()
            day = nxt.date if nxt else today
        rows = fixed.day_rows(facility, day)
        on = [x for x in rows if x['state']]
        self.stdout.write(f'  {day}：行 {len(rows)}・予定のある人 {len(on)}（' + '・'.join(
            f'{lab} {sum(1 for x in rows if x["state"] == v)}' for v, lab in fixed.STATE_CHOICES) + '）')
        post = {}
        for x in rows:
            k = f'b_{x["beneficiary"].pk}_'
            post[f'{k}state'] = x['state']
            post[f'{k}note'] = x['note']
            if x['has_pickup']:
                post[f'{k}pickup'] = 'on'
            if x['has_dropoff']:
                post[f'{k}dropoff'] = 'on'
        try:
            with transaction.atomic():
                same = fixed.save_day(facility, day, post, setting)
                if on:
                    first = on[0]
                    k = f'b_{first["beneficiary"].pk}_'
                    post2 = dict(post, **{f'{k}state': Reservation.ATT_ATTENDED})
                    changed = fixed.save_day(facility, day, post2, setting)
                    after = {x['beneficiary'].pk: x for x in fixed.day_rows(facility, day)}[first['beneficiary'].pk]
                else:
                    changed, after = None, None
                raise _Rollback()
        except _Rollback:
            pass
        self.ok('同じ値で保存しても予定は変わらない', same == 0, f'{same} 件')
        if on:
            self.ok('1 人を「来所」にすると、その人だけ変わる', changed == 1 and after['state'] == Reservation.ATT_ATTENDED,
                    f'{changed} 件・状態 {after["state"] if after else "-"}')
        if facility.use_transport:
            from transport.views import build_board
            board = build_board(facility, day)
            for direction, key in (('pickup', 'has_pickup'), ('dropoff', 'has_dropoff')):
                on_board = sum(1 for x in board[direction] if not x['skip'])
                marked = sum(1 for x in rows if x[key])
                self.ok(f'迎え・送りの印が配車表と合う（{"迎え" if direction == "pickup" else "送り"}）', on_board == marked, f'配車表 {on_board}・印 {marked}')

        self.stdout.write('5. 画面（ログインせずに組み立てる）')
        user = (StaffAccount.objects.filter(facility=facility, is_active=True, role=StaffAccount.ROLE_ADMIN).order_by('pk').first()
                or StaffAccount.objects.filter(facility=facility, is_active=True).order_by('pk').first())
        if user is None:
            self.ok('職員がいる', False)
        else:
            from django.conf import settings
            from reservations.views import AttendanceDayView, CalendarView
            # サーバーの ALLOWED_HOSTS にある名前で組み立てる（testserver は本番・開発の設定では通らない）
            host = next((h.lstrip('.') for h in settings.ALLOWED_HOSTS if h and h != '*'), 'localhost')
            rf = RequestFactory(HTTP_HOST=host)

            def call(view, path, **kw):
                req = rf.get(path, secure=True)
                req.user = user
                req.session = SessionStore()
                req._messages = FallbackStorage(req)
                res = view.as_view()(req, **kw)
                return res.status_code, (res.content.decode() if hasattr(res, 'content') else '')
            st, html = call(CalendarView, '/reservations/', year=today.year, month=today.month)
            self.ok('予定のカレンダーが開く', st == 200 and 'の予定をまとめて作成' in html and '<span>予定</span>' in html,
                    f'ステータス {st}・まとめて作成 {"あり" if "の予定をまとめて作成" in html else "なし"}・メニュー「予定」 {"あり" if "<span>予定</span>" in html else "なし"}')
            st, html = call(AttendanceDayView, '/reservations/x/', year=day.year, month=day.month, day=day.day)
            self.ok('出欠入力が開く', st == 200 and '出欠入力（' in html, f'ステータス {st}・ラジオ {html.count("type=\"radio\"")} 個')

        bad = self.results.count(False)
        self.stdout.write(('すべて OK' if not bad else f'NG が {bad} 件あります') + f'（{len(self.results)} 項目）')
        if bad:
            raise SystemExit(1)

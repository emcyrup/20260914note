"""
ログインの記録と一時ロック（不正アクセスの検知の土台。2026-10-20）

- 記録：Django の user_logged_in / user_login_failed / user_logged_out の合図で LoginEvent を残す（どの入口のログインでも）
- ロック：同じユーザー名で WINDOW 分に USER_TRIES 回、または同じ IP で IP_TRIES 回失敗したら、LOCK 分のあいだ
  そのユーザー名・IP からのログインを受け付けない（正しいパスワードでも）。ロック中の試行も記録する
- 管理者は「職員・運用管理 → ログインの記録」で見られる。180 日より前の記録は消す
"""
import datetime
import logging

from django.conf import settings
from django.contrib.auth import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver
from django.utils import timezone

from .models import LoginEvent, SecurityAlert

logger = logging.getLogger(__name__)
NEW_IP_DAYS = 90        # この日数のあいだに同じ IP からログインしていなければ「初めての場所」
NIGHT_HOURS = range(0, 6)   # 深夜（日本時間）

WINDOW_MINUTES = 15     # 失敗を数える時間
USER_TRIES = 5          # 同じユーザー名の失敗の回数
IP_TRIES = 20           # 同じ IP の失敗の回数（複数の職員が同じ回線のことがあるので多め）
LOCK_MINUTES = 15       # ロックする時間
KEEP_DAYS = 180


def client_ip(request):
    if request is None:
        return ''
    return (request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip()
            or request.META.get('REMOTE_ADDR', '') or '')[:64]


def record(kind, request=None, username='', user=None):
    """ログインの出来事を 1 件残す"""
    facility = getattr(user, 'facility', None) if user is not None else None
    LoginEvent.objects.create(
        facility=facility, user=user if getattr(user, 'pk', None) else None, username=(username or '')[:150], kind=kind,
        ip=client_ip(request), user_agent=(request.META.get('HTTP_USER_AGENT', '') if request is not None else '')[:200])


def locked_until(username='', ip=''):
    """
    ロック中なら解ける時刻（aware datetime）、そうでなければ None。
    ユーザー名・IP それぞれで、直近 WINDOW 分の失敗が上限に達していたら、最後の失敗から LOCK 分
    """
    now = timezone.now()
    since = now - datetime.timedelta(minutes=WINDOW_MINUTES)
    out = None
    for field, value, limit in (('username', (username or '').strip(), USER_TRIES), ('ip', ip, IP_TRIES)):
        if not value:
            continue
        fails = list(LoginEvent.objects.filter(kind=LoginEvent.KIND_FAIL, created_at__gte=since, **{f'{field}__iexact' if field == 'username' else field: value})
                     .order_by('-created_at').values_list('created_at', flat=True)[:limit])
        if len(fails) >= limit:
            until = fails[0] + datetime.timedelta(minutes=LOCK_MINUTES)
            if until > now and (out is None or until > out):
                out = until
    return out


def cleanup():
    LoginEvent.objects.filter(created_at__lt=timezone.now() - datetime.timedelta(days=KEEP_DAYS)).delete()


@receiver(user_logged_in)
def _on_login(sender, request, user, **kwargs):
    record(LoginEvent.KIND_OK, request, user.get_username(), user)
    try:
        _check_after_login(request, user)
    except Exception:  # noqa: BLE001  通知の不具合でログインを止めない
        logger.exception('ログイン後の通知でエラー')


@receiver(user_login_failed)
def _on_fail(sender, credentials, request=None, **kwargs):
    username = str(credentials.get('username') or '')
    record(LoginEvent.KIND_FAIL, request, username)
    try:
        _check_after_fail(request, username)
    except Exception:  # noqa: BLE001
        logger.exception('ログイン失敗後の通知でエラー')


@receiver(user_logged_out)
def _on_logout(sender, request, user, **kwargs):
    if user is not None:
        record(LoginEvent.KIND_LOGOUT, request, user.get_username(), user)


# ---------------------------------------------------------------- 通知（2 段目）
def deliver(facility, text):
    """LINE グループ（予約の増減のお知らせ先）とメール（管理者・児発管、または施設設定の宛先）へ送る。(line_sent, mail_sent, error)"""
    line_sent = mail_sent = False
    errors = []
    try:
        from reservations.models import ReservationSetting
        setting = ReservationSetting.objects.filter(facility=facility).first()
        if setting is not None and setting.notify_group_id and facility.line_channel_access_token:
            from line_integration.sending import push_text
            line_sent, err = push_text(facility, setting.notify_group_id, text)
            if err:
                errors.append(f'LINE: {err}')
    except Exception as e:  # noqa: BLE001
        errors.append(f'LINE: {type(e).__name__}')
    try:
        if settings.EMAIL_HOST:
            from django.core.mail import send_mail
            from facilities.reminders import recipients
            to = [facility.security_notify_email] if facility.security_notify_email else recipients(facility)
            if to:
                send_mail(f'【{facility.name}】みちノートからの安全のお知らせ', text, settings.DEFAULT_FROM_EMAIL, to, fail_silently=False)
                mail_sent = True
    except Exception as e:  # noqa: BLE001
        errors.append(f'メール: {type(e).__name__}')
    return line_sent, mail_sent, '；'.join(errors)[:200]


def alert(facility, kind, text, actor=None):
    """
    不審なことを事業所に知らせる（施設設定の「不正アクセスの通知を送る」がオンのとき）。記録は常に残す。
    本文の先頭に事業所名と日時。送れなくても処理は止めない
    """
    if facility is None:
        return None
    when = timezone.localtime().strftime('%-m/%-d %H:%M')
    body = f'【みちノート 安全のお知らせ】{when}\n{text}\n心当たりが無ければ、職員・運用管理の「ログインの記録」を確かめ、パスワードの変更を検討してください。'
    row = SecurityAlert(facility=facility, kind=kind, text=text[:500], actor=actor if getattr(actor, 'pk', None) else None)
    if getattr(facility, 'security_notify', True):
        try:
            row.line_sent, row.mail_sent, row.error = deliver(facility, body)
        except Exception as e:  # noqa: BLE001
            row.error = type(e).__name__
    row.save()
    return row


def admin_action(request, text):
    """管理者の操作（職員の追加・権限の変更・利用者の削除・予定表のリセットなど）を知らせる"""
    user = getattr(request, 'user', None)
    facility = getattr(user, 'facility', None)
    who = (getattr(user, 'display_name', '') or getattr(user, 'username', '') or '不明')
    return alert(facility, SecurityAlert.KIND_ADMIN, f'{who} さんが {text}（IP {client_ip(request) or "不明"}）', actor=user)


def _check_after_fail(request, username):
    """失敗を記録したあと：この失敗で上限に達したらロックを知らせる（ユーザー名が職員のものなら、その事業所へ）"""
    from .models import StaffAccount
    ip = client_ip(request)
    now = timezone.now()
    since = now - datetime.timedelta(minutes=WINDOW_MINUTES)
    user = StaffAccount.objects.filter(username__iexact=(username or '').strip()).select_related('facility').first() if username else None
    user_fails = LoginEvent.objects.filter(kind=LoginEvent.KIND_FAIL, created_at__gte=since, username__iexact=username).count() if username else 0
    ip_fails = LoginEvent.objects.filter(kind=LoginEvent.KIND_FAIL, created_at__gte=since, ip=ip).count() if ip else 0
    if user is not None and user.facility_id and user_fails == USER_TRIES:
        alert(user.facility, SecurityAlert.KIND_LOCK,
              f'ユーザー名「{username}」でログインの失敗が {USER_TRIES} 回続いたため、{LOCK_MINUTES} 分ロックしました（IP {ip or "不明"}）。')
    elif ip_fails == IP_TRIES:
        # 同じ IP からの失敗がかさんだ：その IP で失敗したユーザー名の事業所すべてに知らせる
        names = set(LoginEvent.objects.filter(kind=LoginEvent.KIND_FAIL, created_at__gte=since, ip=ip).values_list('username', flat=True))
        facilities = {u.facility for u in StaffAccount.objects.filter(username__in=names, facility__isnull=False).select_related('facility')}
        for f in facilities:
            alert(f, SecurityAlert.KIND_LOCK, f'IP {ip} からログインの失敗が {IP_TRIES} 回続いたため、この IP を {LOCK_MINUTES} 分ロックしました。')


def _check_after_login(request, user):
    """ログインのあと：初めての場所（IP）・深夜のログインを知らせる"""
    facility = getattr(user, 'facility', None)
    if facility is None or request is None:
        return
    ip = client_ip(request)
    now = timezone.now()
    earlier = LoginEvent.objects.filter(user=user, kind=LoginEvent.KIND_OK).exclude(pk=LoginEvent.objects.filter(user=user, kind=LoginEvent.KIND_OK).order_by('-pk').values_list('pk', flat=True)[:1])
    if ip and earlier.exists() and not earlier.filter(ip=ip, created_at__gte=now - datetime.timedelta(days=NEW_IP_DAYS)).exists():
        alert(facility, SecurityAlert.KIND_NEW_IP, f'{user.display_name or user.username} さんが初めての場所（IP {ip}）からログインしました。')
    hour = timezone.localtime(now).hour
    if hour in NIGHT_HOURS:
        alert(facility, SecurityAlert.KIND_NIGHT, f'{user.display_name or user.username} さんが深夜（{hour} 時台）にログインしました（IP {ip or "不明"}）。')

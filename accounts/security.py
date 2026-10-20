"""
ログインの記録と一時ロック（不正アクセスの検知の土台。2026-10-20）

- 記録：Django の user_logged_in / user_login_failed / user_logged_out の合図で LoginEvent を残す（どの入口のログインでも）
- ロック：同じユーザー名で WINDOW 分に USER_TRIES 回、または同じ IP で IP_TRIES 回失敗したら、LOCK 分のあいだ
  そのユーザー名・IP からのログインを受け付けない（正しいパスワードでも）。ロック中の試行も記録する
- 管理者は「職員・運用管理 → ログインの記録」で見られる。180 日より前の記録は消す
"""
import datetime

from django.contrib.auth import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver
from django.utils import timezone

from .models import LoginEvent

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


@receiver(user_login_failed)
def _on_fail(sender, credentials, request=None, **kwargs):
    record(LoginEvent.KIND_FAIL, request, str(credentials.get('username') or ''))


@receiver(user_logged_out)
def _on_logout(sender, request, user, **kwargs):
    if user is not None:
        record(LoginEvent.KIND_LOGOUT, request, user.get_username(), user)

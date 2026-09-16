"""ビュー共通の小さな入力検証"""
import datetime

from django.http import Http404
from django.utils.http import url_has_allowed_host_and_scheme


def to_int(value, default=None):
    """'12' → 12。数字でなければ default"""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def month_or_404(year, month):
    """URL の年月が正しい範囲か（1〜12 月・1〜9999 年）。外れていれば 404"""
    if not (1 <= int(month) <= 12 and 1 <= int(year) <= 9999):
        raise Http404('対象月が正しくありません')
    return int(year), int(month)


def date_or_404(year, month, day):
    try:
        return datetime.date(int(year), int(month), int(day))
    except (TypeError, ValueError):
        raise Http404('日付が正しくありません')


def safe_next(request, nxt, fallback):
    """フォームの next は同じホストの相対 URL だけ許す（外部サイトへのリダイレクトを防ぐ）"""
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return nxt
    return fallback


def home_url():
    """
    ログイン後の入口。
    予約管理だけを動かすサーバー（RESERVATION_ONLY）では予約カレンダーが入口になる。
    """
    from django.conf import settings
    from django.urls import reverse
    return reverse('reservations:calendar' if settings.RESERVATION_ONLY else 'facilities:dashboard')


def reservation_enabled(facility):
    """
    予約管理を使う事業所か。
    予約管理だけを動かすサーバー（RESERVATION_ONLY）では、施設設定によらず使う。
    """
    from django.conf import settings
    return bool(settings.RESERVATION_ONLY or (facility is not None and facility.use_reservation))

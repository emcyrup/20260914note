"""予約の通知を公式LINEへ送る（送信待ちに積まれたものを職員の操作でまとめて送る）"""
import logging

from django.utils import timezone

logger = logging.getLogger(__name__)


def push_text(facility, to_line_id, text):
    """1件送る。送れたら (True, '')、送れなければ (False, 理由)"""
    if not facility.line_channel_access_token:
        return False, '施設設定にチャネルアクセストークンがありません'
    if not to_line_id:
        return False, '送信先のLINE IDがありません'
    try:
        from linebot.v3.messaging import (
            ApiClient, Configuration, MessagingApi, PushMessageRequest, TextMessage,
        )
        config = Configuration(access_token=facility.line_channel_access_token)
        with ApiClient(config) as api_client:
            MessagingApi(api_client).push_message(PushMessageRequest(
                to=to_line_id, messages=[TextMessage(type='text', text=text)],
            ))
        return True, ''
    except Exception as e:  # noqa: BLE001
        logger.error('LINE送信エラー（予約の通知・施設 %s）: %s', facility.pk, e)
        return False, str(e)


def send_reservation_notices(facility, limit=50):
    """送信待ちの通知を順に送る。LINE未連携ぶん（手渡し）はそのまま残す"""
    from reservations.models import ReservationNotice
    rows = ReservationNotice.objects.filter(
        facility=facility, status=ReservationNotice.STATUS_PENDING
    ).order_by('created_at')[:limit]
    sent = failed = 0
    for notice in rows:
        ok, error = push_text(facility, notice.to_line_id, notice.body)
        notice.status = ReservationNotice.STATUS_SENT if ok else ReservationNotice.STATUS_FAILED
        notice.error_message = error
        notice.sent_at = timezone.now()
        notice.save(update_fields=['status', 'error_message', 'sent_at'])
        sent, failed = (sent + 1, failed) if ok else (sent, failed + 1)
    return sent, failed

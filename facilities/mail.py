"""
事業所からのメール送信（不正アクセスの通知・期限のお知らせ）。

送信元の決め方：
1. 施設設定の「送信用の Gmail アドレス」と「アプリ パスワード」があれば、その Gmail（smtp.gmail.com:587・TLS）で送る
2. 無ければ、サーバーの .env の EMAIL_HOST 等（settings）で送る
3. どちらも無ければ送れない（available() が False）
"""
from django.conf import settings
from django.core.mail import EmailMessage, get_connection

GMAIL_HOST, GMAIL_PORT = 'smtp.gmail.com', 587
SMTP_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'


def facility_gmail(facility):
    return bool(facility is not None and facility.mail_from and facility.mail_app_password)


def available(facility):
    """この事業所からメールを送れるか"""
    return facility_gmail(facility) or bool(settings.EMAIL_HOST)


def source_label(facility):
    if facility_gmail(facility):
        return f'施設設定の Gmail（{facility.mail_from}）'
    if settings.EMAIL_HOST:
        return f'サーバーの設定（{settings.DEFAULT_FROM_EMAIL}）'
    return ''


def send(facility, subject, body, to):
    """1 通送る（宛先が無ければ False）。接続や認証の失敗は例外のまま（呼ぶ側で知らせる）"""
    if not to:
        return False
    if facility_gmail(facility):
        conn = get_connection(backend=SMTP_BACKEND, host=GMAIL_HOST, port=GMAIL_PORT, username=facility.mail_from,
                              password=facility.mail_app_password, use_tls=True, timeout=20)
        EmailMessage(subject, body, facility.mail_from, list(to), connection=conn).send(fail_silently=False)
        return True
    # サーバーの設定（EMAIL_HOST が無い環境は console／テスト用の送り先になる。available() で先に確かめる）
    EmailMessage(subject, body, settings.DEFAULT_FROM_EMAIL, list(to)).send(fail_silently=False)
    return True

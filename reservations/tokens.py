"""
顧客向けページのアドレス（合い言葉）。

アドレスには **署名（チェックサム）** を付ける。
- 1字でも違えば署名が合わず、DB を引く前にはじく（打ち間違い・書き換えは必ず 404）
- 空き状況ページと顧客ページでは署名の種類（salt）を変える。
  片方のアドレスをもう片方の URL に貼っても通らない
- 署名は Django の `Signer`（SECRET_KEY による HMAC-SHA256、照合は定数時間）

注意：`SECRET_KEY` を変えると、配ったアドレスはすべて使えなくなる。
古い鍵を `SECRET_KEY_FALLBACKS` に残すか、`reissue_reservation_tokens` で作り直して配り直す。
"""
import secrets

from django.core import signing

# 署名の種類。ページごとに変える（片方のアドレスがもう片方で通らないように）
CALENDAR = 'reservations.public-calendar'
CUSTOMER = 'reservations.customer-page'

BODY_BYTES = 18      # 合い言葉の長さ（24文字ぶん）
MAX_LENGTH = 128     # これより長い入力は署名を確かめるまでもなくはじく


def _signer(kind):
    return signing.Signer(salt=kind)


def make_token(kind):
    """新しいアドレスを1つ作る（合い言葉＋署名）"""
    return _signer(kind).sign(secrets.token_urlsafe(BODY_BYTES))


def is_valid(kind, token):
    """このページ用の、書き換えられていないアドレスか"""
    if not token or len(token) > MAX_LENGTH:
        return False
    try:
        _signer(kind).unsign(token)
    except signing.BadSignature:
        return False
    return True


def new_calendar_token():
    return make_token(CALENDAR)


def new_customer_token():
    return make_token(CUSTOMER)

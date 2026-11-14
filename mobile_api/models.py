"""
職員アプリ（iOS・Android）のログインの合い言葉（トークン）。

アプリはユーザー名とパスワードで1回ログインし、以後は端末に保存したトークンを
Authorization: Bearer … で送る。サーバーにはトークンそのものではなく SHA-256 だけを残す。
"""
import datetime
import hashlib
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone

TOKEN_DAYS = 90          # 最後に使ってからこの日数で無効になる
TOKENS_PER_USER = 10     # 1人が同時にログインしておける端末の数（古いものから消す）


def hash_key(key):
    return hashlib.sha256(key.encode()).hexdigest()


class MobileToken(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='mobile_tokens',
                             verbose_name='職員')
    key_hash = models.CharField(max_length=64, unique=True, editable=False)
    device = models.CharField(max_length=100, blank=True, verbose_name='端末')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='ログインした日時')
    last_used_at = models.DateTimeField(default=timezone.now, verbose_name='最後に使った日時')

    class Meta:
        verbose_name = '職員アプリのログイン'
        verbose_name_plural = '職員アプリのログイン'
        ordering = ['-last_used_at']

    def __str__(self):
        return f'{self.user} / {self.device or "端末"}'

    @classmethod
    def issue(cls, user, device=''):
        """新しいトークンを作り、(行, トークンの文字列) を返す。文字列を渡せるのはこのときだけ"""
        key = secrets.token_urlsafe(40)
        token = cls.objects.create(user=user, key_hash=hash_key(key), device=(device or '')[:100])
        old = list(cls.objects.filter(user=user).order_by('-last_used_at', '-pk')
                   .values_list('pk', flat=True)[TOKENS_PER_USER:])
        if old:
            cls.objects.filter(pk__in=old).delete()
        return token, key

    @classmethod
    def find(cls, key):
        """トークンの文字列から、まだ使える行を返す（無ければ None）"""
        if not key:
            return None
        token = cls.objects.select_related('user', 'user__facility').filter(key_hash=hash_key(key)).first()
        if token is None:
            return None
        if token.last_used_at < timezone.now() - datetime.timedelta(days=TOKEN_DAYS):
            token.delete()
            return None
        return token

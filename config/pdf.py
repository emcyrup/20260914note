"""WeasyPrint で /media/ の画像を参照するとき、HTTP 経由ではなくサーバー内のファイルを読む（配信にはログインが要るため）"""
from pathlib import Path
from urllib.parse import unquote, urlsplit

from django.conf import settings


def media_url_fetcher(url, *args, **kwargs):
    from weasyprint import default_url_fetcher
    parsed = urlsplit(url)
    if parsed.path.startswith(settings.MEDIA_URL):
        rel = unquote(parsed.path[len(settings.MEDIA_URL):])
        target = (Path(settings.MEDIA_ROOT) / rel).resolve()
        if str(target).startswith(str(Path(settings.MEDIA_ROOT).resolve())) and target.is_file():
            return default_url_fetcher(target.as_uri(), *args, **kwargs)
    return default_url_fetcher(url, *args, **kwargs)

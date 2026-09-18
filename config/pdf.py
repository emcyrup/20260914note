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


def pdf_or_html(request, template, ctx, filename):
    """?fmt=html なら画面表示（印刷確認）、それ以外は WeasyPrint で PDF"""
    import urllib.parse
    from django.http import HttpResponse
    from django.shortcuts import render

    ctx = dict(ctx, pdf=request.GET.get('fmt') != 'html')
    html = render(request, template, ctx).content.decode('utf-8')
    if not ctx['pdf']:
        return HttpResponse(html)
    try:
        from weasyprint import HTML
        pdf = HTML(string=html, base_url=request.build_absolute_uri('/'), url_fetcher=media_url_fetcher).write_pdf()
    except (ImportError, OSError) as e:
        return HttpResponse(f'PDF を作成できません（サーバーに PDF 用ライブラリがありません）: {e}\n「画面で見る」から印刷してください。',
                            status=500, content_type='text/plain; charset=utf-8')
    res = HttpResponse(pdf, content_type='application/pdf')
    res['Content-Disposition'] = f'attachment; filename="form.pdf"; filename*=UTF-8\'\'{urllib.parse.quote(filename)}.pdf'
    return res

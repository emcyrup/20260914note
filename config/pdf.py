"""
WeasyPrint で PDF を作るときの共通部品。
- /media/ の画像は HTTP 経由ではなくサーバー内のファイルを読む（配信にはログインが要るため）
- 日本語フォント（static/fonts/ipagp.ttf・IPAPゴシック）をアプリに同梱し、サーバーに日本語フォントが無くても
  文字化け（豆腐）しないようにする。すべての PDF は render_pdf() を通す
"""
import logging
from pathlib import Path
from urllib.parse import unquote, urlsplit

from django.conf import settings

logger = logging.getLogger(__name__)

FONT_FILE = 'fonts/ipagp.ttf'      # static の中の同梱フォント（IPAPゴシック。IPA フォントライセンス v1.0）
FONT_NAME = 'IPAPGothic'


def font_path():
    """同梱フォントのファイル（開発中は static/、配備後は collectstatic 先）。無ければ None"""
    try:
        from django.contrib.staticfiles import finders
        found = finders.find(FONT_FILE)
        if found:
            return Path(found)
    except Exception:  # noqa: BLE001  静的ファイルの設定が無いときは下へ
        pass
    for base in (getattr(settings, 'STATIC_ROOT', ''), Path(settings.BASE_DIR) / 'static'):
        if base and (Path(base) / FONT_FILE).is_file():
            return Path(base) / FONT_FILE
    return None


def font_css():
    """同梱フォントを最優先で使う CSS。フォントが見つからなければ空（サーバーのフォントに任せる）"""
    path = font_path()
    if path is None:
        logger.warning('PDF: 同梱フォント %s が見つかりません。サーバーのフォントで作ります', FONT_FILE)
        return ''
    return (f'@font-face {{ font-family: "{FONT_NAME}"; src: url("{path.resolve().as_uri()}"); }}\n'
            f'html, body, * {{ font-family: "{FONT_NAME}", sans-serif !important; }}')


def render_pdf(html, base_url=None, url_fetcher=None):
    """HTML → PDF のバイト列。同梱の日本語フォントを使う。WeasyPrint が無い・共有ライブラリが無いときは ImportError / OSError"""
    from weasyprint import CSS, HTML
    from weasyprint.text.fonts import FontConfiguration
    font_config = FontConfiguration()
    kwargs = {'string': html}
    if base_url:
        kwargs['base_url'] = base_url
    kwargs['url_fetcher'] = url_fetcher or media_url_fetcher()
    css = font_css()
    sheets = [CSS(string=css, font_config=font_config, url_fetcher=kwargs['url_fetcher'])] if css else []
    return HTML(**kwargs).write_pdf(stylesheets=sheets, font_config=font_config)


def _allowed_file_roots():
    """PDF が読んでよいサーバー内のフォルダ（アップロードした画像・静的ファイル・同梱フォント）"""
    roots = [Path(settings.MEDIA_ROOT), Path(settings.BASE_DIR) / 'static']
    if getattr(settings, 'STATIC_ROOT', ''):
        roots.append(Path(settings.STATIC_ROOT))
    return [r.resolve() for r in roots]


def _inside(target, roots):
    return any(target == r or r in target.parents for r in roots)


def media_url_fetcher():
    """
    PDF の画像・フォントを読む URL フェッチャ（WeasyPrint 70 からは URLFetcher のサブクラスを渡す）。
    - /media/ の URL はログイン無しで読めるよう、サーバー内のファイルを直接読む（MEDIA_ROOT の外へは出ない）
    - file:// はアップロード・静的ファイル・同梱フォントのフォルダの中だけ（ほかのサーバー内のファイルは読まない）
    - 読める方式は file・data・http・https だけ
    """
    from weasyprint.urls import URLFetcher

    class MediaURLFetcher(URLFetcher):
        def fetch(self, url, headers=None):
            parsed = urlsplit(url)
            roots = _allowed_file_roots()
            if parsed.scheme in ('http', 'https') and parsed.path.startswith(settings.MEDIA_URL):
                rel = unquote(parsed.path[len(settings.MEDIA_URL):])
                target = (Path(settings.MEDIA_ROOT) / rel).resolve()
                if _inside(target, [Path(settings.MEDIA_ROOT).resolve()]) and target.is_file():
                    return super().fetch(target.as_uri(), headers)
            if parsed.scheme == 'file':
                target = Path(unquote(parsed.path)).resolve()
                if not _inside(target, roots):
                    raise ValueError(f'PDF: 読めない場所のファイルです: {url}')
            return super().fetch(url, headers)

    return MediaURLFetcher(allowed_protocols=('file', 'data', 'http', 'https'))


def pdf_or_html(request, template, ctx, filename):
    """?fmt=html なら画面表示（印刷確認）、それ以外は WeasyPrint で PDF"""
    import urllib.parse
    from django.http import HttpResponse
    from django.shortcuts import render

    ctx = dict(ctx, pdf=request.GET.get('fmt') != 'html')
    params = request.GET.copy()
    for k in ('fmt', 'inline'):
        params.pop(k, None)
    ctx['pdf_url'] = request.path + ('?' + params.urlencode() if params else '')
    params['inline'] = '1'
    ctx['pdf_print_url'] = request.path + '?' + params.urlencode()
    html = render(request, template, ctx).content.decode('utf-8')
    if not ctx['pdf']:
        return HttpResponse(html)
    try:
        pdf = render_pdf(html, base_url=request.build_absolute_uri('/'))
    except (ImportError, OSError) as e:
        return HttpResponse(f'PDF を作成できません（サーバーに PDF 用ライブラリがありません）: {e}\n「画面で見る」から印刷してください。',
                            status=500, content_type='text/plain; charset=utf-8')
    res = HttpResponse(pdf, content_type='application/pdf')
    how = 'inline' if request.GET.get('inline') == '1' else 'attachment'      # inline：ブラウザで開いてそのまま印刷
    res['Content-Disposition'] = f'{how}; filename="form.pdf"; filename*=UTF-8\'\'{urllib.parse.quote(filename)}.pdf'
    return res

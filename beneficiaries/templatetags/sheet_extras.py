"""オウルの用紙（フェイスシート・アセスメントシート）の画面・PDF で、JSON の値を取り出すフィルタ"""
from django import template

register = template.Library()


@register.filter
def dictkey(d, key):
    """辞書から key の値（無ければ空文字）"""
    if not isinstance(d, dict):
        return ''
    v = d.get(key)
    return '' if v is None else v


@register.filter
def rowcell(rows, i):
    """リストの i 番目（無ければ空の辞書）"""
    try:
        return rows[int(i)] if rows and int(i) < len(rows) else {}
    except (TypeError, ValueError):
        return {}


@register.filter
def level_word(levels, level):
    """[(1, '出来る'), ...] から level の言い方"""
    try:
        return dict(levels).get(int(level), '')
    except (TypeError, ValueError):
        return ''

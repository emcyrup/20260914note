from django import template

register = template.Library()


@register.filter
def dictkey(value, key):
    """辞書（JSON の追加項目）からキーの値を取り出す。辞書でなければ空文字"""
    if isinstance(value, dict):
        return value.get(key, '') or ''
    return ''


@register.filter
def has(value, item):
    """リストに含まれていれば True（様式のチェック表示用）"""
    try:
        return item in (value or [])
    except TypeError:
        return False

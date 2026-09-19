from django import template

register = template.Library()


@register.filter
def get_item(d, key):
    """dict[key]（テンプレートで変数のキーを引く）"""
    try:
        return d.get(key)
    except AttributeError:
        return None


@register.filter
def sub(a, b):
    try:
        return int(a) - int(b)
    except (TypeError, ValueError):
        return ''

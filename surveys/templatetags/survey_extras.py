from django import template

register = template.Library()


@register.filter
def get_item(d, key):
    """辞書から取り出す（{{ row.counts|get_item:k }}）。鍵は文字列でも数でもよい"""
    try:
        if key in d:
            return d[key]
        return d.get(str(key), 0)
    except (AttributeError, TypeError):
        return 0

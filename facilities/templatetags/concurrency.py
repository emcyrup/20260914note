from django import template
from django.utils.html import format_html

from config.concurrency import VERSION_FIELD, version_token

register = template.Library()


@register.simple_tag
def version_field(obj):
    """編集フォームに開いた時点の版を埋める（保存時に他の職員の保存と競合していないか確かめる）"""
    return format_html('<input type="hidden" name="{}" value="{}">', VERSION_FIELD, version_token(obj))

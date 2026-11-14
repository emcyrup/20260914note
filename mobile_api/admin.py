from django.contrib import admin

from .models import MobileToken


@admin.register(MobileToken)
class MobileTokenAdmin(admin.ModelAdmin):
    """端末をなくしたときに、その端末のログインを消せるようにする（追加はアプリからだけ）"""
    list_display = ('user', 'device', 'created_at', 'last_used_at')
    search_fields = ('user__username', 'user__display_name', 'device')
    readonly_fields = ('user', 'device', 'created_at', 'last_used_at')

    def has_add_permission(self, request):
        return False

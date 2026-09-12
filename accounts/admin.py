from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import StaffAccount


@admin.register(StaffAccount)
class StaffAccountAdmin(UserAdmin):
    """職員アカウントの管理画面。標準の UserAdmin に所属施設・権限区分・表示名を追加する。"""

    list_display = ['username', 'display_name', 'facility', 'role', 'is_active', 'is_staff']
    list_filter = ['facility', 'role', 'is_active', 'is_staff', 'is_superuser']
    search_fields = ['username', 'display_name', 'email', 'first_name', 'last_name']
    ordering = ['facility', 'username']

    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        ('施設・権限', {'fields': ('facility', 'role', 'display_name')}),
        ('個人情報', {'fields': ('first_name', 'last_name', 'email')}),
        ('権限', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('重要な日付', {'fields': ('last_login', 'date_joined')}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('username', 'password1', 'password2', 'facility', 'role', 'display_name'),
        }),
    )

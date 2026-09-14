from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from facilities.admin_mixins import FacilityScopedAdmin, FacilityScopedMixin

from .models import StaffAccount, StaffInvitation


@admin.register(StaffAccount)
class StaffAccountAdmin(FacilityScopedMixin, UserAdmin):
    """職員アカウントの管理画面。標準の UserAdmin に所属施設・権限区分・表示名を追加する。"""

    list_display = ['username', 'display_name', 'facility', 'role', 'is_developer', 'ui_theme', 'is_active', 'is_staff']
    list_filter = ['facility', 'role', 'is_developer', 'ui_theme', 'is_active', 'is_staff', 'is_superuser']
    search_fields = ['username', 'display_name', 'email', 'first_name', 'last_name']
    ordering = ['facility', 'username']

    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        ('施設・権限', {'fields': ('facility', 'role', 'is_developer', 'display_name', 'ui_theme', 'ui_prefs')}),
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
    # スーパーユーザー以外は、所属・開発向け・スーパーユーザー・管理画面アクセス・権限を変えられない
    PROTECTED = ('facility', 'is_developer', 'is_superuser', 'is_staff', 'groups', 'user_permissions')

    def get_readonly_fields(self, request, obj=None):
        ro = list(super().get_readonly_fields(request, obj))
        if not request.user.is_superuser:
            ro += [f for f in self.PROTECTED if f not in ro]
        return ro

    def save_model(self, request, obj, form, change):
        if not request.user.is_superuser and not change:
            obj.facility = request.user.facility
            obj.is_developer = obj.is_superuser = obj.is_staff = False
        super().save_model(request, obj, form, change)


@admin.register(StaffInvitation)
class StaffInvitationAdmin(FacilityScopedAdmin):
    list_display = ['facility', 'role', 'note', 'status_label', 'used_count', 'max_uses', 'expires_at', 'created_by', 'created_at']
    list_filter = ['facility', 'role', 'is_active']
    readonly_fields = ['token', 'used_count', 'created_at']

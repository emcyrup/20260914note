"""
Django 管理画面（/admin/）を事業所で絞る。

スーパーユーザーはすべて見られる。それ以外（is_staff の職員）は自分の事業所の行だけを見て編集できる。
"""
from django.contrib import admin


class FacilityScopedMixin:
    facility_lookup = 'facility'   # 'pk'（施設そのもの）/ 'facility' / 'plan__facility' など

    def _facility_pk(self, request):
        f = getattr(request.user, 'facility', None)
        return f.pk if f is not None else None

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        fpk = self._facility_pk(request)
        if fpk is None:
            return qs.none()
        return qs.filter(**{self.facility_lookup: fpk})

    def get_list_filter(self, request):
        lf = list(super().get_list_filter(request))
        if not request.user.is_superuser:
            lf = [f for f in lf if f != 'facility']
        return lf

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if not request.user.is_superuser and db_field.name == 'facility':
            from .models import Facility
            kwargs['queryset'] = Facility.objects.filter(pk=self._facility_pk(request) or -1)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class FacilityScopedAdmin(FacilityScopedMixin, admin.ModelAdmin):
    pass


class SuperuserOnlyWriteMixin:
    """全事業所共通のマスタ：スーパーユーザー以外は読むだけ"""

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

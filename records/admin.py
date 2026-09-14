from django.contrib import admin
from facilities.admin_mixins import FacilityScopedAdmin

from .models import ActivityTag, DailyRecord


@admin.register(ActivityTag)
class ActivityTagAdmin(FacilityScopedAdmin):
    list_display = ['name', 'facility', 'display_order', 'is_active', 'price']
    list_filter = ['facility', 'is_active']
    list_editable = ['display_order', 'is_active', 'price']
    search_fields = ['name']
    ordering = ['facility', 'display_order', 'name']


@admin.register(DailyRecord)
class DailyRecordAdmin(FacilityScopedAdmin):
    list_display = ['date', 'beneficiary', 'health_condition', 'status', 'author']
    list_filter = ['facility', 'status', 'health_condition']
    search_fields = ['beneficiary__last_name', 'beneficiary__first_name']
    date_hierarchy = 'date'

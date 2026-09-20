from django.contrib import admin

from facilities.admin_mixins import FacilityScopedAdmin

from .models import TherapyProfile, TherapyRecord


@admin.register(TherapyRecord)
class TherapyRecordAdmin(FacilityScopedAdmin):
    list_display = ('date', 'time', 'beneficiary', 'staff_label')
    list_filter = ('facility',)
    search_fields = ('beneficiary__last_name', 'beneficiary__first_name', 'body')


@admin.register(TherapyProfile)
class TherapyProfileAdmin(admin.ModelAdmin):
    list_display = ('beneficiary',)

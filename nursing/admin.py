from django.contrib import admin

from .models import CarePlan, CareRecord, IncidentReport, Medication, NursingSetting, StaffingDay, VitalsLog


@admin.register(VitalsLog)
class VitalsLogAdmin(admin.ModelAdmin):
    list_display = ('date', 'slot', 'beneficiary', 'temp', 'bp_high', 'bp_low', 'pulse', 'spo2')
    list_filter = ('facility', 'slot')
    date_hierarchy = 'date'


@admin.register(CareRecord)
class CareRecordAdmin(admin.ModelAdmin):
    list_display = ('date', 'beneficiary', 'status', 'author', 'approved_by')
    list_filter = ('facility', 'status')
    date_hierarchy = 'date'


@admin.register(IncidentReport)
class IncidentReportAdmin(admin.ModelAdmin):
    list_display = ('occurred_at', 'kind', 'category', 'beneficiary', 'status')
    list_filter = ('facility', 'kind', 'status')


@admin.register(Medication)
class MedicationAdmin(admin.ModelAdmin):
    list_display = ('beneficiary', 'name', 'dose', 'review_due', 'is_active')
    list_filter = ('facility', 'is_active')


@admin.register(CarePlan)
class CarePlanAdmin(admin.ModelAdmin):
    list_display = ('beneficiary', 'created_on', 'review_due')
    list_filter = ('facility',)


admin.site.register(StaffingDay)
admin.site.register(NursingSetting)

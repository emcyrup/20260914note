from django.contrib import admin

from .models import (
    Assessment, ConsentDelivery, Monitoring, MonitoringRecord, PlanDraft, PlanGoal, StaffMeeting, SupportPlan,
)


class GoalInline(admin.TabularInline):
    model = PlanGoal
    extra = 0


class MonitoringRecordInline(admin.TabularInline):
    model = MonitoringRecord
    extra = 0
    fields = ['date', 'achievement', 'review_needed', 'next_due']


@admin.register(SupportPlan)
class SupportPlanAdmin(admin.ModelAdmin):
    list_display = ['beneficiary', 'title', 'current_step', 'status', 'facility', 'updated_at']
    list_filter = ['facility', 'status', 'current_step']
    search_fields = ['title', 'beneficiary__last_name', 'beneficiary__first_name']
    inlines = [GoalInline, MonitoringRecordInline]


for m in (Assessment, PlanDraft, StaffMeeting, ConsentDelivery, Monitoring):
    admin.site.register(m)

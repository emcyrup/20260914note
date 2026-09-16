from django.contrib import admin

from facilities.admin_mixins import FacilityScopedAdmin

from .models import ClosedDate, Customer, LineInbox, Reservation, ReservationNotice, ReservationSetting


@admin.register(ReservationSetting)
class ReservationSettingAdmin(FacilityScopedAdmin):
    list_display = ['facility', 'capacity', 'allow_waitlist', 'auto_send',
                    'public_calendar', 'public_booking', 'line_auto_apply', 'group_auto_apply']


@admin.register(ClosedDate)
class ClosedDateAdmin(FacilityScopedAdmin):
    list_display = ['facility', 'date', 'reason']
    list_filter = ['facility']


@admin.register(Customer)
class CustomerAdmin(FacilityScopedAdmin):
    list_display = ['name', 'facility', 'phone', 'notify_enabled']
    list_filter = ['facility', 'notify_enabled']
    search_fields = ['name', 'kana', 'phone']


@admin.register(Reservation)
class ReservationAdmin(FacilityScopedAdmin):
    list_display = ['date', 'beneficiary', 'status', 'source', 'facility']
    list_filter = ['facility', 'status', 'source']
    date_hierarchy = 'date'


@admin.register(ReservationNotice)
class ReservationNoticeAdmin(FacilityScopedAdmin):
    list_display = ['kind', 'date', 'customer', 'status', 'created_at', 'facility']
    list_filter = ['facility', 'kind', 'status']


@admin.register(LineInbox)
class LineInboxAdmin(FacilityScopedAdmin):
    list_display = ['received_at', 'source_type', 'display_name', 'status', 'facility']
    list_filter = ['facility', 'source_type', 'status']

from django.contrib import admin

from facilities.admin_mixins import FacilityScopedAdmin

from .models import Minutes


@admin.register(Minutes)
class MinutesAdmin(FacilityScopedAdmin):
    list_display = ('held_on', 'title', 'facility', 'created_at')
    list_filter = ('facility',)
    search_fields = ('title', 'summary', 'transcript')

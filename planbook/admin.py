from django.contrib import admin

from .models import ContactNote, Interview


@admin.register(Interview)
class InterviewAdmin(admin.ModelAdmin):
    list_display = ('plan', 'interview_date', 'period_start', 'period_end', 'completed_at')
    raw_id_fields = ('plan',)


@admin.register(ContactNote)
class ContactNoteAdmin(admin.ModelAdmin):
    list_display = ('guardian', 'sender', 'created_at', 'line_sent', 'is_read')
    list_filter = ('sender', 'line_sent')
    raw_id_fields = ('guardian',)

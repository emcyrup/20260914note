from django.contrib import admin

from facilities.admin_mixins import FacilityScopedAdmin

from .models import AddonSuggestion, DocumentChunk, ReferenceDocument


@admin.register(ReferenceDocument)
class ReferenceDocumentAdmin(FacilityScopedAdmin):
    list_display = ['title', 'facility', 'page_count', 'chunk_count', 'indexed_at', 'uploaded_at']
    list_filter = ['facility']


@admin.register(DocumentChunk)
class DocumentChunkAdmin(FacilityScopedAdmin):
    facility_lookup = 'document__facility'
    list_display = ['document', 'index', 'page', 'length']
    list_filter = ['document']


@admin.register(AddonSuggestion)
class AddonSuggestionAdmin(FacilityScopedAdmin):
    list_display = ['daily_record', 'addon', 'status', 'created_at', 'decided_by']
    list_filter = ['facility', 'status']

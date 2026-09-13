from django.contrib import admin

from .models import AddonSuggestion, DocumentChunk, ReferenceDocument


@admin.register(ReferenceDocument)
class ReferenceDocumentAdmin(admin.ModelAdmin):
    list_display = ['title', 'facility', 'page_count', 'chunk_count', 'indexed_at', 'uploaded_at']
    list_filter = ['facility']


@admin.register(DocumentChunk)
class DocumentChunkAdmin(admin.ModelAdmin):
    list_display = ['document', 'index', 'page', 'length']
    list_filter = ['document']


@admin.register(AddonSuggestion)
class AddonSuggestionAdmin(admin.ModelAdmin):
    list_display = ['daily_record', 'addon', 'status', 'created_at', 'decided_by']
    list_filter = ['facility', 'status']

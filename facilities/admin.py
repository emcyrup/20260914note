from django.contrib import admin
from .models import AddonMaster, Facility, FacilityAddonSetting, SupportContentTag


@admin.register(Facility)
class FacilityAdmin(admin.ModelAdmin):
    list_display = ['name', 'office_number', 'phone', 'region_category', 'standard_close_time']
    search_fields = ['name', 'office_number']


@admin.register(SupportContentTag)
class SupportContentTagAdmin(admin.ModelAdmin):
    list_display = ['name', 'facility', 'order', 'is_active']
    list_filter = ['facility', 'is_active']
    list_editable = ['order', 'is_active']
    search_fields = ['name']


@admin.register(AddonMaster)
class AddonMasterAdmin(admin.ModelAdmin):
    list_display = ['name', 'addon_type', 'unit_count', 'is_active']
    list_filter = ['addon_type', 'is_active']
    list_editable = ['unit_count', 'is_active']
    search_fields = ['name']


@admin.register(FacilityAddonSetting)
class FacilityAddonSettingAdmin(admin.ModelAdmin):
    list_display = ['facility', 'addon', 'is_enabled']
    list_filter = ['facility', 'is_enabled']

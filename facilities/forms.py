"""
施設設定・タグ管理のフォーム定義
"""

from django import forms
from .models import Facility, SupportContentTag
from records.models import ActivityTag


class FacilityForm(forms.ModelForm):
    """施設基本情報の編集フォーム"""

    class Meta:
        model = Facility
        fields = [
            'name', 'office_number', 'address', 'phone',
            'region_category', 'standard_close_time',
            'base_unit_count', 'is_new_facility_r8',
            'line_channel_access_token', 'line_channel_secret',
        ]
        widgets = {
            'name':                     forms.TextInput(attrs={'class': 'form-control'}),
            'office_number':            forms.TextInput(attrs={'class': 'form-control'}),
            'address':                  forms.TextInput(attrs={'class': 'form-control'}),
            'phone':                    forms.TextInput(attrs={'class': 'form-control'}),
            'region_category':          forms.Select(attrs={'class': 'form-select'}),
            'standard_close_time':      forms.TimeInput(attrs={'class': 'form-control', 'type': 'time'}),
            'base_unit_count':          forms.NumberInput(attrs={'class': 'form-control', 'min': '0'}),
            'is_new_facility_r8':       forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'line_channel_access_token': forms.TextInput(attrs={'class': 'form-control'}),
            'line_channel_secret':      forms.TextInput(attrs={'class': 'form-control'}),
        }


class ActivityTagForm(forms.ModelForm):
    """活動タグの追加・編集フォーム"""

    class Meta:
        model = ActivityTag
        fields = ['name', 'display_order', 'is_active', 'price']
        widgets = {
            'name':          forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'タグ名を入力'}),
            'display_order': forms.NumberInput(attrs={'class': 'form-control', 'min': '0'}),
            'is_active':     forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'price':         forms.NumberInput(attrs={'class': 'form-control', 'min': '0'}),
        }
        labels = {
            'name':          'タグ名',
            'display_order': '表示順',
            'is_active':     '有効',
            'price':         '実費単価（円/回）',
        }


class SupportContentTagForm(forms.ModelForm):
    """支援内容タグの追加・編集フォーム"""

    class Meta:
        model = SupportContentTag
        fields = ['name', 'order', 'is_active']
        widgets = {
            'name':      forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'タグ名を入力'}),
            'order':     forms.NumberInput(attrs={'class': 'form-control', 'min': '0'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        labels = {
            'name':      'タグ名',
            'order':     '表示順',
            'is_active': '有効',
        }

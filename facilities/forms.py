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
            'name', 'office_number', 'address', 'phone', 'representative_email',
            'region_category', 'standard_close_time',
            'base_unit_count', 'base_unit_count_severe', 'is_new_facility_r8',
            'line_channel_access_token', 'line_channel_secret',
            'term_staff', 'term_beneficiary', 'logo', 'brand_color',
        ]
        widgets = {
            'name':                     forms.TextInput(attrs={'class': 'form-control'}),
            'office_number':            forms.TextInput(attrs={'class': 'form-control'}),
            'address':                  forms.TextInput(attrs={'class': 'form-control'}),
            'phone':                    forms.TextInput(attrs={'class': 'form-control'}),
            'representative_email':     forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'example@example.com'}),
            'region_category':          forms.Select(attrs={'class': 'form-select'}),
            'standard_close_time':      forms.TimeInput(attrs={'class': 'form-control', 'type': 'time'}),
            'base_unit_count':          forms.NumberInput(attrs={'class': 'form-control', 'min': '0'}),
            'is_new_facility_r8':       forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'line_channel_access_token': forms.TextInput(attrs={'class': 'form-control'}),
            'line_channel_secret':      forms.TextInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ('term_staff', 'term_beneficiary'):
            self.fields[name].widget.attrs.update({'class': 'form-control'})
        self.fields['logo'].widget.attrs.update({'class': 'form-control', 'accept': 'image/*'})
        self.fields['brand_color'].widget = forms.TextInput(attrs={'type': 'color', 'class': 'form-control form-control-color'})
        self.fields['brand_color'].required = False

    def clean_brand_color(self):
        v = (self.cleaned_data.get('brand_color') or '').strip().lower()
        import re
        if v and not re.fullmatch(r'#[0-9a-f]{6}', v):
            raise forms.ValidationError('#4e7d89 のような形式で入力してください。')
        return v


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
        fields = ['name', 'order', 'price', 'is_active']
        widgets = {
            'name':      forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'タグ名を入力'}),
            'order':     forms.NumberInput(attrs={'class': 'form-control', 'min': '0'}),
            'price':     forms.NumberInput(attrs={'class': 'form-control', 'min': '0'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        labels = {
            'name':      'タグ名',
            'order':     '表示順',
            'is_active': '有効',
        }

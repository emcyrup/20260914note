"""
請求管理のフォーム定義
"""

from django import forms

from .models import CopaymentManagement, CopaymentOfficeRecord


class CopaymentManagementForm(forms.ModelForm):
    """上限額管理の基本情報フォーム"""

    class Meta:
        model  = CopaymentManagement
        fields = ['is_upper_limit_manager', 'management_result']
        widgets = {
            'management_result': forms.Select(attrs={'class': 'form-select'}),
        }
        labels = {
            'is_upper_limit_manager': '当施設が上限額管理事業所である',
            'management_result':      '管理結果',
        }


class CopaymentOfficeRecordForm(forms.ModelForm):
    """各事業所の利用実績フォーム（当施設・他事業所共通）"""

    class Meta:
        model  = CopaymentOfficeRecord
        fields = ['office_name', 'office_number', 'total_cost', 'original_copayment', 'adjusted_copayment']
        widgets = {
            'office_name':         forms.TextInput(attrs={'class': 'form-control', 'placeholder': '事業所名'}),
            'office_number':       forms.TextInput(attrs={'class': 'form-control', 'placeholder': '事業所番号'}),
            'total_cost':          forms.NumberInput(attrs={'class': 'form-control', 'min': '0'}),
            'original_copayment':  forms.NumberInput(attrs={'class': 'form-control', 'min': '0'}),
            'adjusted_copayment':  forms.NumberInput(attrs={'class': 'form-control', 'min': '0'}),
        }
        labels = {
            'office_name':        '事業所名',
            'office_number':      '事業所番号',
            'total_cost':         '総費用額（円）',
            'original_copayment': '利用者負担額（調整前）',
            'adjusted_copayment': '利用者負担額（調整後）',
        }

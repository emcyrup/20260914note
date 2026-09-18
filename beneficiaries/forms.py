from django import forms
from .models import Beneficiary, BeneficiaryOffice, Guardian, RecipientCertificate


class BeneficiaryForm(forms.ModelForm):
    """利用者の基本情報フォーム"""

    class Meta:
        model = Beneficiary
        fields = [
            'last_name', 'first_name',
            'last_name_kana', 'first_name_kana',
            'date_of_birth', 'gender',
            'disability_class', 'disability_type', 'is_severe',
            'weekday_mon', 'weekday_tue', 'weekday_wed',
            'weekday_thu', 'weekday_fri', 'weekday_sat',
            'notes', 'status',
        ]
        widgets = {
            'date_of_birth': forms.DateInput(attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }
        labels = {
            'last_name': '姓', 'first_name': '名',
            'last_name_kana': '姓（かな）', 'first_name_kana': '名（かな）',
        }


class GuardianForm(forms.ModelForm):
    """保護者情報フォーム"""

    class Meta:
        model = Guardian
        fields = [
            'last_name', 'first_name', 'relation',
            'phone', 'email', 'is_primary',
            'line_user_id', 'line_linked',
        ]
        labels = {
            'line_user_id': 'LINE User ID',
            'line_linked': 'LINE連携済みにする',
        }
        help_texts = {
            'line_user_id': '通常はWebhookで自動入力されます。テスト目的で手動入力も可能です。',
        }


class RecipientCertificateForm(forms.ModelForm):
    """受給者証フォーム"""

    class Meta:
        model = RecipientCertificate
        fields = [
            'certificate_number', 'granted_days', 'monthly_cap',
            'valid_from', 'valid_until',
            'municipality', 'support_office',
            'scanned_image',
        ]
        widgets = {
            'valid_from': forms.DateInput(attrs={'type': 'date'}),
            'valid_until': forms.DateInput(attrs={'type': 'date'}),
        }


class BeneficiaryOfficeForm(forms.ModelForm):
    """利用事業所（上限額管理の相手先）フォーム"""

    class Meta:
        model = BeneficiaryOffice
        fields = ['name', 'office_number', 'is_this_office', 'is_manager', 'address', 'phone', 'fax', 'contact_name', 'note', 'order']
        labels = {
            'name': '事業所名', 'office_number': '事業所番号',
            'is_this_office': '当施設', 'is_manager': '上限管理事業所',
        }

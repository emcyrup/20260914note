from django import forms
from .models import Beneficiary, Guardian, RecipientCertificate


class BeneficiaryForm(forms.ModelForm):
    """利用者の基本情報フォーム"""

    class Meta:
        model = Beneficiary
        fields = [
            'last_name', 'first_name',
            'last_name_kana', 'first_name_kana',
            'date_of_birth', 'gender',
            'disability_class', 'disability_type',
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

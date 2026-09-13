from django import forms

from .models import StaffAccount


class StaffCreateForm(forms.ModelForm):
    password = forms.CharField(label='初期パスワード', min_length=8, widget=forms.PasswordInput(attrs={'class': 'form-control'}))

    class Meta:
        model = StaffAccount
        fields = ['username', 'display_name', 'role', 'ui_theme']
        widgets = {
            'username': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '半角英数（ログインに使う）'}),
            'display_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '例：山田 花子'}),
            'role': forms.Select(attrs={'class': 'form-select'}),
            'ui_theme': forms.Select(attrs={'class': 'form-select'}),
        }
        labels = {'username': 'ログインID', 'display_name': '表示名', 'role': '権限区分', 'ui_theme': '画面の見た目'}


class StaffUpdateForm(forms.ModelForm):
    new_password = forms.CharField(label='新しいパスワード', required=False, min_length=8,
                                   widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': '変更しない場合は空のまま'}))

    class Meta:
        model = StaffAccount
        fields = ['display_name', 'role', 'ui_theme', 'is_active']
        widgets = {
            'display_name': forms.TextInput(attrs={'class': 'form-control'}),
            'role': forms.Select(attrs={'class': 'form-select'}),
            'ui_theme': forms.Select(attrs={'class': 'form-select'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

from django import forms
from django.contrib.auth import password_validation

from .models import StaffAccount, StaffInvitation


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


class _PasswordPairMixin:
    """password1 / password2 の一致と Django の強度チェック"""

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get('password1'), cleaned.get('password2')
        if p1 and p2 and p1 != p2:
            self.add_error('password2', 'パスワードが一致しません。')
        elif p1:
            tmp = StaffAccount(username=cleaned.get('username', ''), display_name=cleaned.get('display_name', ''))
            try:
                password_validation.validate_password(p1, tmp)
            except forms.ValidationError as e:
                self.add_error('password1', e)
        return cleaned


class JoinForm(_PasswordPairMixin, forms.Form):
    """招待リンクから自分でアカウントを作る"""
    username = forms.CharField(label='ログインID', max_length=150,
                               widget=forms.TextInput(attrs={'class': 'form-control form-control-lg', 'autocomplete': 'username', 'placeholder': '半角英数（ログインに使う）'}))
    display_name = forms.CharField(label='表示名', max_length=50,
                                   widget=forms.TextInput(attrs={'class': 'form-control form-control-lg', 'placeholder': '例：山田 花子'}))
    password1 = forms.CharField(label='パスワード', widget=forms.PasswordInput(attrs={'class': 'form-control form-control-lg', 'autocomplete': 'new-password'}))
    password2 = forms.CharField(label='パスワード（確認）', widget=forms.PasswordInput(attrs={'class': 'form-control form-control-lg', 'autocomplete': 'new-password'}))

    def clean_username(self):
        username = self.cleaned_data['username'].strip()
        StaffAccount.username_validator(username)
        if StaffAccount.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError('このログインIDはすでに使われています。別のIDにしてください。')
        return username


class InvitationForm(forms.ModelForm):
    expires_days = forms.TypedChoiceField(label='有効期限', coerce=int, initial=7,
                                          choices=[(1, '1日'), (3, '3日'), (7, '7日'), (30, '30日')],
                                          widget=forms.Select(attrs={'class': 'form-select'}))

    class Meta:
        model = StaffInvitation
        fields = ['role', 'note', 'max_uses']
        widgets = {
            'role': forms.Select(attrs={'class': 'form-select'}),
            'note': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '例：4月入職の3名'}),
            'max_uses': forms.NumberInput(attrs={'class': 'form-control', 'min': 1, 'max': 50}),
        }
        labels = {'role': '権限区分', 'note': 'メモ', 'max_uses': '使える回数'}

    def clean_max_uses(self):
        n = self.cleaned_data['max_uses']
        if not 1 <= n <= 50:
            raise forms.ValidationError('1〜50 の範囲で指定してください。')
        return n


class FacilitySignupForm(_PasswordPairMixin, forms.Form):
    """新しい事業所として登録する（事業所＋管理者アカウント）"""
    facility_name = forms.CharField(label='事業所名', max_length=100,
                                    widget=forms.TextInput(attrs={'class': 'form-control form-control-lg', 'placeholder': '例：あおば教室'}))
    office_number = forms.CharField(label='事業所番号', max_length=10, required=False,
                                    widget=forms.TextInput(attrs={'class': 'form-control form-control-lg', 'placeholder': '10桁（あとからでも設定できます）'}))
    display_name = forms.CharField(label='管理者の氏名', max_length=50,
                                   widget=forms.TextInput(attrs={'class': 'form-control form-control-lg', 'placeholder': '例：山田 花子'}))
    username = forms.CharField(label='ログインID', max_length=150,
                               widget=forms.TextInput(attrs={'class': 'form-control form-control-lg', 'autocomplete': 'username', 'placeholder': '半角英数（ログインに使う）'}))
    password1 = forms.CharField(label='パスワード', widget=forms.PasswordInput(attrs={'class': 'form-control form-control-lg', 'autocomplete': 'new-password'}))
    password2 = forms.CharField(label='パスワード（確認）', widget=forms.PasswordInput(attrs={'class': 'form-control form-control-lg', 'autocomplete': 'new-password'}))
    signup_code = forms.CharField(label='登録コード', required=False,
                                  widget=forms.TextInput(attrs={'class': 'form-control form-control-lg', 'autocomplete': 'off'}))

    def __init__(self, *args, require_code=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.require_code = require_code
        if not require_code:
            del self.fields['signup_code']

    def clean_username(self):
        return JoinForm.clean_username(self)

    def clean_facility_name(self):
        from facilities.models import Facility
        name = self.cleaned_data['facility_name'].strip()
        if Facility.objects.filter(name=name).exists():
            raise forms.ValidationError('この事業所名はすでに登録されています。すでに登録した事業所に入るときは、管理者から招待リンクをもらってください。')
        return name

    def clean_signup_code(self):
        from django.conf import settings
        code = (self.cleaned_data.get('signup_code') or '').strip()
        if self.require_code and code != settings.SIGNUP_CODE:
            raise forms.ValidationError('登録コードが正しくありません。')
        return code

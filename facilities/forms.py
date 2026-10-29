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
            'capacity_class', 'time_kubun_weekday', 'time_kubun_holiday', 'base_code_1', 'base_code_2', 'base_code_3',
            'is_severe_facility', 'severe_capacity', 'base_unit_count', 'base_unit_count_severe', 'base_unit_count_severe_holiday',
            'base_code_severe', 'base_code_severe_holiday', 'is_new_facility_r8',
            'reduce_self_eval', 'reduce_program', 'reduce_hours',
            'line_channel_access_token', 'line_channel_secret',
            'security_notify', 'security_notify_email', 'mail_from',
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
            'base_unit_count_severe':   forms.NumberInput(attrs={'class': 'form-control', 'min': '0'}),
            'base_unit_count_severe_holiday': forms.NumberInput(attrs={'class': 'form-control', 'min': '0'}),
            'capacity_class':           forms.Select(attrs={'class': 'form-select'}),
            'time_kubun_weekday':       forms.Select(attrs={'class': 'form-select'}),
            'time_kubun_holiday':       forms.Select(attrs={'class': 'form-select'}),
            'base_code_1':              forms.TextInput(attrs={'class': 'form-control font-monospace', 'maxlength': '10', 'placeholder': '例：63xxxx'}),
            'base_code_severe': forms.TextInput(attrs={'class': 'form-control font-monospace', 'maxlength': '10', 'placeholder': '例：63xxxx'}),
            'base_code_severe_holiday': forms.TextInput(attrs={'class': 'form-control font-monospace', 'maxlength': '10', 'placeholder': '例：63xxxx'}),
            'base_code_2':              forms.TextInput(attrs={'class': 'form-control font-monospace', 'maxlength': '10', 'placeholder': '例：63xxxx'}),
            'base_code_3':              forms.TextInput(attrs={'class': 'form-control font-monospace', 'maxlength': '10', 'placeholder': '例：63xxxx'}),
            'is_severe_facility':       forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'severe_capacity':          forms.Select(attrs={'class': 'form-select'}),
            'is_new_facility_r8':       forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'reduce_self_eval':         forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'reduce_program':           forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'reduce_hours':             forms.Select(attrs={'class': 'form-select'}),
            'line_channel_access_token': forms.TextInput(attrs={'class': 'form-control'}),
            'line_channel_secret':      forms.TextInput(attrs={'class': 'form-control'}),
            'security_notify':          forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'security_notify_email':    forms.EmailInput(attrs={'class': 'form-control', 'placeholder': '例：admin@example.com'}),
            'mail_from':                forms.EmailInput(attrs={'class': 'form-control', 'placeholder': '例：michinote.alert@gmail.com', 'autocomplete': 'off'}),
        }

    # Gmail のアプリ パスワード（保存ずみの値は画面に出さない。空なら変えない。「消す」に印で消す）
    mail_app_password_new = forms.CharField(required=False, label='Gmail のアプリ パスワード', max_length=64,
                                            widget=forms.PasswordInput(attrs={'class': 'form-control', 'autocomplete': 'new-password',
                                                                              'placeholder': '16 桁（変えないときは空のまま）'}))
    mail_app_password_clear = forms.BooleanField(required=False, label='アプリ パスワードを消す',
                                                 widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}))

    def clean_mail_app_password_new(self):
        return (self.cleaned_data.get('mail_app_password_new') or '').replace(' ', '').strip()

    def save(self, commit=True):
        obj = super().save(commit=False)
        if self.cleaned_data.get('mail_app_password_clear'):
            obj.mail_app_password = ''
        elif self.cleaned_data.get('mail_app_password_new'):
            obj.mail_app_password = self.cleaned_data['mail_app_password_new']
        if commit:
            obj.save()
            self.save_m2m()
        return obj

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from billing.codes import SEVERE_CAPACITY_CHOICES
        self.fields['severe_capacity'] = forms.ChoiceField(choices=SEVERE_CAPACITY_CHOICES, required=False, label='重心型の定員',
                                                           widget=forms.Select(attrs={'class': 'form-select'}))
        from billing.codes import KUBUN_CHOICES
        self.fields['time_kubun_weekday'].widget.choices = [(k, v) for k, v in KUBUN_CHOICES if k != 3]
        self.fields['time_kubun_holiday'].widget.choices = list(KUBUN_CHOICES)
        for name in ('time_kubun_weekday', 'time_kubun_holiday'):        # 欄が無い画面（請求を使わない事業所）からの保存でも通す
            self.fields[name].required = False

    def _kubun(self, name, allowed):
        value = self.cleaned_data.get(name)
        if value in allowed:
            return value
        return getattr(self.instance, name, None) or (2 if name == 'time_kubun_weekday' else 3)

    def clean_time_kubun_weekday(self):
        return self._kubun('time_kubun_weekday', (1, 2))

    def clean_time_kubun_holiday(self):
        return self._kubun('time_kubun_holiday', (1, 2, 3))
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

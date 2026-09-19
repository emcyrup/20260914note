from django import forms

from accounts.models import StaffAccount
from beneficiaries.models import Beneficiary, Guardian
from facilities.models import Facility
from support_plans.forms import _StyledForm
from support_plans.models import ConsentDelivery, MonitoringRecord, PlanDraft, StaffMeeting

from .models import Interview


class ProfileForm(_StyledForm):
    """プロフィールタブ（基本情報・住まいと学校・通所状態）"""

    class Meta:
        model = Beneficiary
        fields = ['last_name', 'first_name', 'last_name_kana', 'first_name_kana', 'gender', 'date_of_birth',
                  'status', 'admission_date', 'discharge_date', 'has_prior_records',
                  'postal_code', 'address', 'mobile_phone', 'home_phone', 'school_name', 'grade',
                  'disability_type', 'is_severe', 'notes']
        widgets = {'notes': forms.Textarea(attrs={'rows': 2})}


class InterviewForm(_StyledForm):
    class Meta:
        model = Interview
        fields = ['period_start', 'period_end', 'interview_date', 'created_date', 'author', 'participants',
                  'home_parent', 'home_staff', 'school_parent', 'school_staff', 'social_parent', 'social_staff',
                  'future_wishes', 'other_wishes', 'notes', 'extension_reason']
        widgets = {f: forms.Textarea(attrs={'rows': 2}) for f in
                   ('home_parent', 'home_staff', 'school_parent', 'school_staff', 'social_parent', 'social_staff',
                    'future_wishes', 'other_wishes', 'notes', 'extension_reason')}

    def __init__(self, *args, facility=None, **kwargs):
        super().__init__(*args, facility=facility, **kwargs)
        qs = StaffAccount.objects.filter(facility=facility, is_active=True).order_by('username') if facility else StaffAccount.objects.none()
        self.fields['participants'].queryset = qs
        self.fields['participants'].widget = forms.CheckboxSelectMultiple()
        self.fields['participants'].widget.choices = self.fields['participants'].choices
        self.fields['notes'].widget.attrs['maxlength'] = 500
        self.fields['extension_reason'].widget.attrs['maxlength'] = 500

    def clean(self):
        data = super().clean()
        s, e = data.get('period_start'), data.get('period_end')
        if s and e and s > e:
            self.add_error('period_end', '終了日は開始日より後にしてください。')
        return data


class DraftForm(_StyledForm):
    class Meta:
        model = PlanDraft
        fields = ['period_start', 'period_end', 'policy', 'family_wishes', 'notes']
        widgets = {'policy': forms.Textarea(attrs={'rows': 3}), 'family_wishes': forms.Textarea(attrs={'rows': 2}),
                   'notes': forms.Textarea(attrs={'rows': 2})}


class MeetingForm(_StyledForm):
    class Meta:
        model = StaffMeeting
        fields = ['meeting_date', 'attendees', 'beneficiary_attended', 'guardian_attended', 'absence_reason',
                  'opinions', 'revised', 'revision_summary']
        widgets = {'attendees': forms.Textarea(attrs={'rows': 2}), 'opinions': forms.Textarea(attrs={'rows': 4}),
                   'revision_summary': forms.Textarea(attrs={'rows': 2})}


class ConsentForm(_StyledForm):
    class Meta:
        model = ConsentDelivery
        fields = ['explained_date', 'explained_to', 'explained_by', 'consent_method', 'consent_date', 'consent_signer',
                  'delivered_to_user_date', 'consultation_office', 'delivered_to_office_date', 'service_start_date']


class MonitoringForm(_StyledForm):
    class Meta:
        model = MonitoringRecord
        fields = ['date', 'interviewed_with', 'conducted_by', 'implementation', 'achievement', 'achievement_detail',
                  'review_needed', 'review_reason', 'next_due']
        widgets = {'implementation': forms.Textarea(attrs={'rows': 3}), 'achievement_detail': forms.Textarea(attrs={'rows': 2}),
                   'review_reason': forms.Textarea(attrs={'rows': 2})}


class GuardianEditForm(forms.ModelForm):
    class Meta:
        model = Guardian
        fields = ['last_name', 'first_name', 'relation', 'phone', 'email', 'is_primary', 'memo']


class FacilityInfoForm(forms.ModelForm):
    class Meta:
        model = Facility
        fields = ['name', 'postal_code', 'address', 'address2', 'phone', 'company_name', 'representative_name',
                  'representative_email']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            f.widget.attrs.setdefault('class', 'form-control')


class ImportForm(forms.Form):
    file = forms.FileField(label='ファイル（CSV または Excel）')

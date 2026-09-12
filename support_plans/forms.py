from django import forms

from accounts.models import StaffAccount

from .models import (
    Assessment, ConsentDelivery, Monitoring, MonitoringRecord, PlanDraft, PlanGoal, StaffMeeting, SupportPlan,
)


class _StyledForm(forms.ModelForm):
    """Bootstrap のクラスと日付入力を一括で当てる"""

    facility = None

    def __init__(self, *args, facility=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.facility = facility
        for name, field in self.fields.items():
            w = field.widget
            if isinstance(w, forms.CheckboxInput):
                w.attrs.setdefault('class', 'form-check-input')
            elif isinstance(w, forms.Select):
                w.attrs.setdefault('class', 'form-select')
            else:
                w.attrs.setdefault('class', 'form-control')
            if isinstance(field, forms.DateField):
                w.input_type = 'date'
                w.format = '%Y-%m-%d'
                field.input_formats = ['%Y-%m-%d']
            if isinstance(w, forms.Textarea) and int(w.attrs.get('rows', 10)) >= 10:
                w.attrs['rows'] = 3  # Django 既定の 10 行は長すぎる
            if isinstance(field, forms.ModelChoiceField) and field.queryset.model is StaffAccount and facility:
                field.queryset = StaffAccount.objects.filter(facility=facility, is_active=True).order_by('username')


class SupportPlanForm(_StyledForm):
    class Meta:
        model = SupportPlan
        fields = ['title', 'manager']


class AssessmentForm(_StyledForm):
    class Meta:
        model = Assessment
        fields = ['interview_date', 'interviewed_with', 'interviewer', 'condition', 'environment', 'wishes', 'notes']
        widgets = {'condition': forms.Textarea(attrs={'rows': 4}), 'environment': forms.Textarea(attrs={'rows': 4}),
                   'wishes': forms.Textarea(attrs={'rows': 4})}


class PlanDraftForm(_StyledForm):
    class Meta:
        model = PlanDraft
        fields = ['period_start', 'period_end', 'family_wishes', 'policy', 'notes']
        widgets = {'policy': forms.Textarea(attrs={'rows': 4})}

    def clean(self):
        data = super().clean()
        s, e = data.get('period_start'), data.get('period_end')
        if s and e and s > e:
            self.add_error('period_end', '終了日は開始日より後にしてください。')
        return data


class PlanGoalForm(_StyledForm):
    class Meta:
        model = PlanGoal
        fields = ['goal_type', 'content', 'target_date', 'support_content', 'frequency']

    def clean(self):
        data = super().clean()
        if data.get('goal_type') == PlanGoal.TYPE_SHORT and not (data.get('support_content') or '').strip():
            self.add_error('support_content', '短期目標には具体的な支援内容を記入してください。')
        return data


class StaffMeetingForm(_StyledForm):
    class Meta:
        model = StaffMeeting
        fields = ['meeting_date', 'attendees', 'beneficiary_attended', 'guardian_attended', 'absence_reason',
                  'opinions', 'revised', 'revision_summary']
        widgets = {'attendees': forms.Textarea(attrs={'rows': 2}), 'opinions': forms.Textarea(attrs={'rows': 5})}


class ConsentDeliveryForm(_StyledForm):
    class Meta:
        model = ConsentDelivery
        fields = ['explained_date', 'explained_to', 'explained_by', 'consent_method', 'consent_date', 'consent_signer',
                  'delivered_to_user_date', 'consultation_office', 'delivered_to_office_date', 'service_start_date']


class MonitoringSettingForm(_StyledForm):
    class Meta:
        model = Monitoring
        fields = ['interval_months']


class MonitoringRecordForm(_StyledForm):
    class Meta:
        model = MonitoringRecord
        fields = ['date', 'interviewed_with', 'conducted_by', 'implementation', 'achievement', 'achievement_detail',
                  'review_needed', 'review_reason', 'next_due']

    def clean(self):
        data = super().clean()
        if data.get('review_needed') and not (data.get('review_reason') or '').strip():
            self.add_error('review_reason', '見直しが必要な理由を記入してください。')
        return data

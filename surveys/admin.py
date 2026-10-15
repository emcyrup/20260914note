from django.contrib import admin

from .models import SelfEvaluation, Survey, SurveyResponse

admin.site.register(Survey)
admin.site.register(SurveyResponse)
admin.site.register(SelfEvaluation)

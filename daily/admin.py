from django.contrib import admin

from .models import ClassGroup, GroupSession, HealthLog, HealthProfile

admin.site.register(ClassGroup)
admin.site.register(GroupSession)
admin.site.register(HealthLog)
admin.site.register(HealthProfile)

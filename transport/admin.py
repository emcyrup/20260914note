from django.contrib import admin

from .models import Driver, TransportAssignment, TransportProfile, Vehicle

admin.site.register(Vehicle)
admin.site.register(Driver)
admin.site.register(TransportProfile)
admin.site.register(TransportAssignment)

"""
その日の担当（月間予定表で入れ、業務日誌の右上に印字する）。
"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('facilities', '0017_ryoiku_layout_trial'),
        ('reservations', '0010_monthlyrequest_ng_dates'),
    ]

    operations = [
        migrations.CreateModel(
            name='DayStaff',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateField(verbose_name='日付')),
                ('text', models.CharField(blank=True, max_length=100, verbose_name='担当')),
                ('facility', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='day_staff', to='facilities.facility')),
            ],
            options={
                'verbose_name': 'その日の担当',
                'verbose_name_plural': 'その日の担当',
                'ordering': ['date'],
                'unique_together': {('facility', 'date')},
            },
        ),
    ]

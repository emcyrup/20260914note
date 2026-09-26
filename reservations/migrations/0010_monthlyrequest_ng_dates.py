"""
月予約利用希望の書き方：用紙の○（wishes）のほかに、「来られない日」だけを書いた用紙（ng_dates）に対応する。
来られない日の書き方のときは、その月のそれ以外の日をすべて終日可能として扱う。
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('reservations', '0009_rename_ryoiku_facility'),
    ]

    operations = [
        migrations.AddField(
            model_name='monthlyrequest',
            name='wish_mode',
            field=models.CharField(choices=[('ok', '可能な日時に○'), ('ng', '来られない日を書く')], default='ok', max_length=2, verbose_name='書き方'),
        ),
        migrations.AddField(
            model_name='monthlyrequest',
            name='ng_dates',
            field=models.JSONField(blank=True, default=list, verbose_name='来られない日'),
        ),
    ]

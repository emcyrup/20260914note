"""
月予約利用希望の入口に「Excel・CSV から」を追加。
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('reservations', '0011_daystaff'),
    ]

    operations = [
        migrations.AlterField(
            model_name='monthlyrequest',
            name='source',
            field=models.CharField(choices=[('staff', '職員が転記'), ('web', '顧客ページ'), ('photo', '用紙の写真から'), ('file', 'Excel・CSV から')], default='staff', max_length=10, verbose_name='入口'),
        ),
    ]

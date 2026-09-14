from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('support_plans', '0002_plangoal_evidence'),
    ]

    operations = [
        migrations.AddField(
            model_name='supportplan',
            name='form_extra',
            field=models.JSONField(blank=True, default=dict, verbose_name='様式の追加項目'),
        ),
        migrations.AddField(
            model_name='plangoal',
            name='form_extra',
            field=models.JSONField(blank=True, default=dict, verbose_name='様式の追加項目'),
        ),
    ]

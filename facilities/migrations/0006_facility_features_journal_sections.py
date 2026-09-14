from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('facilities', '0005_facility_branding_terms'),
    ]

    operations = [
        # 既存の施設は「使わない」で追加し、これから作る施設は「使う」を既定にする
        migrations.AddField(
            model_name='facility',
            name='use_billing',
            field=models.BooleanField(default=False, verbose_name='請求機能を使う'),
        ),
        migrations.AddField(
            model_name='facility',
            name='use_line',
            field=models.BooleanField(default=False, verbose_name='LINE連携を使う'),
        ),
        migrations.AlterField(
            model_name='facility',
            name='use_billing',
            field=models.BooleanField(default=True, verbose_name='請求機能を使う'),
        ),
        migrations.AlterField(
            model_name='facility',
            name='use_line',
            field=models.BooleanField(default=True, verbose_name='LINE連携を使う'),
        ),
        migrations.AddField(
            model_name='facility',
            name='journal_sections',
            field=models.JSONField(blank=True, default=list, verbose_name='日誌の項目と順番'),
        ),
    ]

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('beneficiaries', '0005_add_unique_constraint_to_line_registration_code'),
        ('facilities', '0006_facility_features_journal_sections'),
        ('records', '0007_record_template'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PaperScan',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('image', models.ImageField(upload_to='paper_scans/%Y/%m/', verbose_name='画像')),
                ('status', models.CharField(choices=[('pending', '未読み取り'), ('extracted', '確認待ち'), ('imported', '日誌にした')], default='pending', max_length=10, verbose_name='状態')),
                ('extracted', models.JSONField(blank=True, default=dict, verbose_name='読み取り結果')),
                ('error', models.TextField(blank=True, verbose_name='エラー')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('extracted_at', models.DateTimeField(blank=True, null=True)),
                ('beneficiary', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='paper_scans', to='beneficiaries.beneficiary', verbose_name='利用者')),
                ('facility', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='facilities.facility', verbose_name='施設')),
                ('record', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='paper_scans', to='records.dailyrecord', verbose_name='作成した日誌')),
                ('uploaded_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL, verbose_name='取り込んだ人')),
            ],
            options={
                'verbose_name': '紙の日誌（取り込み）',
                'verbose_name_plural': '紙の日誌（取り込み）',
                'ordering': ['-created_at'],
            },
        ),
    ]

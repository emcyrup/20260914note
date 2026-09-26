"""
利用者の基本情報に付ける書類・画像（写真・PDF・Excel・CSV）。ドラッグ＆ドロップで取り込む。
"""
from django.db import migrations, models
import django.db.models.deletion

import facilities.uploads


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0006_signup_pending'),
        ('beneficiaries', '0010_assessments'),
    ]

    operations = [
        migrations.CreateModel(
            name='BeneficiaryDocument',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('file', models.FileField(upload_to=facilities.uploads.document_upload_to, verbose_name='ファイル')),
                ('file_name', models.CharField(blank=True, max_length=200, verbose_name='元のファイル名')),
                ('title', models.CharField(blank=True, max_length=100, verbose_name='件名')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('beneficiary', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='documents', to='beneficiaries.beneficiary', verbose_name='利用者')),
                ('uploaded_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='accounts.staffaccount', verbose_name='登録者')),
            ],
            options={
                'verbose_name': '利用者の書類',
                'verbose_name_plural': '利用者の書類',
                'ordering': ['-created_at', '-pk'],
            },
        ),
    ]

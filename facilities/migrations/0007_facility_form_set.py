from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('facilities', '0006_facility_features_journal_sections'),
    ]

    operations = [
        migrations.AddField(
            model_name='facility',
            name='form_set',
            field=models.CharField(choices=[('standard', '標準'), ('happiness', 'はぴねす様式（関係機関連携報告書・個別支援計画書 別紙1／詳細版・専門的支援実施計画書）')], default='standard', max_length=20, verbose_name='帳票様式'),
        ),
    ]

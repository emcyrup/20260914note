from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('facilities', '0038_severe_capacity')]
    operations = [
        migrations.AlterField(
            model_name='facility', name='form_set',
            field=models.CharField(choices=[('standard', '標準'), ('happiness', 'はびりす様式（関係機関連携報告書・個別支援計画書 別紙1／詳細版・専門的支援実施計画書）'),
                                            ('owl', 'オウル様式（個別支援計画・個別支援計画別表）')],
                                   default='standard', max_length=20, verbose_name='帳票様式'),
        ),
    ]

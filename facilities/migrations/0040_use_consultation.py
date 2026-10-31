from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('facilities', '0039_form_set_owl')]
    operations = [
        migrations.AddField(
            model_name='facility', name='use_consultation',
            field=models.BooleanField(default=False, verbose_name='相談支援の計画を使う',
                                      help_text='計画相談支援・障害児相談支援の書類（申請者の現状・サービス等利用計画・週間計画表）を作り、A4 で印刷します。児童発達支援の個別支援計画はこの計画の方針・目標に沿わせます。'),
        ),
    ]

from django.db import migrations, models


def turn_on_for_yours(apps, schema_editor):
    """ゆあーずの依頼（2026-10-13）。ほかの事業所はこれまでどおり"""
    Facility = apps.get_model('facilities', 'Facility')
    Facility.objects.filter(name='発達支援ルーム　ゆあーず').update(schedule_tools=True, therapy_no_wording=True)


class Migration(migrations.Migration):

    dependencies = [
        ('facilities', '0028_beneficiary_tools'),
    ]

    operations = [
        migrations.AddField(
            model_name='facility',
            name='schedule_tools',
            field=models.BooleanField(default=False, verbose_name='予定づくり：共演NG の一括の印・編集中の日時・ドラッグで共演NG を赤く・名前で探す・月の予定のやり直し'),
        ),
        migrations.AddField(
            model_name='facility',
            name='therapy_no_wording',
            field=models.BooleanField(default=False, verbose_name='療育記録：保存のときの言葉づかいの注意を出さない'),
        ),
        migrations.RunPython(turn_on_for_yours, migrations.RunPython.noop),
    ]

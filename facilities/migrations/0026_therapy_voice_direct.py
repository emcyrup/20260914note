from django.db import migrations, models


def turn_on_for_yours(apps, schema_editor):
    """ゆあーずの依頼（2026-10-13）：音声入力を今日の記録へ直接入れる。ほかの事業所はこれまでどおり"""
    Facility = apps.get_model('facilities', 'Facility')
    Facility.objects.filter(name='発達支援ルーム　ゆあーず').update(therapy_voice_direct=True)


class Migration(migrations.Migration):

    dependencies = [
        ('facilities', '0025_journal_quality'),
    ]

    operations = [
        migrations.AddField(
            model_name='facility',
            name='therapy_voice_direct',
            field=models.BooleanField(default=False, verbose_name='療育記録：音声入力を今日の記録へ直接入れる'),
        ),
        migrations.RunPython(turn_on_for_yours, migrations.RunPython.noop),
    ]

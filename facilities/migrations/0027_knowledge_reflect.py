from django.db import migrations, models


def turn_on_for_yours(apps, schema_editor):
    """ゆあーずの依頼（2026-10-13）：書類の読み取りの「療育記録に反映する」は初めは印なし。ほかの事業所はこれまでどおり"""
    Facility = apps.get_model('facilities', 'Facility')
    Facility.objects.filter(name='発達支援ルーム　ゆあーず').update(knowledge_reflect=True)


class Migration(migrations.Migration):

    dependencies = [
        ('facilities', '0026_therapy_voice_direct'),
    ]

    operations = [
        migrations.AddField(
            model_name='facility',
            name='knowledge_reflect',
            field=models.BooleanField(default=False, verbose_name='書類の読み取り：「療育記録に反映する」は初めは印なし'),
        ),
        migrations.RunPython(turn_on_for_yours, migrations.RunPython.noop),
    ]

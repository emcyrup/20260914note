from django.db import migrations, models


def turn_on_for_yours(apps, schema_editor):
    """ゆあーずの依頼（2026-10-13）。ほかの事業所はこれまでどおり"""
    Facility = apps.get_model('facilities', 'Facility')
    Facility.objects.filter(name='発達支援ルーム　ゆあーず').update(beneficiary_tools=True)


class Migration(migrations.Migration):

    dependencies = [
        ('facilities', '0027_knowledge_reflect'),
    ]

    operations = [
        migrations.AddField(
            model_name='facility',
            name='beneficiary_tools',
            field=models.BooleanField(default=False, verbose_name='利用者情報：前後の利用者へ送る・編集中の警告・分かったことの削除'),
        ),
        migrations.RunPython(turn_on_for_yours, migrations.RunPython.noop),
    ]

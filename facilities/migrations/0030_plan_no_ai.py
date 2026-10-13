from django.db import migrations, models


def turn_on_for_yours(apps, schema_editor):
    """ゆあーずの依頼（2026-10-13 の打ち合わせ：支援計画は自動作文をしない）。ほかの事業所はこれまでどおり"""
    Facility = apps.get_model('facilities', 'Facility')
    Facility.objects.filter(name='発達支援ルーム　ゆあーず').update(plan_no_ai=True)


class Migration(migrations.Migration):

    dependencies = [
        ('facilities', '0029_schedule_tools'),
    ]

    operations = [
        migrations.AddField(
            model_name='facility',
            name='plan_no_ai',
            field=models.BooleanField(default=False, verbose_name='個別支援計画：AI の下書き・整文を出さず、療育記録とアセスメントを欄に入れる'),
        ),
        migrations.RunPython(turn_on_for_yours, migrations.RunPython.noop),
    ]

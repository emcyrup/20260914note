from django.db import migrations


def turn_on_for_habilis(apps, schema_editor):
    """はびりすは請求機能を使う（2026-10-15 依頼者）。名前が「はびりす」の事業所の「請求機能を使う」をオンにする"""
    Facility = apps.get_model('facilities', 'Facility')
    Facility.objects.filter(name='はびりす').update(use_billing=True)


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0001_initial'),
        ('facilities', '0022_happiness_label'),     # 「はぴねす」「はぴりす」を「はびりす」に直したあと
    ]

    operations = [
        migrations.RunPython(turn_on_for_habilis, migrations.RunPython.noop),
    ]

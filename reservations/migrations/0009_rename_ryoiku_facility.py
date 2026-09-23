"""
事業所名の変更：「りょういく」→「発達支援ルーム　ゆあーず」（2026-09-23 依頼）。
名前が「りょういく」の事業所だけを書き換える。予約のお知らせの署名が旧名のままなら、それも直す。
英字の識別子（ブランチ ryoiku・--preset ryoiku・/opt/ryoiku など）は変えない。
"""
from django.db import migrations

OLD = 'りょういく'
NEW = '発達支援ルーム　ゆあーず'


def rename(apps, schema_editor, old=OLD, new=NEW):
    Facility = apps.get_model('facilities', 'Facility')
    ReservationSetting = apps.get_model('reservations', 'ReservationSetting')
    for facility in Facility.objects.filter(name=old):
        facility.name = new
        facility.save(update_fields=['name'])
        ReservationSetting.objects.filter(facility=facility, signature=old).update(signature=new)


def rename_back(apps, schema_editor):
    rename(apps, schema_editor, old=NEW, new=OLD)


class Migration(migrations.Migration):

    dependencies = [
        ('facilities', '0013_ryoiku'),
        ('reservations', '0008_request_scan'),
    ]

    operations = [
        migrations.RunPython(rename, rename_back),
    ]

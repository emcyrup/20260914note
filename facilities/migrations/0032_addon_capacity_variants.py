from django.db import migrations

from facilities.addon_defaults import load_default_addons


def reload_addons(apps, schema_editor):
    # 児童指導員等加配加算を配置形態・経験年数の 5 区分に分け、処遇改善加算Ⅰ〜Ⅳにコードを入れる（令和6年4月のコード表）
    load_default_addons(apps.get_model('facilities', 'AddonMaster'))


class Migration(migrations.Migration):

    dependencies = [
        ('facilities', '0031_billing_base_reward'),
    ]

    operations = [
        migrations.RunPython(reload_addons, migrations.RunPython.noop),
    ]

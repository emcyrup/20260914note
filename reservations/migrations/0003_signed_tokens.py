"""
署名のないアドレスを、署名つきに作り直す。

0002 をすでに当てた環境（作業ブランチで動かした開発用 DB など）に残っている
古い形式のアドレスを、1字違えば必ずはじける形式に入れ替える。
入れ替えたあとは、前のアドレスでは開けない（職員が新しいアドレスを配り直す）。
"""
from django.db import migrations


def resign_tokens(apps, schema_editor):
    from reservations import tokens
    Customer = apps.get_model('reservations', 'Customer')
    ReservationSetting = apps.get_model('reservations', 'ReservationSetting')
    for model, field, kind in ((Customer, 'token', tokens.CUSTOMER),
                               (ReservationSetting, 'public_token', tokens.CALENDAR)):
        for row in model.objects.all():
            if tokens.is_valid(kind, getattr(row, field)):
                continue
            setattr(row, field, tokens.make_token(kind))
            row.save(update_fields=[field])


class Migration(migrations.Migration):

    dependencies = [
        ("reservations", "0002_public_pages"),
    ]

    operations = [
        migrations.RunPython(resign_tokens, migrations.RunPython.noop),
    ]

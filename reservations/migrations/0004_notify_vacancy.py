"""満席から空きが出たときのお知らせを、設定で止められるようにする"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("reservations", "0003_booking_requests"),
    ]

    operations = [
        migrations.AddField(
            model_name="reservationsetting",
            name="notify_vacancy",
            field=models.BooleanField(
                default=True, verbose_name="満席から空きが出たら、顧客へお知らせする"
            ),
        ),
    ]

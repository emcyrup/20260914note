"""予約の受け方（自動／承認）と、台帳に未登録の方のぶんの席"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("beneficiaries", "0007_reissue_line_codes"),
        ("facilities", "0010_reservations"),
        ("reservations", "0004_notify_vacancy"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="reservationsetting",
            name="line_auto_apply",
        ),
        migrations.RemoveField(
            model_name="reservationsetting",
            name="request_auto_apply",
        ),
        migrations.AddField(
            model_name="reservation",
            name="guest_name",
            field=models.CharField(
                blank=True, max_length=100, verbose_name="お名前（台帳に未登録）"
            ),
        ),
        migrations.AddField(
            model_name="reservationsetting",
            name="booking_mode",
            field=models.CharField(
                choices=[
                    ("auto", "来た順に自動で確定する"),
                    ("approve", "職員が確認してから確定する"),
                ],
                default="auto",
                max_length=10,
                verbose_name="予約の受け方",
            ),
        ),
        migrations.AlterField(
            model_name="reservation",
            name="beneficiary",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="reservations",
                to="beneficiaries.beneficiary",
                verbose_name="利用者",
            ),
        ),
        migrations.AddConstraint(
            model_name="reservation",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    ("beneficiary__isnull", True),
                    ("status__in", ("confirmed", "waitlist")),
                    models.Q(("guest_name", ""), _negated=True),
                ),
                fields=("facility", "guest_name", "date"),
                name="uniq_active_guest_reservation",
            ),
        ),
        migrations.AddConstraint(
            model_name="reservation",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("beneficiary__isnull", False),
                    models.Q(("guest_name", ""), _negated=True),
                    _connector="OR",
                ),
                name="reservation_has_someone",
            ),
        ),
    ]

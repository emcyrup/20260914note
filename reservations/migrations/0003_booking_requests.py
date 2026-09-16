"""空き状況のページから届く申し込み（職員が確かめて予約にする）"""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("facilities", "0010_reservations"),
        ("reservations", "0002_public_pages"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="reservationsetting",
            name="public_request",
            field=models.BooleanField(
                default=True, verbose_name="空き状況のページから申し込みを受ける"
            ),
        ),
        migrations.AddField(
            model_name="reservationsetting",
            name="request_auto_apply",
            field=models.BooleanField(
                default=False, verbose_name="名前が1人に決まるとき、その場で予約にする"
            ),
        ),
        migrations.CreateModel(
            name="BookingRequest",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("date", models.DateField(verbose_name="希望日")),
                (
                    "name",
                    models.CharField(
                        max_length=100, verbose_name="お申し込みの方のお名前"
                    ),
                ),
                (
                    "kana",
                    models.CharField(
                        blank=True, max_length=100, verbose_name="ふりがな"
                    ),
                ),
                (
                    "phone",
                    models.CharField(
                        blank=True, max_length=20, verbose_name="電話番号"
                    ),
                ),
                (
                    "child_name",
                    models.CharField(
                        blank=True, max_length=100, verbose_name="お子さまのお名前"
                    ),
                ),
                (
                    "note",
                    models.CharField(blank=True, max_length=200, verbose_name="ご要望"),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "未確認"),
                            ("done", "予約にした"),
                            ("declined", "見送り"),
                        ],
                        default="pending",
                        max_length=10,
                        verbose_name="状態",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("handled_at", models.DateTimeField(blank=True, null=True)),
                (
                    "result_note",
                    models.CharField(
                        blank=True, max_length=200, verbose_name="処理の結果"
                    ),
                ),
                (
                    "customer",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="booking_requests",
                        to="reservations.customer",
                        verbose_name="顧客台帳の相手",
                    ),
                ),
                (
                    "facility",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="booking_requests",
                        to="facilities.facility",
                    ),
                ),
                (
                    "handled_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "reservation",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="reservations.reservation",
                        verbose_name="できた予約",
                    ),
                ),
            ],
            options={
                "verbose_name": "予約の申し込み",
                "verbose_name_plural": "予約の申し込み",
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(
                        fields=["facility", "status"],
                        name="reservation_facilit_186377_idx",
                    )
                ],
            },
        ),
    ]

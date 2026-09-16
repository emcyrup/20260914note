"""顧客向けの予定表（公開ページ）と、LINE からその場で反映する設定を足す"""
import reservations.tokens
from django.db import migrations, models


def fill_tokens(apps, schema_editor):
    """
    すでにある行に、重ならないアドレスを1つずつ入れる。

    アドレスは最初から署名つきで作る（`reservations/tokens.py`）。
    このマイグレーションを当てる前の環境に顧客向けのアドレスは無いので、
    古い形式を作り直す手当ては要らない。
    """
    from reservations.tokens import new_calendar_token, new_customer_token
    Customer = apps.get_model('reservations', 'Customer')
    ReservationSetting = apps.get_model('reservations', 'ReservationSetting')
    for model, field, make in ((Customer, 'token', new_customer_token),
                               (ReservationSetting, 'public_token', new_calendar_token)):
        for row in model.objects.filter(**{field: ''}):
            setattr(row, field, make())
            row.save(update_fields=[field])


class Migration(migrations.Migration):

    dependencies = [
        ("reservations", "0001_reservations"),
    ]

    operations = [

        migrations.AddField(
            model_name="customer",
            name="token",
            field=models.CharField(default="", max_length=128, verbose_name="顧客ページのアドレス"),
        ),
        migrations.AddField(
            model_name="reservationsetting",
            name="booking_from_days",
            field=models.PositiveSmallIntegerField(
                default=1,
                help_text="0 なら当日ぶんも受け付けます。",
                verbose_name="何日先から受け付けるか",
            ),
        ),
        migrations.AddField(
            model_name="reservationsetting",
            name="booking_until_days",
            field=models.PositiveSmallIntegerField(
                default=60, verbose_name="何日先まで受け付けるか"
            ),
        ),
        migrations.AddField(
            model_name="reservationsetting",
            name="group_auto_apply",
            field=models.BooleanField(
                default=True, verbose_name="スタッフのグループの投稿を反映する"
            ),
        ),
        migrations.AddField(
            model_name="reservationsetting",
            name="line_auto_apply",
            field=models.BooleanField(
                default=False, verbose_name="公式LINEの申し込みをその場で反映する"
            ),
        ),
        migrations.AddField(
            model_name="reservationsetting",
            name="public_booking",
            field=models.BooleanField(
                default=True, verbose_name="顧客が自分で予約できるようにする"
            ),
        ),
        migrations.AddField(
            model_name="reservationsetting",
            name="public_calendar",
            field=models.BooleanField(
                default=True, verbose_name="顧客向けの予定表を公開する"
            ),
        ),
        migrations.AddField(
            model_name="reservationsetting",
            name="public_token",
            field=models.CharField(default="", max_length=128, verbose_name="予定表の公開アドレス"),
        ),
        migrations.RunPython(fill_tokens, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="customer",
            name="token",
            field=models.CharField(
                default=reservations.tokens.new_customer_token, max_length=128, unique=True,
                verbose_name="顧客ページのアドレス",
            ),
        ),
        migrations.AlterField(
            model_name="reservationsetting",
            name="public_token",
            field=models.CharField(
                default=reservations.tokens.new_calendar_token, max_length=128, unique=True,
                verbose_name="予定表の公開アドレス",
            ),
        ),
        migrations.AlterField(
            model_name="reservation",
            name="source",
            field=models.CharField(
                choices=[
                    ("staff", "職員"),
                    ("line", "公式LINE"),
                    ("web", "顧客ページ"),
                    ("group", "スタッフのグループ"),
                ],
                default="staff",
                max_length=10,
                verbose_name="入口",
            ),
        ),
        migrations.AlterField(
            model_name="reservationnotice",
            name="kind",
            field=models.CharField(
                choices=[
                    ("accepted", "受付"),
                    ("waitlisted", "キャンセル待ち"),
                    ("promoted", "繰り上げ確定"),
                    ("cancelled", "取消"),
                    ("moved", "日にちの変更"),
                    ("declined", "お断り"),
                    ("reminder", "前日のお知らせ"),
                    ("vacancy", "空き枠"),
                    ("group", "予約の増減（グループ）"),
                ],
                max_length=20,
                verbose_name="種類",
            ),
        ),
    ]

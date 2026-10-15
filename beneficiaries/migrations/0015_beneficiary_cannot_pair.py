from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("beneficiaries", "0014_kana_to_hiragana")]
    operations = [
        migrations.AddField(
            model_name="beneficiary", name="cannot_pair",
            field=models.ManyToManyField(blank=True, to="beneficiaries.beneficiary", verbose_name="同じ日にできない利用者"),
        ),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("facilities", "0017_ryoiku_layout_trial")]
    operations = [
        migrations.AddField(
            model_name="facility", name="speech_words",
            field=models.TextField(blank=True, verbose_name="音声入力でよく使う言葉"),
        ),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("therapy", "0002_record_situation"),
    ]

    operations = [
        migrations.AddField(
            model_name="therapyrecord",
            name="spoken_text",
            field=models.TextField(blank=True, verbose_name="音声の文字（整える前）"),
        ),
        migrations.AddField(
            model_name="therapyrecord",
            name="ai_body",
            field=models.TextField(blank=True, verbose_name="AI が整えた文"),
        ),
    ]

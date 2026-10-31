from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('beneficiaries', '0026_document_category')]
    operations = [
        migrations.AddField(
            model_name='beneficiaryknowledge', name='extra',
            field=models.JSONField(blank=True, default=dict, verbose_name='取り出した構造'),
        ),
    ]

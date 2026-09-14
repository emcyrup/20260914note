import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('beneficiaries', '0005_add_unique_constraint_to_line_registration_code'),
        ('facilities', '0007_facility_form_set'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='AgencyMeetingReport',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateField(verbose_name='会議開催日')),
                ('start_time', models.TimeField(blank=True, null=True, verbose_name='開始')),
                ('end_time', models.TimeField(blank=True, null=True, verbose_name='終了')),
                ('place', models.CharField(blank=True, max_length=200, verbose_name='会議場所')),
                ('format', models.CharField(choices=[('face', '対面形式'), ('online', 'オンライン'), ('phone', '電話'), ('document', '文書')], default='face', max_length=10, verbose_name='会議形式')),
                ('participants', models.JSONField(blank=True, default=list, verbose_name='参加者')),
                ('purpose', models.TextField(blank=True, verbose_name='会議の目的')),
                ('result', models.TextField(blank=True, verbose_name='結果・報告内容')),
                ('opinions', models.TextField(blank=True, verbose_name='関係機関からの意見・助言')),
                ('policy', models.TextField(blank=True, verbose_name='事業所としての対応方針')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('beneficiary', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='agency_meetings', to='beneficiaries.beneficiary', verbose_name='児童')),
                ('facility', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='facilities.facility', verbose_name='施設')),
                ('recorder', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL, verbose_name='記録者')),
            ],
            options={'verbose_name': '関係機関連携報告書', 'verbose_name_plural': '関係機関連携報告書', 'ordering': ['-date', '-pk']},
        ),
        migrations.CreateModel(
            name='SpecializedSupportPlan',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('period_start', models.DateField(blank=True, null=True, verbose_name='想定支援期間（開始）')),
                ('period_end', models.DateField(blank=True, null=True, verbose_name='想定支援期間（終了）')),
                ('wishes', models.TextField(blank=True, verbose_name='本人・ご家族の希望')),
                ('rom_parts', models.JSONField(blank=True, default=list, verbose_name='関節可動域制限（部位）')),
                ('pain_site', models.CharField(blank=True, max_length=100, verbose_name='疼痛（部位）')),
                ('weak_parts', models.JSONField(blank=True, default=list, verbose_name='筋力低下（部位）')),
                ('balance', models.CharField(blank=True, choices=[('', '—'), ('yes', 'あり'), ('no', 'なし')], max_length=5, verbose_name='バランス障害')),
                ('cardio', models.CharField(blank=True, max_length=100, verbose_name='呼吸・循環機能障害')),
                ('muscle_tone', models.CharField(blank=True, choices=[('', '—'), ('high', '亢進'), ('low', '減弱')], max_length=5, verbose_name='筋緊張異常')),
                ('other_physical', models.CharField(blank=True, max_length=200, verbose_name='他')),
                ('move_rolling', models.CharField(blank=True, choices=[('', '—'), ('independent', '自立'), ('partial', '一部介助'), ('assist', '介助'), ('none', '非実施')], max_length=12, verbose_name='寝返り')),
                ('move_sitting', models.CharField(blank=True, choices=[('', '—'), ('independent', '自立'), ('partial', '一部介助'), ('assist', '介助'), ('none', '非実施')], max_length=12, verbose_name='座位保持')),
                ('move_getting_up', models.CharField(blank=True, choices=[('', '—'), ('independent', '自立'), ('partial', '一部介助'), ('assist', '介助'), ('none', '非実施')], max_length=12, verbose_name='起き上がり')),
                ('move_standing', models.CharField(blank=True, choices=[('', '—'), ('independent', '自立'), ('partial', '一部介助'), ('assist', '介助'), ('none', '非実施')], max_length=12, verbose_name='立位保持')),
                ('move_stand_up', models.CharField(blank=True, choices=[('', '—'), ('independent', '自立'), ('partial', '一部介助'), ('assist', '介助'), ('none', '非実施')], max_length=12, verbose_name='立ち上がり')),
                ('other_movement', models.CharField(blank=True, max_length=200, verbose_name='その他')),
                ('abms', models.JSONField(blank=True, default=dict, verbose_name='ABMS-C')),
                ('abms_t', models.JSONField(blank=True, default=dict, verbose_name='ABMS-C Type T')),
                ('key_areas', models.TextField(blank=True, verbose_name='重要領域')),
                ('goals', models.TextField(blank=True, verbose_name='達成目標')),
                ('support_items', models.JSONField(blank=True, default=list, verbose_name='支援内容')),
                ('support_other', models.CharField(blank=True, max_length=200, verbose_name='支援内容（その他）')),
                ('implementation', models.TextField(blank=True, verbose_name='実施内容')),
                ('explained_date', models.DateField(blank=True, null=True, verbose_name='本人・家族への説明日')),
                ('explained_to', models.CharField(blank=True, max_length=100, verbose_name='説明を受けた人')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('beneficiary', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='specialized_plans', to='beneficiaries.beneficiary', verbose_name='利用児')),
                ('explained_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL, verbose_name='説明者')),
                ('facility', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='facilities.facility', verbose_name='施設')),
            ],
            options={'verbose_name': '専門的支援実施計画書', 'verbose_name_plural': '専門的支援実施計画書', 'ordering': ['-period_start', '-pk']},
        ),
    ]

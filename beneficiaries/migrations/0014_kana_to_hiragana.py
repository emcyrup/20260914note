from django.db import migrations


def to_hiragana(s):
    return ''.join(chr(ord(ch) - 0x60) if 0x30A1 <= ord(ch) <= 0x30F6 else ch for ch in (s or '')).replace('　', ' ').strip()


def forwards(apps, schema_editor):
    """ふりがなをひらがなにそろえる（カタカナで入っていると 50 音順に並ばないため）"""
    Beneficiary = apps.get_model('beneficiaries', 'Beneficiary')
    for b in Beneficiary.objects.all().only('last_name_kana', 'first_name_kana'):
        ln, fn = to_hiragana(b.last_name_kana), to_hiragana(b.first_name_kana)
        if (ln, fn) != (b.last_name_kana, b.first_name_kana):
            b.last_name_kana, b.first_name_kana = ln, fn
            b.save(update_fields=['last_name_kana', 'first_name_kana'])


class Migration(migrations.Migration):
    dependencies = [('beneficiaries', '0013_beneficiary_knowledge')]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]

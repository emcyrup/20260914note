"""旧形式（6桁数字・無期限）の LINE 登録コードを、8文字・72時間有効の新形式に発行し直す（未連携の保護者のみ）"""
from django.db import migrations


def reissue(apps, schema_editor):
    from beneficiaries.models import _generate_line_code, _line_code_expiry
    Guardian = apps.get_model('beneficiaries', 'Guardian')
    used = set(Guardian.objects.values_list('line_registration_code', flat=True))
    for g in Guardian.objects.all():
        if g.line_linked:
            g.line_code_expires_at = None
        else:
            code = _generate_line_code()
            while code in used:
                code = _generate_line_code()
            used.add(code)
            g.line_registration_code = code
            g.line_code_expires_at = _line_code_expiry()
        g.save(update_fields=['line_registration_code', 'line_code_expires_at'])


class Migration(migrations.Migration):
    dependencies = [('beneficiaries', '0006_uuid_uploads')]
    operations = [migrations.RunPython(reissue, migrations.RunPython.noop)]

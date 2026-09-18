"""
請求まわりの共通処理（日誌からの加算の反映など）
"""
from facilities.models import AddonMaster, facility_addon_rows

from .models import BillingMatrixAddon, BillingMatrixEntry


def journal_addon_rows(facility):
    """日誌の「加算の入力」に出す個別加算（事業所で「算定する」にしたもの。設定がなければ全部）"""
    if not facility.use_billing:
        return []
    return facility_addon_rows(facility, addon_type='individual', enabled_only=True)


def applied_addon_ids(facility, beneficiary, day):
    """その日の請求セルに付いている加算IDの集合"""
    return set(BillingMatrixAddon.objects.filter(
        entry__facility=facility, entry__beneficiary=beneficiary, entry__date=day, is_applied=True,
    ).values_list('addon_id', flat=True))


def sync_record_addons(record, addon_ids):
    """
    日誌で選んだ加算を、その日の請求セル（BillingMatrixEntry / BillingMatrixAddon）に反映する。
    - 請求機能を使わない事業所では何もしない
    - セルがまだなく加算も選ばれていないときは、セルを作らない（日誌だけで請求セルを増やさない）
    - セルの状態（利用／欠席）は変えない。新しく作るときは「利用」
    戻り値: 反映した加算の件数
    """
    facility = record.facility
    if not facility.use_billing:
        return 0
    wanted = set()
    for raw in addon_ids or []:
        try:
            wanted.add(int(raw))
        except (TypeError, ValueError):
            continue
    valid = set(AddonMaster.objects.filter(pk__in=wanted, is_active=True, addon_type='individual')
                .values_list('pk', flat=True))

    entry = BillingMatrixEntry.objects.filter(
        facility=facility, beneficiary=record.beneficiary, date=record.date,
    ).first()
    if entry is None:
        if not valid:
            return 0
        entry = BillingMatrixEntry.objects.create(
            facility=facility, beneficiary=record.beneficiary, date=record.date,
            status=BillingMatrixEntry.STATUS_ATTENDED,
        )

    current = {a.addon_id: a for a in BillingMatrixAddon.objects.filter(entry=entry)}
    for addon_id in valid:
        row = current.get(addon_id)
        if row is None:
            BillingMatrixAddon.objects.create(entry=entry, addon_id=addon_id, is_applied=True)
        elif not row.is_applied:
            row.is_applied = True
            row.save(update_fields=['is_applied'])
    BillingMatrixAddon.objects.filter(entry=entry).exclude(addon_id__in=valid).delete()
    return len(valid)

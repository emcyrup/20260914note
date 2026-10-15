"""
ごみ箱：消した利用者を、いっしょに消える記録・予約・書類などごと TRASH_DAYS 日のあいだ取っておき、「戻す」で元に戻す

- trash(b, by)       : 消す前に、消えるものをすべて（Django の削除と同じ集め方で）JSON に写してから消す。ファイルは残す
- restore(entry)     : 写したものを元の番号のまま入れ直す。相手がもう無い結びつき（ほかの利用者のきょうだいなど）は外す
- purge(entry)       : ごみ箱から本当に消す（書類のファイルも）
- purge_expired()    : 期限を過ぎたものを本当に消す（ごみ箱の画面を開いたときと、利用者を消したときに呼ぶ）
"""
import datetime
import json

from django.apps import apps
from django.core import serializers
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models.deletion import Collector
from django.utils import timezone

from .models import TRASH_DAYS, Beneficiary, DeletedBeneficiary


def file_names(b):
    """その利用者を消すと使われなくなるファイル（書類・アセスメントの添付・受給者証の写真・日誌の写真）"""
    from records.models import DailyRecordPhoto
    files = [d.file for d in b.documents.all()] + [a.file for a in b.assessments.all() if a.file] \
        + [c.scanned_image for c in b.recipient_certificates.all() if c.scanned_image] \
        + [ph.photo for ph in DailyRecordPhoto.objects.filter(daily_record__beneficiary=b) if ph.photo]
    return [f.name for f in files if f and f.name]


def _collect(objs):
    """消すと消えるもの（消す順）と、消すと空になる結びつき（SET_NULL：モデル・欄・[(番号, 元の値)]）"""
    c = Collector(using='default', force_collection=True)
    c.collect(objs)
    c.sort()
    order = []
    for model, instances in c.data.items():
        order.extend(sorted(instances, key=lambda o: o.pk))
    relinks = []
    for (field, _value), groups in c.field_updates.items():
        pairs = [(o.pk, getattr(o, field.attname)) for group in groups for o in group]
        if pairs:
            relinks.append([field.model._meta.label, field.attname, pairs])
    return order, relinks


def _dump(objs):
    """JSON に写す。多対多はつなぎの表の行として別に写るので、ここでは外す（二重に入れないように）"""
    out = []
    for rec in json.loads(serializers.serialize('json', list(objs))):
        model = apps.get_model(rec['model'])
        for f in model._meta.many_to_many:
            rec['fields'].pop(f.name, None)
        out.append(rec)
    return out


@transaction.atomic
def trash(b, by=None):
    """利用者をごみ箱へ（関連する記録・予約などごと消す。戻せるように写しておく）。ごみ箱の行を返す"""
    from .views import related_counts
    counts = [list(x) for x in related_counts(b)]
    files = file_names(b)
    plans = list(b.support_plans.all())          # PROTECT なので先に写して消す
    plan_order, plan_relinks = _collect(plans) if plans else ([], [])
    plan_payload = _dump(reversed(plan_order))
    if plans:
        b.support_plans.all().delete()
    order, relinks = _collect([b])
    entry = DeletedBeneficiary.objects.create(
        facility=b.facility, beneficiary_pk=b.pk, name=b.full_name[:120], status_label=b.get_status_display(),
        deleted_by=by, payload=_dump(reversed(order)) + plan_payload, relinks=relinks + plan_relinks,
        files=files, counts=counts)
    b.delete()
    purge_expired()
    return entry


def _exists(model, pk, restored):
    return (model._meta.label, str(pk)) in restored or model._base_manager.filter(pk=pk).exists()


@transaction.atomic
def restore(entry):
    """ごみ箱から戻す。戻した利用者を返す。外した結びつきの数は entry.skipped に入れる"""
    if Beneficiary._base_manager.filter(pk=entry.beneficiary_pk).exists():
        raise ValueError('同じ番号の利用者がすでにいるため戻せません。')
    restored, skipped = set(), 0
    for rec in entry.payload:
        obj = next(serializers.deserialize('json', json.dumps([rec]), ignorenonexistent=True))
        inst, model, ok = obj.object, obj.object.__class__, True
        for f in model._meta.concrete_fields:
            if not f.is_relation or f.related_model is None:
                continue
            value = getattr(inst, f.attname)
            if value is None or _exists(f.related_model, value, restored):
                continue
            if f.null:
                setattr(inst, f.attname, None)    # 相手がもう無い（消えた職員など）：空にして戻す
            else:
                ok = False                         # 相手がもう無い（消えたほかの利用者とのきょうだいなど）：戻さない
                break
        if not ok:
            skipped += 1
            continue
        obj.save()
        restored.add((model._meta.label, str(inst.pk)))
    for label, attname, pairs in entry.relinks:
        model = apps.get_model(label)
        for pk, value in pairs:
            field = next(f for f in model._meta.concrete_fields if f.attname == attname)
            if _exists(field.related_model, value, restored):
                model._base_manager.filter(pk=pk, **{f'{attname}__isnull': True}).update(**{attname: value})
    b = Beneficiary.objects.get(pk=entry.beneficiary_pk)
    entry.delete()
    entry.skipped = skipped
    return b


def purge(entry):
    """ごみ箱から本当に消す（書類のファイルも）"""
    names = list(entry.files)
    entry.delete()
    for name in names:
        try:
            default_storage.delete(name)
        except Exception:        # ファイルが既に無いなどは無視
            pass


def purge_expired(now=None):
    """期限（TRASH_DAYS 日）を過ぎたものを本当に消す。消した件数を返す"""
    limit = (now or timezone.now()) - datetime.timedelta(days=TRASH_DAYS)
    old = list(DeletedBeneficiary.objects.filter(deleted_at__lt=limit))
    for entry in old:
        purge(entry)
    return len(old)

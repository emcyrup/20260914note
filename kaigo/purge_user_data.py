# 実行：Actions「Manage (koto)」の purge_user_data_check（数えるだけ）／purge_user_data（消す）。ファイルを ssh で送り manage.py shell に流す
# 失注した事業所の「利用者に関するデータ」を消す（2026-11-10 コト）。数だけを出す（名前は出さない）。
# 環境変数：FACILITY_ID（必須）・APPLY=1 で本当に消す（無ければ数えるだけ）・KEEP_ADMIN（残す管理者のユーザー名。既定 kyotaku）
# 消すもの：利用者（在籍・退所すべて）とその記録・書類（ファイルも）・ごみ箱、議事録、スタッフメモ、職員アカウント（KEEP_ADMIN 以外）と招待
# 残すもの：事業所と設定、管理者 KEEP_ADMIN、取引先
import os
from collections import Counter

from django.core.files.storage import default_storage
from django.db import router, transaction
from django.db.models import FileField
from django.db.models.deletion import Collector, ProtectedError

from accounts.models import StaffAccount, StaffInvitation
from beneficiaries.models import Beneficiary, DeletedBeneficiary
from facilities.models import Facility
from minutes.models import Minutes
from records.models import StaffMemo

fid = int(os.environ['FACILITY_ID'])
apply = os.environ.get('APPLY') == '1'
keep = os.environ.get('KEEP_ADMIN') or 'kyotaku'
f = Facility.objects.get(pk=fid)


def targets():
    groups = [list(Beneficiary.objects.filter(facility=f)), list(DeletedBeneficiary.objects.filter(facility=f)),
              list(Minutes.objects.filter(facility=f)), list(StaffMemo.objects.filter(facility=f)),
              list(StaffInvitation.objects.filter(facility=f)),
              list(StaffAccount.objects.filter(facility=f).exclude(username=keep))]
    return [g for g in groups if g]


def collect(groups):
    """モデルごとに集める（守られた記録＝PROTECT は、その記録を先に集める）"""
    c = Collector(using=router.db_for_write(Facility))
    pending = list(groups)
    for _ in range(100):
        if not pending:
            return c
        g = pending.pop(0)
        try:
            c.collect(g)
        except ProtectedError as e:
            by_model = {}
            for o in e.protected_objects:
                by_model.setdefault(type(o), []).append(o)
            pending = list(by_model.values()) + [g] + pending
    raise RuntimeError('守られた記録の関係が深すぎます')


def file_names(c):
    names = set()
    for model, insts in c.data.items():
        ffs = [fld for fld in model._meta.fields if isinstance(fld, FileField)]
        for o in insts:
            for fld in ffs:
                v = getattr(o, fld.attname)
                if v:
                    names.add(str(v))
    for qs in c.fast_deletes:
        ffs = [fld for fld in qs.model._meta.fields if isinstance(fld, FileField)]
        if ffs:
            for row in qs.values_list(*[fld.attname for fld in ffs]):
                names.update(str(v) for v in row if v)
    for t in DeletedBeneficiary.objects.filter(facility=f):     # ごみ箱に残っている書類のファイル
        for item in (t.files or []):
            name = item.get('name') if isinstance(item, dict) else item
            if name:
                names.add(str(name))
    return names


groups = targets()
counts = Counter()
files = set()
if groups:
    c = collect(groups)
    for model, insts in c.data.items():
        counts[str(model._meta.verbose_name)] += len(insts)
    for qs in c.fast_deletes:
        n = qs.count()
        if n:
            counts[str(qs.model._meta.verbose_name)] += n
    files = file_names(c)
existing = [n for n in files if default_storage.exists(n)]
kept_admin = StaffAccount.objects.filter(facility=f, username=keep).exists()

print(f'事業所 ID {f.pk}（型: {f.layout}）  {"消します" if apply else "数えるだけ（消さない）"}')
for label, n in sorted(counts.items(), key=lambda x: -x[1]):
    print(f'  {label}: {n}')
print(f'  書類のファイル: {len(existing)}（記録にあるが見つからないもの {len(files) - len(existing)}）')
print(f'  残す管理者 {keep}: {"あり" if kept_admin else "なし（事業所にこのユーザー名の職員がいない）"}')
if apply and groups:
    with transaction.atomic():
        collect(targets()).delete()
    gone = 0
    for n in existing:
        try:
            default_storage.delete(n)
            gone += 1
        except Exception as e:              # noqa: BLE001
            print('  ファイルを消せませんでした:', type(e).__name__)
    print(f'消しました。書類のファイル {gone} 件も消しました')
left = {'利用者': Beneficiary.objects.filter(facility=f).count(), 'ごみ箱': DeletedBeneficiary.objects.filter(facility=f).count(),
        '議事録': Minutes.objects.filter(facility=f).count(), 'スタッフメモ': StaffMemo.objects.filter(facility=f).count(),
        '職員': StaffAccount.objects.filter(facility=f).count()}
print('いまの件数：' + '・'.join(f'{k} {v}' for k, v in left.items()))

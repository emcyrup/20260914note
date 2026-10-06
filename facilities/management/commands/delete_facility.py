"""
空の事業所（施設）を消す。間違えて作った事業所を片付けるためのもの。

  python manage.py delete_facility <施設ID> --yes

利用者・予約・日誌・支援計画などのデータが 1 件でもあれば消さない（退所した利用者の削除と同じく、記録は守る）。
その事業所の職員アカウント・設定・タグは一緒に消える。
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models.deletion import ProtectedError

from facilities.models import Facility


class Command(BaseCommand):
    help = '利用者や記録の無い事業所を、職員アカウント・設定ごと消す'

    def add_arguments(self, parser):
        parser.add_argument('facility_id', type=int, help='施設 ID')
        parser.add_argument('--yes', action='store_true', help='確認なしで消す')

    def handle(self, *args, **o):
        from accounts.models import StaffAccount
        from beneficiaries.models import Beneficiary
        from reservations.models import Customer, Reservation
        try:
            f = Facility.objects.get(pk=o['facility_id'])
        except Facility.DoesNotExist:
            raise CommandError(f'施設 ID {o["facility_id"]} はありません')
        counts = {'利用者': Beneficiary.objects.filter(facility=f).count(),
                  '予約': Reservation.objects.filter(facility=f).count(),
                  '顧客': Customer.objects.filter(facility=f).count()}
        used = {k: n for k, n in counts.items() if n}
        if used:
            raise CommandError(f'「{f.name}」（ID {f.pk}）にはデータがあるので消しません：' + '、'.join(f'{k} {n} 件' for k, n in used.items()))
        staff = list(StaffAccount.objects.filter(facility=f).values_list('username', flat=True))
        if not o['yes']:
            raise CommandError(f'「{f.name}」（ID {f.pk}、職員 {len(staff)} 人）を消すには --yes を付けてください')
        try:
            with transaction.atomic():
                StaffAccount.objects.filter(facility=f).delete()
                f.delete()
        except ProtectedError as e:
            raise CommandError(f'「{f.name}」には記録が残っているので消しません：{type(e.protected_objects and next(iter(e.protected_objects))).__name__}')
        self.stdout.write(self.style.SUCCESS(f'事業所「{f.name}」（ID {o["facility_id"]}）と職員 {len(staff)} 人を消しました'))

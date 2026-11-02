"""
介護施設（やすらぎの森）の架空のサンプルデータを入れる／消す（nursing/demo.py）。
  python manage.py nursing_demo --facility 1
  python manage.py nursing_demo --facility 1 --remove
"""
from django.core.management.base import BaseCommand, CommandError

from accounts.models import StaffAccount
from beneficiaries.models import Beneficiary
from facilities.models import Facility
from nursing import demo


class Command(BaseCommand):
    help = '介護施設の架空のサンプル（利用者 12 人・検温板・介護記録・連絡帳・薬・計画書・ヒヤリハット／事故報告・送迎・人員配置）を入れる／消す'

    def add_arguments(self, parser):
        parser.add_argument('--facility', type=int, help='施設 ID（省略時は画面の型「介護施設」の事業所が 1 つならそれ）')
        parser.add_argument('--remove', action='store_true', help='サンプルだけを消す')

    def handle(self, *args, **o):
        if o.get('facility'):
            facility = Facility.objects.filter(pk=o['facility']).first()
            if facility is None:
                raise CommandError(f'施設 ID {o["facility"]} はありません')
        else:
            cares = list(Facility.objects.filter(layout=Facility.LAYOUT_CARE))
            if len(cares) != 1:
                raise CommandError('--facility で施設 ID を指定してください（介護施設の事業所が 1 つではありません）')
            facility = cares[0]
        if o.get('remove'):
            n = demo.remove(facility)
            self.stdout.write(self.style.SUCCESS(f'サンプルの利用者 {n} 人とその記録を消しました（{facility.name}）'))
            return
        user = StaffAccount.objects.filter(facility=facility, role=StaffAccount.ROLE_ADMIN).order_by('pk').first()
        n = demo.add(facility, user)
        total = Beneficiary.objects.filter(facility=facility, notes__startswith=demo.MARK).count()
        if n:
            self.stdout.write(self.style.SUCCESS(f'サンプルの利用者を {n} 人入れました（サンプルは合わせて {total} 人）。検温板・介護記録・連絡帳・薬・計画書・'
                                                 f'ヒヤリハット／事故報告・送迎・人員配置も入っています（{facility.name}）'))
        else:
            self.stdout.write(f'サンプルはすでに全部入っています（{total} 人。何もしていません）')

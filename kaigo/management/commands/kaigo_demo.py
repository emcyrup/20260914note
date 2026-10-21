"""
居宅介護支援（ケアマネ）の架空のサンプルデータを入れる・消す（kaigo/demo.py）。

  python manage.py kaigo_demo                 # 居宅介護の事業所（1つだけのとき）に入れる
  python manage.py kaigo_demo --facility 1    # 事業所を指定して入れる（もう入っていれば何もしない）
  python manage.py kaigo_demo --facility 1 --remove   # サンプルの利用者・取引先だけ消す

出力は件数だけ（利用者の名前は出さない）。
"""
from django.core.management.base import BaseCommand, CommandError

from facilities.models import Facility


class Command(BaseCommand):
    help = '居宅介護支援の架空のサンプルデータを入れる（--remove で消す）'

    def add_arguments(self, parser):
        parser.add_argument('--facility', type=int, help='事業所の ID（省略時は居宅介護の事業所が1つだけならそれ）')
        parser.add_argument('--remove', action='store_true', help='サンプルの利用者・取引先だけを消す')

    def handle(self, *args, **o):
        from kaigo import demo
        if o.get('facility'):
            facility = Facility.objects.filter(pk=o['facility']).first()
            if facility is None:
                raise CommandError(f'事業所 ID {o["facility"]} がありません。')
        else:
            found = [f for f in Facility.objects.order_by('pk') if f.is_kyotaku]
            if len(found) != 1:
                raise CommandError('居宅介護の事業所が1つではありません。--facility で ID を指定してください。')
            facility = found[0]
        if not facility.is_kyotaku:
            raise CommandError(f'事業所 ID {facility.pk} は居宅介護の事業所ではありません。')
        if o.get('remove'):
            n = demo.remove(facility)
            self.stdout.write(self.style.SUCCESS(
                f'事業所 ID {facility.pk}：サンプルを消しました（利用者 {n["people"]} 人と、その記録・取引先 {n["providers"]} 件）。ほかのデータはそのまま'))
            return
        n = demo.seed(facility)
        if n.get('skipped'):
            self.stdout.write(f'事業所 ID {facility.pk}：サンプルはもう入っています（何もしませんでした）')
            return
        self.stdout.write(self.style.SUCCESS(
            f'事業所 ID {facility.pk}：サンプルを入れました（利用者 {n["people"]} 人・支援経過 {n["logs"]} 件・ケアプラン {n["plans"]} 件・'
            f'担当者会議 {n["meetings"]} 件・アセスメント {n["assessments"]} 件・予定実績 {n["uses"]} 行・取引先 {n["providers"]} 件）'))

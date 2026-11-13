"""
介護施設の「上申書の医師」の一覧を入れる（事業所の用紙の医師を、配備の作業から初期設定するとき）。
  NURSING_DOCTORS="山田 Dr、佐藤 Dr" python manage.py nursing_doctors --facility 1
名前は引数ではなく環境変数 NURSING_DOCTORS から読む（「、」「,」・改行で区切る）。公開リポジトリの Actions のログに
実在の医師の名前を出さないため、このコマンドは人数だけを出す。
  --rename : いまの一覧の 1 人目 → 新しい 1 人目…のように、書いてある記録（上申書・所見・薬の処方した医師）の名前も付け替える
             （サンプルの医師の名前で入っている見本を、新しい医師の列に寄せる）
"""
import os
import re

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from facilities.models import Facility
from nursing.models import DoctorNote, JoushinItem, Medication, NursingSetting


class Command(BaseCommand):
    help = '上申書の医師の一覧を環境変数 NURSING_DOCTORS から入れる（名前は出さず、人数だけ出す）'

    def add_arguments(self, parser):
        parser.add_argument('--facility', type=int, help='施設 ID（省略時は画面の型「介護施設」の事業所が 1 つならそれ）')
        parser.add_argument('--rename', action='store_true', help='いまの一覧の順に、記録に書いてある医師の名前も新しい名前へ付け替える')

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
        names = [x for x in re.split(r'[、,，\n]+', os.environ.get('NURSING_DOCTORS', '')) if x.strip()]
        if not names:
            raise CommandError('環境変数 NURSING_DOCTORS が空です（「、」区切りで医師の名前を入れる）')
        setting = NursingSetting.of(facility)
        old = setting.doctor_list()
        with transaction.atomic():
            new = setting.set_doctors(names)
            setting.save()
            moved = 0
            if o.get('rename'):
                # いちど仮の名前へ移してから付け替える（新旧で同じ名前が別の位置にあっても混ざらない）
                pairs = [(a, b) for a, b in zip(old, new) if a != b]
                tmp = {a: f'\x00doctor-{i}' for i, (a, _) in enumerate(pairs)}
                for step in (lambda a, b: (a, tmp[a]), lambda a, b: (tmp[a], b)):
                    for a, b in pairs:
                        src, dst = step(a, b)
                        moved += JoushinItem.objects.filter(facility=facility, doctor=src).update(doctor=dst)
                        moved += DoctorNote.objects.filter(facility=facility, doctor=src).update(doctor=dst)
                        moved += Medication.objects.filter(facility=facility, prescriber=src).update(prescriber=dst)
                        moved += Medication.objects.filter(facility=facility, check_doctor=src).update(check_doctor=dst)
                moved //= 2
        msg = f'上申書の医師を {len(new)} 人にしました（これまで {len(old)} 人。施設 ID {facility.pk}）'
        if o.get('rename'):
            msg += f'。記録の医師の名前の付け替え {moved} 件'
        self.stdout.write(self.style.SUCCESS(msg))

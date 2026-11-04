"""
音声→AI の療育記録の長さを、記録ごとに数字だけで出す（2026-11-04 ゆあーず：長く話したのに 2 行しか出ない件の切り分け）。
  python manage.py therapy_voice_stats --days 14
「話した文字」（音声入力が聞き取った文字）が短ければ聞き取り（声の大きさ・マイク・端末）の側、
「話した文字」は長いのに「AI の文」が短ければ AI の側の問題と分かる。名前・中身は出さない。
"""
import datetime
import statistics

from django.core.management.base import BaseCommand

from facilities.models import Facility
from therapy.fidelity import _clean_len, check
from therapy.models import TherapyRecord


class Command(BaseCommand):
    help = '音声→AI の療育記録の長さ（話した文字・AI の文・保存した記録）を数字だけで出す'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=14)

    def handle(self, *args, **o):
        since = datetime.date.today() - datetime.timedelta(days=o['days'])
        for f in Facility.objects.filter(therapy_voice_direct=True):
            recs = list(TherapyRecord.objects.filter(facility=f, date__gte=since).exclude(spoken_text='').order_by('date', 'pk'))
            self.stdout.write(f'== 施設 ID {f.pk}：直近 {o["days"]} 日の音声の記録 {len(recs)} 件')
            if not recs:
                continue
            self.stdout.write('   日付   話した文字 → AI の文 → 保存した記録（字）  否定 話→AI  メモ')
            spoken_short = ai_short = staff_added = flips = 0
            lens = []
            for r in recs:
                sl, al, bl = _clean_len(r.spoken_text), _clean_len(r.ai_body), _clean_len(r.body)
                c = check(r.spoken_text, r.ai_body)
                notes = []
                if sl < 120:
                    notes.append('話した文字が短い（聞き取り）')
                    spoken_short += 1
                if c['short']:
                    notes.append('AI が短くした')
                    ai_short += 1
                if al and bl > al * 1.5 and bl - al > 80:
                    notes.append('職員が大きく書き足した')
                    staff_added += 1
                if c['flips']:
                    notes.append(f'反転のおそれ {len(c["flips"])}')
                    flips += 1
                lens.append(sl)
                self.stdout.write(f'   {r.date:%m/%d}  {sl:5d} → {al:5d} → {bl:5d}   {c["neg_spoken"]:2d}→{c["neg_body"]:2d}  {"・".join(notes)}')
            self.stdout.write(f'   まとめ：話した文字の中央値 {int(statistics.median(lens))} 字・話した文字が短い {spoken_short} 件・'
                              f'AI が短くした {ai_short} 件・職員が大きく書き足した {staff_added} 件・反転のおそれ {flips} 件')

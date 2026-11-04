"""
音声→AI の療育記録の出来を、架空の話し言葉で確かめる（2026-11-04 ゆあーず打ち合わせ）。実際の AI を使う。
  python manage.py therapy_fidelity_check            # 架空の話 6 つを 1 回ずつ
  python manage.py therapy_fidelity_check --times 3  # くり返して、ばらつきを見る
話はすべて架空（名前も架空）。出力は、作った記録と、反転・否定の数・長さの照合の結果。実在の記録は読まない。
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from therapy import fidelity
from therapy.views import CautionsSummaryView, build_record

SAMPLES = [
    ('あいさつ・ルール', 'えーと最初にあいさつをしたんですけど、あいさつができんかったです。目は合ったけど声は出なかった。'
                      'それからボール遊びをして、ルールがちょっと難しくてできなかった。でもブロックは最後まで積めて、できたって言ってた。'),
    ('活動が三つ', 'ウレタン棒で打ち合いをして、十回続いた。そのあとトランポリンは怖がって乗らなかった。'
                 'お絵かきは、なんか途中でやめちゃって、最後まではしなかったです。でもウレタン棒はもう一回やりたいって言ってた。'),
    ('方言の否定', '今日はね、お片付けはせえへんかった。声かけても全然やらへん。おやつは半分くらい食べて、残りは食べへんかった。'
                 '帰りの会は座っとったけど、手は挙げへんかったです。'),
    ('泣いた・泣かない', '負けても泣かなかった。前は泣いてたけど今日は泣かんかった。じゃんけんは三回して二回負けた。'
                      '順番は守れなかったけど、待つのはできた。'),
    ('長い振り返り', 'まず来てすぐに手洗いをして、そのあと自由遊びでブロックを高く積んで、崩れたら自分でまた積み直してた。'
                  '三回目くらいで「手伝って」って言えたので、一緒に土台を広くした。次にサーキットをして、平均台は一人で渡れたけど、'
                  'トンネルはくぐるのを嫌がって、横を通った。無理にはさせなかった。おやつのときは、袋を自分で開けようとして開かなくて、'
                  '「開けて」と言えた。そのあと絵本を読んで、途中で立ち歩いたけど、声をかけると戻ってきた。最後の帰りの会では名前を呼ばれて返事ができた。'),
    ('混ざりやすい', '粘土とパズルをした。粘土は手につくのが嫌で触らなかった。パズルは十ピースのを一人でできた。'
                   'あ、粘土はスプーンで少しだけ混ぜることはできた。パズルは二回目は途中でやめた。'),
]


class Command(BaseCommand):
    help = '架空の話し言葉で、音声→AI の療育記録の反転・否定の落ち・短すぎを確かめる（実際の AI を使う）'

    def add_arguments(self, parser):
        parser.add_argument('--times', type=int, default=1)

    def handle(self, *args, **o):
        if not settings.ANTHROPIC_API_KEY:
            raise CommandError('ANTHROPIC_API_KEY がありません')
        bad = total = retried = 0
        for _ in range(max(1, o['times'])):
            for title, text in SAMPLES:
                out, error = build_record(CautionsSummaryView.RECORD_PROMPT, '見本 太郎', None, text)
                total += 1
                if error:
                    self.stdout.write(f'== {title}: エラー {error.content.decode()[:200]}')
                    bad += 1
                    continue
                r = out['result']
                retried += out['retried']
                ok = not fidelity.has_problem(r)
                bad += not ok
                self.stdout.write(f'== {title}: {"OK" if ok else "要確認"}（{out["sec"]:.1f} 秒・作り直し {"あり" if out["retried"] else "なし"}・'
                                  f'話 {r["spoken_len"]} 字 → 記録 {r["body_len"]} 字・否定 {r["neg_spoken"]} → {r["neg_body"]}）')
                for s in r['flips']:
                    self.stdout.write(f'   反転のおそれ: {s}')
                self.stdout.write('   やったこと: ' + '、'.join(out['data']['activities']))
                for line in out['data']['body'].splitlines():
                    self.stdout.write('   ' + line)
        self.stdout.write(f'合計 {total} 件：要確認 {bad} 件・作り直し {retried} 件')

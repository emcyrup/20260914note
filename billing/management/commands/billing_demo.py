"""
請求の動作確認用の架空の利用者（既定 40 人）を入れる／消す（billing/demo.py）。
  python manage.py billing_demo --facility 2 [--month 2026-10] [--count 40]
  python manage.py billing_demo --facility 2 --remove
出力は人数・件数だけ（公開リポジトリの Actions のログに出るため、名前は出さない）
"""
import datetime

from django.core.management.base import BaseCommand, CommandError

from facilities.models import Facility


class Command(BaseCommand):
    help = '請求の動作確認用の架空の利用者（既定 40 人）と 2 か月ぶんの出欠・加算・上限額管理を入れる／消す'

    def add_arguments(self, parser):
        parser.add_argument('--facility', type=int, required=True, help='施設 ID')
        parser.add_argument('--month', default='', help='「今月」として扱う月（YYYY-MM）。空なら今日の月')
        parser.add_argument('--count', type=int, default=40, help='人数（1〜60）')
        parser.add_argument('--remove', action='store_true', help='入れた架空の利用者を消す')

    def handle(self, *args, **opts):
        from billing import demo
        facility = Facility.objects.filter(pk=opts['facility']).first()
        if facility is None:
            raise CommandError(f'施設 ID {opts["facility"]} がありません')
        if opts['remove']:
            n = demo.remove(facility)
            self.stdout.write(f'施設 {facility.pk}：請求デモの利用者 {n} 人を関連の記録ごと消しました（施設の設定はそのまま）')
            return
        if not facility.use_billing:
            raise CommandError(f'施設 {facility.pk} は請求機能を使わない設定です（施設設定 → 使う機能）')
        today = datetime.date.today()
        try:
            year, month = (int(x) for x in opts['month'].split('-')) if opts['month'] else (today.year, today.month)
            datetime.date(year, month, 1)
        except ValueError:
            raise CommandError('--month は YYYY-MM で入れてください')
        if not 1 <= opts['count'] <= 60:
            raise CommandError('--count は 1〜60 にしてください')
        out = demo.seed(facility, year, month, count=opts['count'], today=today)
        if out.get('skipped'):
            self.stdout.write(f'施設 {facility.pk}：請求デモの利用者はもう入っています（入れ直すときは先に --remove）')
            return
        self.stdout.write(f'施設 {facility.pk}：請求デモの利用者 {out["people"]} 人、来所・欠席 {out["entries"]} 件、'
                          f'これからの予定 {out["planned"]} 件、加算 {out["addons"]} 件を入れました（{year}年{month}月とその前月）')
        for line in out['settings']:
            self.stdout.write(f'  施設の設定：{line}')

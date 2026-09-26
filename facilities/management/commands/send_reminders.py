"""
期限のお知らせをメールで送る（受給者証の有効期限・個別支援計画の計画期間・モニタリングの期日）。

毎朝 1 回、cron から実行する（例：毎日 8:00）:
    0 8 * * * cd ~/michinoteyours && ~/env/bin/python manage.py send_reminders >> ~/michinoteyours/reminders.log 2>&1

送るのは、期限の 30・14・7・3・1・0 日前のものがある日と、期限切れがあるときの月曜だけ（毎日は送らない）。
宛先は、その事業所の管理者・児発管のメールアドレスと、施設設定の代表者メール。
メールの設定（EMAIL_HOST など）が .env に無ければ送れない（画面のお知らせだけ使う）。
"""
from django.conf import settings
from django.core.management.base import BaseCommand

from facilities.models import Facility
from facilities import reminders


class Command(BaseCommand):
    help = '期限のお知らせ（受給者証・計画期間・モニタリング）をメールで送る'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=reminders.DEFAULT_DAYS, help='何日先までを対象にするか（既定 30）')
        parser.add_argument('--facility', type=int, help='施設 ID（省略時は全施設）')
        parser.add_argument('--force', action='store_true', help='節目の日でなくても送る')
        parser.add_argument('--dry-run', action='store_true', help='送らずに内容だけ表示する')
        parser.add_argument('--base-url', default='', help='メールに載せるリンクの先頭（例 https://michinote.yours.ai-labo.cloud）')

    def handle(self, *args, **options):
        if not settings.EMAIL_HOST and not options['dry_run'] and 'console' not in settings.EMAIL_BACKEND \
                and 'locmem' not in settings.EMAIL_BACKEND:
            self.stdout.write(self.style.WARNING('EMAIL_HOST が設定されていないため送れません（.env に EMAIL_* を入れてください）'))
            return
        qs = Facility.objects.order_by('pk')
        if options['facility']:
            qs = qs.filter(pk=options['facility'])
        for facility in qs:
            sent, n, to, note = reminders.send_for(facility, days=options['days'], force=options['force'],
                                                  dry_run=options['dry_run'], base_url=options['base_url'])
            if sent:
                self.stdout.write(self.style.SUCCESS(
                    f'{facility.name}: {"（お試し）" if options["dry_run"] else ""}{n} 件を {", ".join(to)} へ送りました — {note}'))
            else:
                self.stdout.write(f'{facility.name}: 送りません（{n} 件）— {note}')

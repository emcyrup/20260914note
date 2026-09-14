"""
開発向けユーザーの付与・解除。

    python manage.py set_developer <ログインID>          # 付与
    python manage.py set_developer <ログインID> --off    # 解除

開発向けユーザーはすべての事業所にアクセスでき、サイドバー上部で事業所を切り替えて動作確認できます。
"""
from django.core.management.base import BaseCommand, CommandError

from accounts.models import StaffAccount


class Command(BaseCommand):
    help = '開発向けユーザー（複数事業所へアクセスし、画面上で事業所を切り替えられる）を付与・解除する'

    def add_arguments(self, parser):
        parser.add_argument('username', help='ログインID')
        parser.add_argument('--off', action='store_true', help='開発向けユーザーを解除する')

    def handle(self, *args, **options):
        try:
            u = StaffAccount.objects.get(username=options['username'])
        except StaffAccount.DoesNotExist:
            raise CommandError(f'ログインID「{options["username"]}」の職員が見つかりません')
        u.is_developer = not options['off']
        u.save(update_fields=['is_developer'])
        if u.is_developer:
            self.stdout.write(self.style.SUCCESS(
                f'{u} を開発向けユーザーにしました。ログイン後、サイドバー上部で事業所を切り替えられます。'))
        else:
            self.stdout.write(self.style.SUCCESS(f'{u} の開発向けユーザーを解除しました。'))

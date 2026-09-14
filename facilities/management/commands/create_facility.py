"""
別の事業所（施設）を同じアプリ内に作る。

  python manage.py create_facility "あおば教室" --admin aoba_admin --password '（初期パスワード）'
  python manage.py create_facility "あおば教室" --admin aoba_admin --password '…' --copy-settings-from 1 --demo

- 施設を作成し、標準の活動タグ・支援内容タグを入れる
- --admin を付けると、その施設の管理者アカウント（権限区分＝管理者）を作る
- --copy-settings-from <施設ID> で、呼び方・配色・使う機能・日誌の項目順・単位数などの設定を既存施設からコピーする
- --demo を付けると架空のサンプルデータ（seed_demo）も投入する
記録（利用者・日誌・計画・請求）は施設ごとに完全に分かれる。職員は1つの施設にだけ所属する。
"""
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import StaffAccount
from facilities.models import Facility
from facilities.services import COPY_FIELDS, create_facility  # noqa: F401  (COPY_FIELDS は互換のため公開)


class Command(BaseCommand):
    help = '別の事業所（施設）を作成し、標準タグと管理者アカウントを用意する'

    def add_arguments(self, parser):
        parser.add_argument('name', help='施設名')
        parser.add_argument('--office-number', default='', help='事業所番号（任意）')
        parser.add_argument('--admin', metavar='ユーザー名', help='この施設の管理者アカウントを作る')
        parser.add_argument('--password', help='--admin の初期パスワード（省略時は入力を求める）')
        parser.add_argument('--display-name', default='', help='管理者の表示名（任意）')
        parser.add_argument('--copy-settings-from', type=int, metavar='施設ID', help='設定をコピーする既存施設の ID')
        parser.add_argument('--no-default-tags', action='store_true', help='標準の活動タグ・支援内容タグを入れない')
        parser.add_argument('--demo', action='store_true', help='架空のサンプルデータ（seed_demo）も投入する')

    def handle(self, *args, **o):
        name = o['name'].strip()
        if not name:
            raise CommandError('施設名を指定してください')
        if Facility.objects.filter(name=name).exists():
            raise CommandError(f'施設「{name}」はすでにあります（一覧: {self._list()}）')
        if o.get('admin') and StaffAccount.objects.filter(username=o['admin']).exists():
            raise CommandError(f'ユーザー名「{o["admin"]}」はすでに使われています')

        password = o.get('password')
        if o.get('admin') and not password:
            import getpass
            password = getpass.getpass(f'{o["admin"]} の初期パスワード: ')
            if len(password) < 8:
                raise CommandError('パスワードは8文字以上にしてください')

        with transaction.atomic():
            src = None
            if o.get('copy_settings_from'):
                try:
                    src = Facility.objects.get(pk=o['copy_settings_from'])
                except Facility.DoesNotExist:
                    raise CommandError(f'施設ID {o["copy_settings_from"]} は存在しません（一覧: {self._list()}）')
            facility = create_facility(name, o.get('office_number') or '', copy_from=src,
                                       default_tags=not o.get('no_default_tags'))
            if src is not None:
                self.stdout.write(f'設定を「{src.name}」からコピーしました（呼び方・配色・使う機能・日誌の項目・単位数）')
            self.stdout.write(self.style.SUCCESS(f'施設「{facility.name}」を作成しました（ID {facility.pk}）'))
            if not o.get('no_default_tags'):
                self.stdout.write('標準の活動タグ・支援内容タグを入れました')

            if o.get('admin'):
                StaffAccount.objects.create_user(
                    username=o['admin'], password=password, facility=facility,
                    role=StaffAccount.ROLE_ADMIN, display_name=o.get('display_name') or '',
                )
                self.stdout.write(self.style.SUCCESS(f'管理者アカウント「{o["admin"]}」を作成しました（初回ログイン後にパスワードを変えてください）'))

        if o.get('demo'):
            call_command('seed_demo', '--facility', str(facility.pk), stdout=self.stdout)

        self.stdout.write('次の手順: この管理者でログイン → 施設設定で事業所番号・呼び方・使う機能を確認 → 職員・運用管理で職員を追加')

    @staticmethod
    def _list():
        return ', '.join(f'{f.pk}:{f.name}' for f in Facility.objects.order_by('pk')) or 'なし'

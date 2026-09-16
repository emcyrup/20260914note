"""
顧客向けページのアドレスを作り直す。

使うとき：
- `SECRET_KEY` を変えた（署名が合わなくなり、配ったアドレスが開けなくなる）
- アドレスが外に漏れたおそれがある

作り直すと前のアドレスは開けない。新しいアドレスを配り直すこと。
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.urls import reverse

from facilities.models import Facility
from reservations import tokens
from reservations.models import Customer
from reservations.services import get_setting


class Command(BaseCommand):
    help = '顧客向けページのアドレス（署名つき）を作り直す'

    def add_arguments(self, parser):
        parser.add_argument('--facility', type=int, help='事業所ID（省略すると全事業所）')
        parser.add_argument('--calendar', action='store_true', help='空き状況ページのアドレスだけ')
        parser.add_argument('--customers', action='store_true', help='顧客ページのアドレスだけ')
        parser.add_argument('--only-broken', action='store_true',
                            help='署名が合わなくなったものだけ作り直す')

    def handle(self, *args, **options):
        facilities = Facility.objects.all()
        if options['facility']:
            facilities = facilities.filter(pk=options['facility'])
            if not facilities.exists():
                raise CommandError(f'事業所ID {options["facility"]} が見つかりません。')
        both = not (options['calendar'] or options['customers'])
        base = (settings.RESERVATION_SITE_URL or '').rstrip('/')
        only_broken = options['only_broken']

        for facility in facilities:
            self.stdout.write(self.style.MIGRATE_HEADING(f'■ {facility.name}（ID {facility.pk}）'))
            if both or options['calendar']:
                setting = get_setting(facility)
                if not (only_broken and tokens.is_valid(tokens.CALENDAR, setting.public_token)):
                    setting.reissue_public_token()
                    setting.save(update_fields=['public_token', 'updated_at'])
                    self.stdout.write('  空き状況：' + base
                                      + reverse('reservations_public:calendar', args=[setting.public_token]))
            if both or options['customers']:
                for customer in Customer.objects.filter(facility=facility):
                    if only_broken and tokens.is_valid(tokens.CUSTOMER, customer.token):
                        continue
                    customer.reissue_token()
                    customer.save(update_fields=['token', 'updated_at'])
                    self.stdout.write(f'  {customer.name}：' + base
                                      + reverse('reservations_public:customer', args=[customer.token]))
        self.stdout.write(self.style.SUCCESS('作り直しました。前のアドレスは開けません。配り直してください。'))

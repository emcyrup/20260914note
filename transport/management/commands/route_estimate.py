"""
住所から住所までの車の所要時間を 1 組求めて表示する（2026-11-09）。サーバーから経路のサービスに届くかの確認用。
  python manage.py route_estimate                       # 公共の住所 2 か所（京都市伏見区役所 → 京都駅）で試す
  python manage.py route_estimate "出発の住所" "到着の住所"
  python manage.py route_estimate --no-cache ...        # 控えを使わず問い合わせる
出力は分・km・出どころだけ（個人の住所を引数にしても、ログには住所を出さない --quiet）
"""
from django.core.management.base import BaseCommand

from transport import routing
from transport.models import RouteEstimate

DEFAULT_PAIR = ('京都市伏見区鷹匠町39-2', '京都市下京区東塩小路町')     # 伏見区役所 → 京都駅（公共の住所）


class Command(BaseCommand):
    help = '住所から住所までの車の所要時間を求める（経路のサービスへの疎通の確認）'

    def add_arguments(self, parser):
        parser.add_argument('origin', nargs='?', default=DEFAULT_PAIR[0])
        parser.add_argument('destination', nargs='?', default=DEFAULT_PAIR[1])
        parser.add_argument('--no-cache', action='store_true', help='控えを消してから問い合わせる')
        parser.add_argument('--quiet', action='store_true', help='住所を出力しない')

    def handle(self, *args, **options):
        origin, destination = routing.normalize_address(options['origin']), routing.normalize_address(options['destination'])
        if options['no_cache']:
            RouteEstimate.objects.filter(origin=origin, destination=destination).delete()
        if not options['quiet']:
            self.stdout.write(f'{origin} → {destination}')
        from django.conf import settings
        self.stdout.write('経路のサービス: ' + ('Google Distance Matrix（GOOGLE_MAPS_API_KEY あり）' if getattr(settings, 'GOOGLE_MAPS_API_KEY', '') else '国土地理院の住所検索 + OSRM'))
        a = routing.geocode(origin)
        b = routing.geocode(destination)
        self.stdout.write(f'座標: 出発 {"OK" if a else "見つからない"} / 到着 {"OK" if b else "見つからない"}')
        est = routing.estimate(origin, destination)
        if est is None:
            self.stderr.write('所要時間を求められませんでした（住所が見つからないか、経路のサービスに届いていません）')
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS(f'所要時間: {est.label}（{est.source_label}）'))

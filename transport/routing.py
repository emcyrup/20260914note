"""
住所から住所までの車の所要時間（2026-11-09 オウル）。配車表で「学校の住所→施設」「施設→自宅」のかかる時間を出す。

順番：
  1. GOOGLE_MAPS_API_KEY があれば Google Distance Matrix（住所のまま渡せる。有料・要キー）
  2. 無ければ 国土地理院の住所検索（無料・キー不要）で座標にし、OSRM の公開サーバーで車の経路を引く
  3. 経路が引けなければ、直線距離 × 1.3 を時速 25km で走った目安（source='direct'）
一度求めた組は RouteEstimate に控え、次からは外部に問い合わせない（住所の座標も GeoPoint に控える）。
失敗（住所が見つからない・通信できない）は None を返し、控えない。
"""
import logging
import math

import requests
from django.conf import settings

from .models import GeoPoint, RouteEstimate

log = logging.getLogger(__name__)

GSI_URL = 'https://msearch.gsi.go.jp/address-search/AddressSearch'
OSRM_URL = 'https://router.project-osrm.org/route/v1/driving/'
GOOGLE_URL = 'https://maps.googleapis.com/maps/api/distancematrix/json'
TIMEOUT = 8
USER_AGENT = 'michinote-transport/1.0'
DIRECT_FACTOR = 1.3        # 直線距離→道のりの目安
DIRECT_KMH = 25            # 市街地の目安の速さ


class RoutingError(Exception):
    pass


def normalize_address(text):
    """住所の正規化：前後の空白・〒と郵便番号・全角空白を外す（「〒612-0000 京都市…」→「京都市…」）"""
    s = (text or '').replace('　', ' ').strip()
    if s.startswith('〒'):
        parts = s[1:].split(None, 1)
        s = parts[1] if len(parts) == 2 else ''
    return s.strip()[:200]


def _get(url, params):
    """HTTP GET → JSON（テストで差し替える）"""
    res = requests.get(url, params=params, timeout=TIMEOUT, headers={'User-Agent': USER_AGENT})
    res.raise_for_status()
    return res.json()


def geocode(address):
    """住所 → (lat, lon)。控えがあればそれ。見つからなければ None"""
    address = normalize_address(address)
    if not address:
        return None
    hit = GeoPoint.objects.filter(address=address).first()
    if hit:
        return hit.lat, hit.lon
    try:
        data = _get(GSI_URL, {'q': address})
    except Exception as e:      # noqa: BLE001  通信の失敗は None（控えない）
        log.warning('geocode failed: %s', e)
        return None
    if not data:
        return None
    try:
        lon, lat = data[0]['geometry']['coordinates']
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    GeoPoint.objects.get_or_create(address=address, defaults={'lat': lat, 'lon': lon, 'source': 'gsi'})
    return lat, lon


def _haversine_m(a, b):
    r = 6371000.0
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _google(origin, destination):
    key = getattr(settings, 'GOOGLE_MAPS_API_KEY', '')
    if not key:
        return None
    data = _get(GOOGLE_URL, {'origins': origin, 'destinations': destination, 'key': key, 'language': 'ja', 'region': 'jp', 'mode': 'driving'})
    try:
        el = data['rows'][0]['elements'][0]
        if el.get('status') != 'OK':
            return None
        return {'minutes': max(1, round(el['duration']['value'] / 60)), 'distance_m': int(el['distance']['value']), 'source': 'google'}
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _osrm(a, b):
    data = _get(OSRM_URL + f'{a[1]},{a[0]};{b[1]},{b[0]}', {'overview': 'false'})
    try:
        route = data['routes'][0]
        return {'minutes': max(1, round(route['duration'] / 60)), 'distance_m': int(route['distance']), 'source': 'osrm'}
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _direct(a, b):
    m = _haversine_m(a, b) * DIRECT_FACTOR
    return {'minutes': max(1, round(m / 1000 / DIRECT_KMH * 60)), 'distance_m': int(m), 'source': 'direct'}


def cached(origin, destination):
    """控えだけを見る（画面の表示用。外部には問い合わせない）"""
    origin, destination = normalize_address(origin), normalize_address(destination)
    if not origin or not destination:
        return None
    return RouteEstimate.objects.filter(origin=origin, destination=destination).first()


def estimate(origin, destination):
    """所要時間を求めて控える。RouteEstimate か None"""
    origin, destination = normalize_address(origin), normalize_address(destination)
    if not origin or not destination:
        return None
    if origin == destination:
        return None
    hit = RouteEstimate.objects.filter(origin=origin, destination=destination).first()
    if hit:
        return hit
    result = None
    try:
        result = _google(origin, destination)
    except Exception as e:      # noqa: BLE001
        log.warning('google distance matrix failed: %s', e)
    if result is None:
        a, b = geocode(origin), geocode(destination)
        if a is None or b is None:
            return None
        try:
            result = _osrm(a, b)
        except Exception as e:  # noqa: BLE001
            log.warning('osrm failed: %s', e)
            result = None
        if result is None:
            result = _direct(a, b)
    obj, _created = RouteEstimate.objects.get_or_create(origin=origin, destination=destination, defaults=result)
    return obj


def estimate_many(pairs, limit=40):
    """(出発, 到着) の組をまとめて。戻り値 (求めた数, 求められなかった数)。limit 件を超える分は次回に"""
    done = failed = 0
    seen = set()
    for origin, destination in pairs:
        key = (normalize_address(origin), normalize_address(destination))
        if key in seen or not key[0] or not key[1]:
            continue
        seen.add(key)
        if len(seen) > limit:
            break
        if cached(*key):
            continue
        if estimate(*key):
            done += 1
        else:
            failed += 1
    return done, failed

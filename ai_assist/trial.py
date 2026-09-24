"""
「お試し」の機能（記録の AI・支援計画の AI）の回数制限。

事業所の trial_ai_limit（0 なら制限なし）まで使え、使った回数は trial_ai_used に数える。
療育記録・議事録・予約の AI（基本機能）は数えない。
"""
from django.db.models import F

TRIAL_FEATURES = '記録と支援計画の AI'


def status(facility):
    """{'limit', 'used', 'remaining'}。制限なしなら limit=0・remaining=None"""
    limit = getattr(facility, 'trial_ai_limit', 0) or 0
    used = getattr(facility, 'trial_ai_used', 0) or 0
    return {'limit': limit, 'used': used, 'remaining': max(limit - used, 0) if limit else None}


def check(facility):
    """使えるなら None、使い切っていれば画面に出す文"""
    st = status(facility)
    if st['limit'] and st['used'] >= st['limit']:
        return (f'お試しの AI（{TRIAL_FEATURES}）は {st["limit"]} 回まで使えます。回数を使い切りました。'
                '続けて使うには、システムの担当者にご連絡ください。')
    return None


def use(facility):
    """1回使ったと数える（同時に押されても数え漏れないよう、データベース側で足す）"""
    if not getattr(facility, 'trial_ai_limit', 0):
        return
    type(facility).objects.filter(pk=facility.pk).update(trial_ai_used=F('trial_ai_used') + 1)
    facility.trial_ai_used = (facility.trial_ai_used or 0) + 1

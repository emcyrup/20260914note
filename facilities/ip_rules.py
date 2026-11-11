"""
事業所ごとの「使える場所」（IP アドレスの制限。2026-11-11 やすらぎの森の打ち合わせ：自宅からのアクセス・ID の漏れへの心配）。
- Facility.allowed_ips：1 行に 1 つ（192.0.2.10・192.0.2.0/24・IPv6 も可）。# から後ろはメモ。空なら制限しない
- 制限しない人：スーパーユーザー・開発向けユーザー、「管理者は場所を制限しない」に印があるときの管理者
- 締め出し防止：保存する人のいまの場所が入っていなければ保存しない。困ったときは Manage の ip_restrict_clear で消す
"""
import ipaddress


def parse(text):
    """(ネットワークのリスト, 読めなかった行のリスト)"""
    nets, bad = [], []
    for raw in (text or '').splitlines():
        line = raw.split('#', 1)[0].strip().replace('　', '')
        if not line:
            continue
        try:
            nets.append(ipaddress.ip_network(line, strict=False))
        except ValueError:
            bad.append(raw.strip())
    return nets, bad


def ip_in(nets, ip):
    try:
        addr = ipaddress.ip_address((ip or '').strip())
    except ValueError:
        return False
    if getattr(addr, 'ipv4_mapped', None):
        addr = addr.ipv4_mapped
    return any(addr.version == n.version and addr in n for n in nets)


def exempt(user, facility):
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    if user.is_superuser or getattr(user, 'is_developer', False):
        return True
    return bool(facility.ip_admin_exempt and getattr(user, 'is_admin', False))


def allowed(facility, user, ip):
    """この人がこの場所（ip）から使えるか"""
    if facility is None or not (facility.allowed_ips or '').strip() or exempt(user, facility):
        return True
    nets, _bad = parse(facility.allowed_ips)
    if not nets:
        return True
    return ip_in(nets, ip)

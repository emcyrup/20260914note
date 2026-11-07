"""
請求の準備チェック：請求書・上限管理・請求集計を出す前に足りない設定を一覧にする（請求マトリックスの上に出す）。
"""
from django.urls import reverse

from beneficiaries.models import Beneficiary, RecipientCertificate
from facilities.models import AddonMaster, FacilityAddonSetting

from .copayment import month_range


def issues(facility, month_start):
    """足りないものの一覧 [{'text', 'url'}]。空なら準備できている"""
    out = []
    settings_url = reverse('facilities:settings') + '#tab-basic'

    def add(text, url=settings_url):
        out.append({'text': text, 'url': url})

    if not facility.office_number:
        add('施設設定に事業所番号が入っていません（請求書・上限管理結果票に印字され、当施設の判定にも使います）')
    if facility.region_category == 'other':
        add(f'施設設定の地域区分が「その他（1 単位 10 円）」のままです（いまの単価 {facility.unit_price} 円）')
    if not facility.capacity_class and not facility.base_unit_count:
        add('施設設定の基本報酬（定員区分、または 1 日あたりの単位数）が入っていません。空のままだと利用者負担は上限月額そのものになります')
    if not facility.capacity_class and facility.base_unit_count and not (facility.base_code_1 or facility.base_code_2 or facility.base_code_3):
        add('基本報酬のサービスコード（区分1〜3）が入っていません（定員区分を選べばコード表から自動で決まります）')
    if not facility.use_schedule:
        add('「来所予定（出欠）」を使う設定になっていません。請求の日数は出欠の記録から数えます')
    on = [label for flag, label in ((facility.reduce_self_eval, '自己評価結果等未公表減算'), (facility.reduce_program, '支援プログラム未公表減算'),
                                    (facility.reduce_hours in ('85', '70'), f'開所時間減算（{facility.reduce_hours}%）')) if flag]
    if on:
        add('施設設定で減算が付いています：' + '・'.join(on) + '。当てはまらない月になったら外してください')
    # 重心型：定員が無いと重身の基本報酬・看護職員加配加算の単位数が決まらない。重心の印がある利用者に重身の単位数が無い
    if facility.is_severe_facility and not facility.severe_capacity and not facility.base_unit_count_severe:
        add('重心型の事業所ですが、施設設定の「重心型の定員」も「重身の基本報酬単位数」も入っていません（重心の利用者の基本報酬が 0 になります）')
    severe_people = [b for b in Beneficiary.objects.filter(facility=facility, status=Beneficiary.STATUS_ACTIVE, is_severe=True)]
    if severe_people and not facility.severe_units_apply(severe_people[0]):
        add(f'重症心身障害児の印がある利用者が {len(severe_people)} 名いますが、重身の基本報酬（重心型の定員か単位数）が入っていません')
    addons_url = reverse('facilities:settings') + '#tab-billing'
    from facilities.models import facility_addon_rows
    nurse = [r for r in facility_addon_rows(facility, addon_type='facility', enabled_only=True) if r.name.startswith('看護職員加配加算') and not r.unit_count]
    if nurse:
        add('「算定する」にした看護職員加配加算の単位数が 0 です（施設設定で「主として重症心身障害児」と「重心型の定員」を選ぶか、加算設定に単位数を入れてください）', addons_url)
    if not AddonMaster.objects.filter(is_active=True).exists():
        add('加算マスタが空です（開発向けユーザーが「標準加算マスタを読み込む」を実行）', addons_url)
    elif not FacilityAddonSetting.objects.filter(facility=facility, is_enabled=True).exists():
        add('施設設定の加算で「算定する」加算が 1 つもありません（日誌の加算の入力は全部出ます）', addons_url)

    active = list(Beneficiary.objects.filter(facility=facility, status=Beneficiary.STATUS_ACTIVE))
    if active:
        first, last = month_range(month_start.year, month_start.month)
        covered = set(RecipientCertificate.objects.filter(beneficiary__in=active, valid_from__lte=last, valid_until__gte=first)
                      .values_list('beneficiary_id', flat=True))
        missing = [b for b in active if b.pk not in covered]
        if missing:
            add(f'{month_start.month}月に有効な受給者証が無い利用者 {len(missing)} 名（{_names(missing)}）', reverse('beneficiaries:list'))
    for text in certificate_issues(facility, month_start.year, month_start.month):
        add(text, reverse('beneficiaries:list'))
    return out


def _names(people, n=5):
    return '・'.join(b.full_name for b in people[:n]) + ('…' if len(people) > n else '')


def certificate_issues(facility, year, month):
    """
    受給者証と利用日の突き合わせ（請求マトリックスの「来所」の日）：
    - 受給者証の有効期間の外の日に来所がある（その日は請求できない）
    - 来所の日数が受給者証の支給量（日／月）を超えている
    - 月の途中で負担上限月額の違う受給者証に替わった（請求ではあとから有効になったほうを使う。確かめる）
    戻り値は文の一覧
    """
    from .models import BillingMatrixEntry
    days = {}
    for bid, d in BillingMatrixEntry.objects.filter(facility=facility, date__year=year, date__month=month,
                                                    status='attended').values_list('beneficiary_id', 'date'):
        days.setdefault(bid, []).append(d)
    if not days:
        return []
    first, last = month_range(year, month)
    certs = {}
    for c in RecipientCertificate.objects.filter(beneficiary_id__in=days, valid_from__lte=last, valid_until__gte=first).order_by('valid_from'):
        certs.setdefault(c.beneficiary_id, []).append(c)
    outside, over, cap_changed = [], [], []
    for b in Beneficiary.objects.filter(pk__in=days).order_by('last_name_kana', 'first_name_kana'):
        cs = certs.get(b.pk, [])
        if not cs:
            continue      # 受給者証が無い利用者は上の「有効な受給者証が無い」で知らせる
        bad = sorted(d for d in days[b.pk] if not any(c.valid_from <= d <= c.valid_until for c in cs))
        if bad:
            outside.append(f'{b.full_name}（' + '・'.join(f'{d.day}日' for d in bad[:5]) + ('…' if len(bad) > 5 else '') + '）')
        granted = cs[-1].granted_days
        if granted and len(days[b.pk]) > granted:
            over.append(f'{b.full_name}（{len(days[b.pk])} 日／支給量 {granted} 日）')
        if len({c.monthly_cap for c in cs}) > 1:
            cap_changed.append(f'{b.full_name}（' + ' → '.join(f'{c.monthly_cap:,} 円' for c in cs) + '）')
    out = []
    if outside:
        out.append(f'受給者証の有効期間の外の日に来所がある利用者 {len(outside)} 名：' + '、'.join(outside[:5])
                   + ('…' if len(outside) > 5 else '') + '。その日は請求できません。受給者証の期間か出欠を確かめてください')
    if over:
        out.append(f'来所の日数が支給量を超えている利用者 {len(over)} 名：' + '、'.join(over[:5]) + ('…' if len(over) > 5 else ''))
    if cap_changed:
        out.append(f'月の途中で負担上限月額が変わった利用者 {len(cap_changed)} 名：' + '、'.join(cap_changed[:5])
                   + ('…' if len(cap_changed) > 5 else '') + '。請求ではあとから有効になった受給者証の額を使います（市町村の扱いを確かめてください）')
    return out


def month_diff(facility, year, month):
    """
    請求前の差分チェック：先月のセルに控えた基本報酬（保存したときのサービスコード・単位数）と、
    いまの設定で計算した今月の基本報酬が違う利用者を知らせる（定員区分・医療的ケア区分・重心の印・
    時間区分の初期値を変えたときの誤適用に気づくため）。控えの無い先月のセルは、いまの値で控えを作る。
    戻り値 [{'text', 'url'}]
    """
    from .models import BillingMatrixEntry
    from .views import base_for_entry, snapshot_entry
    py, pm = (year - 1, 12) if month == 1 else (year, month - 1)
    changed = []
    for b in Beneficiary.objects.filter(facility=facility, status=Beneficiary.STATUS_ACTIVE).order_by('last_name_kana', 'first_name_kana'):
        cur = list(BillingMatrixEntry.objects.filter(facility=facility, beneficiary=b, date__year=year, date__month=month, status='attended'))
        prev = list(BillingMatrixEntry.objects.filter(facility=facility, beneficiary=b, date__year=py, date__month=pm, status='attended'))
        if not cur or not prev:
            continue
        before = {}
        for e in prev:
            if e.base_units is None and not e.base_code:
                snapshot_entry(e, facility, b)
            k = e.time_kubun if e.time_kubun in (1, 2, 3) else None
            before.setdefault(k, (e.base_code, e.base_units))
        now = {}
        for e in cur:
            k = e.time_kubun if e.time_kubun in (1, 2, 3) else None
            now.setdefault(k, base_for_entry(facility, b, e))
        for k in sorted(now, key=lambda x: x or 0):
            if k in before and before[k] != now[k]:
                o, n = before[k], now[k]
                changed.append(f'{b.full_name}（{"区分" + str(k) if k else "初期値"}：{o[0] or "コードなし"}・{o[1] if o[1] is not None else "?"} 単位 → '
                               f'{n[0] or "コードなし"}・{n[1] if n[1] is not None else "?"} 単位）')
                break
    if not changed:
        return []
    return [{'text': f'先月と基本報酬のサービスコード・単位数が違う利用者 {len(changed)} 名：' + '、'.join(changed[:5]) + ('…' if len(changed) > 5 else '')
                     + '。設定を変えた覚えが無ければ確かめてください',
             'url': reverse('facilities:settings') + '#tab-basic'}]

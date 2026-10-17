"""
請求の準備チェック：請求書・上限管理・請求集計を出す前に足りない設定を一覧にする（請求マトリックスの上に出す）。
"""
from django.urls import reverse

from beneficiaries.models import Beneficiary, RecipientCertificate
from facilities.models import AddonMaster, FacilityAddonSetting


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
    addons_url = reverse('facilities:settings') + '#tab-billing'
    if not AddonMaster.objects.filter(is_active=True).exists():
        add('加算マスタが空です（開発向けユーザーが「標準加算マスタを読み込む」を実行）', addons_url)
    elif not FacilityAddonSetting.objects.filter(facility=facility, is_enabled=True).exists():
        add('施設設定の加算で「算定する」加算が 1 つもありません（日誌の加算の入力は全部出ます）', addons_url)

    active = list(Beneficiary.objects.filter(facility=facility, status=Beneficiary.STATUS_ACTIVE))
    if active:
        covered = set(RecipientCertificate.objects.filter(beneficiary__in=active, valid_from__lte=month_start, valid_until__gte=month_start)
                      .values_list('beneficiary_id', flat=True))
        missing = [b for b in active if b.pk not in covered]
        if missing:
            names = '・'.join(b.full_name for b in missing[:5]) + ('…' if len(missing) > 5 else '')
            add(f'{month_start.month}月 1 日に有効な受給者証が無い利用者 {len(missing)} 名（{names}）', reverse('beneficiaries:list'))
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

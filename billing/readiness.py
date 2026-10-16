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

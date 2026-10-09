"""
利用者負担上限額管理の計算と一括の操作（billing/views.py の CopaymentListView・CopaymentEditView から使う）

- cap_for            : その月に有効な受給者証の負担上限月額
- this_office_amounts: 当施設の総費用額と利用者負担額（請求書と同じ計算：基本報酬 × 単価 × 利用日数、負担は 1 割と上限の小さい方）
- allocate           : 管理事業所（当施設）を先に上限まで充て、残りをほかの事業所に順に配分する。管理結果（1・2・3）も決める
- judge              : 一覧の判定（対象外・当施設で上限に達する・配分が要る・ほかの事業所が管理）
- apply_fax_result   : ほかの事業所が管理する利用者に、FAX で返ってきた結果（0 円・全額・金額）を反映する
- copy_from_prev_month: 先月の「管理する／しない」と事業所の並びを、今月の未入力の利用者に写す

上限管理の担当は行政が決めるのではなく、利用者に関わる事業所間で決める（利用者情報の「利用事業所」の上限管理事業所のフラグ）。
"""
import datetime

from django.db import transaction

from beneficiaries.models import Beneficiary, RecipientCertificate

from .models import CopaymentManagement, CopaymentOfficeRecord

RESULT_ALL_HERE = '1'     # 管理事業所が充当したため、他事業所の徴収なし
RESULT_NO_ADJUST = '2'    # 合算が上限以下のため調整なし
RESULT_ADJUSTED = '3'     # 合算が上限を超えるため調整あり

FAX_CHOICES = [('zero', '0 円（管理事業所で上限に達した）'), ('full', '全額（調整なし）'), ('amount', '金額を入れる')]


def cap_for(beneficiary, year, month):
    """その月に有効な受給者証の負担上限月額。受給者証が無ければ None"""
    first = datetime.date(year, month, 1)
    cert = (RecipientCertificate.objects.filter(beneficiary=beneficiary, valid_from__lte=first, valid_until__gte=first)
            .order_by('-valid_until').first())
    return cert.monthly_cap if cert else None


def this_office_amounts(facility, beneficiary, year, month):
    """当施設の (総費用額, 利用者負担額)。どちらも分からなければ (None, None)"""
    from .views import _build_invoice_context
    ctx = _build_invoice_context(facility, beneficiary, year, month)
    return ctx['total_cost'], ctx['user_burden']


def allocate(cap, rows):
    """
    配分。rows は [{'is_this_office': bool, 'original_copayment': int}]（当施設を先頭に、あとは入れた順）。
    戻り値 (調整後の額のリスト, 管理結果)。cap が無い（None・0）ときは調整せず結果 2
    """
    originals = [max(int(r.get('original_copayment') or 0), 0) for r in rows]
    if not cap:
        return originals, RESULT_NO_ADJUST
    if sum(originals) <= cap:
        return originals, RESULT_NO_ADJUST
    order = sorted(range(len(rows)), key=lambda i: (not rows[i].get('is_this_office'), i))
    remaining, adjusted = cap, [0] * len(rows)
    for i in order:
        adjusted[i] = min(originals[i], remaining)
        remaining -= adjusted[i]
    mine = next((i for i in order if rows[i].get('is_this_office')), None)
    if mine is not None and adjusted[mine] == cap and all(adjusted[i] == 0 for i in range(len(rows)) if i != mine):
        return adjusted, RESULT_ALL_HERE
    return adjusted, RESULT_ADJUSTED


def judge(facility, beneficiary, year, month, management=None):
    """
    一覧の判定。戻り値 {'target', 'state', 'text', 'cap', 'burden', 'total_cost'}
    state: 'single'（1 事業所だけ：対象外）・'here'（当施設が管理）・'other'（ほかの事業所が管理）
    """
    offices = list(beneficiary.offices.all())
    others = [o for o in offices if not o.is_this_office]
    cap = cap_for(beneficiary, year, month)
    total_cost, burden = this_office_amounts(facility, beneficiary, year, month)
    out = {'cap': cap, 'burden': burden, 'total_cost': total_cost, 'target': bool(others) or management is not None}
    if management is not None:
        here = management.is_upper_limit_manager
    else:
        here = beneficiary.is_copayment_manager_here or not others
    if not out['target']:
        out['state'], out['text'] = 'single', '対象外（利用は当施設だけ）'
        return out
    if here:
        out['state'] = 'here'
        if management is not None and management.office_records.exists():
            out['text'] = '当施設が管理：' + management.get_management_result_display()
        elif cap is None:
            out['text'] = '当施設が管理：受給者証（上限月額）が未登録'
        elif burden is not None and burden >= cap:
            out['text'] = f'当施設が管理：当施設だけで上限 {cap:,} 円に達する → ほかの事業所は 0 円'
        else:
            out['text'] = '当施設が管理：ほかの事業所の額を入れて配分'
    else:
        out['state'] = 'other'
        if management is not None and management.office_records.filter(is_this_office=True).exists():
            out['text'] = f'ほかの事業所が管理：結果を反映ずみ（当施設 {management.this_office_copayment:,} 円）'
        else:
            out['text'] = 'ほかの事業所が管理：FAX の結果待ち'
    return out


@transaction.atomic
def apply_fax_result(facility, beneficiary, year, month, choice, amount=None):
    """
    ほかの事業所が管理する利用者に、FAX で返ってきた結果を反映する。
    choice: 'zero'（0 円）・'full'（全額＝当施設の負担額）・'amount'（金額）。戻り値は管理の行
    """
    total_cost, burden = this_office_amounts(facility, beneficiary, year, month)
    burden = burden or 0
    if choice == 'zero':
        adjusted, result = 0, RESULT_ALL_HERE
    elif choice == 'full':
        adjusted, result = burden, RESULT_NO_ADJUST
    else:
        adjusted = max(min(int(amount or 0), burden if burden else int(amount or 0)), 0)
        result = RESULT_NO_ADJUST if adjusted == burden else RESULT_ADJUSTED
    m, _ = CopaymentManagement.objects.get_or_create(
        facility=facility, beneficiary=beneficiary, year_month=f'{year}-{month:02d}',
        defaults={'is_upper_limit_manager': False, 'management_result': result})
    m.is_upper_limit_manager, m.management_result = False, result
    m.save()
    mine = m.office_records.filter(is_this_office=True).first()
    if mine is None:
        mine = CopaymentOfficeRecord(management=m, is_this_office=True, office_name=facility.name,
                                     office_number=facility.office_number or '')
    mine.total_cost = total_cost or mine.total_cost or 0
    mine.original_copayment, mine.adjusted_copayment = burden, adjusted
    mine.save()
    return m


def copy_from_prev_month(facility, year, month):
    """先月の管理（管理する／しない・事業所の並び）を、今月まだ無い利用者に写す。写した人数を返す"""
    py, pm = (year - 1, 12) if month == 1 else (year, month - 1)
    prev = {m.beneficiary_id: m for m in CopaymentManagement.objects.filter(facility=facility, year_month=f'{py}-{pm:02d}')
            .prefetch_related('office_records')}
    have = set(CopaymentManagement.objects.filter(facility=facility, year_month=f'{year}-{month:02d}').values_list('beneficiary_id', flat=True))
    n = 0
    for b in Beneficiary.objects.filter(facility=facility, status='active', pk__in=prev.keys()).exclude(pk__in=have):
        old = prev[b.pk]
        total_cost, burden = this_office_amounts(facility, b, year, month)
        m = CopaymentManagement.objects.create(facility=facility, beneficiary=b, year_month=f'{year}-{month:02d}',
                                               is_upper_limit_manager=old.is_upper_limit_manager, management_result=RESULT_NO_ADJUST)
        for r in old.office_records.all():
            CopaymentOfficeRecord.objects.create(
                management=m, is_this_office=r.is_this_office, office_name=r.office_name, office_number=r.office_number,
                total_cost=(total_cost or 0) if r.is_this_office else 0,
                original_copayment=(burden or 0) if r.is_this_office else 0,
                adjusted_copayment=(burden or 0) if r.is_this_office else 0)
        n += 1
    return n

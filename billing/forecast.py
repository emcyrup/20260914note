"""
月次の見込み（2026-10-16 はびりす打ち合わせの「4. 月次の見込みダッシュボード」）：
その月の延べ利用人数・総費用（売上の概算）・給付費・利用者負担・処遇改善加算の額を、
- 実績：請求マトリックスで「来所」にした日（請求書と同じ計算。billing/views._build_invoice_context）
- 見込み：実績に、今日から月末までの来所予定（schedules.ScheduledVisit の「予定」）を足したもの。
  予定の日の額は、その利用者の今月の 1 日あたりの総費用（実績が無ければ基本報酬の初期値 × 単価）で見積もる
で出す。利用者負担は 1 割と上限月額の小さい方（上限額管理での調整の前）。給付費 ＝ 総費用 − 利用者負担
"""
import datetime
from decimal import Decimal

from beneficiaries.models import Beneficiary

from . import codes
from .copayment import month_range
from .models import BillingMatrixEntry


def _estimate_per_day(facility, beneficiary, day):
    """実績の無い利用者の 1 日の総費用の見積もり：その日の時間区分の初期値の基本報酬 × 単価。分からなければ None"""
    units = facility.base_units_for(beneficiary, codes.default_kubun(facility, day))
    if units is None:
        return None
    return int(Decimal(units) * facility.unit_price)


def month_summary(facility, year, month, today=None):
    """{'rows': [利用者ごと], 'actual': {...}, 'forecast': {...}, 'remaining_days': 予定の延べ日数}"""
    from schedules.models import ScheduledVisit

    from .views import _build_invoice_context
    today = today or datetime.date.today()
    first, last = month_range(year, month)
    used = set(BillingMatrixEntry.objects.filter(facility=facility, date__year=year, date__month=month)
               .values_list('beneficiary_id', flat=True))
    # これからの予定（今日以降・月末まで。請求マトリックスにまだ付けていない日だけ）
    done = set(BillingMatrixEntry.objects.filter(facility=facility, date__year=year, date__month=month)
               .values_list('beneficiary_id', 'date'))
    planned = {}
    if last >= today:
        for bid, d in ScheduledVisit.objects.filter(facility=facility, status=ScheduledVisit.STATUS_SCHEDULED,
                                                    date__gte=max(first, today), date__lte=last).values_list('beneficiary_id', 'date'):
            if (bid, d) not in done:
                planned.setdefault(bid, []).append(d)
    people = Beneficiary.objects.filter(facility=facility, pk__in=used | set(planned)).order_by('last_name_kana', 'first_name_kana')
    rows = []
    for b in people:
        ctx = _build_invoice_context(facility, b, year, month)
        days = ctx['attended_days']
        cost = ctx['total_cost']
        burden = ctx['user_burden']
        treat_yen = int(Decimal(ctx['treatment']['units']) * ctx['unit_price']) if ctx['treatment'] else 0
        future = planned.get(b.pk, [])
        per_day = (cost // days) if cost and days else (_estimate_per_day(facility, b, future[0]) if future else None)
        f_cost = (cost or 0) + (per_day or 0) * len(future) if (cost is not None or per_day is not None) else None
        cap = ctx['monthly_cap']
        if f_cost is not None and cap is not None:
            f_burden = min(int(Decimal(f_cost) * Decimal('0.1')), cap)
        else:
            f_burden = burden
        f_treat = int(Decimal(treat_yen) * f_cost / cost) if treat_yen and cost and f_cost else treat_yen
        rows.append({
            'beneficiary': b, 'days': days, 'cost': cost, 'burden': burden, 'cap': cap, 'treatment': treat_yen,
            'benefit': (cost - burden) if cost is not None and burden is not None else None,
            'planned': len(future), 'f_days': days + len(future), 'f_cost': f_cost, 'f_burden': f_burden, 'f_treatment': f_treat,
            'f_benefit': (f_cost - f_burden) if f_cost is not None and f_burden is not None else None,
            'unknown': cost is None,
        })

    def total(prefix=''):
        def s(key):
            return sum(r[prefix + key] or 0 for r in rows)
        return {'people': sum(1 for r in rows if r[prefix + 'days']), 'days': s('days'), 'cost': s('cost'),
                'burden': s('burden'), 'benefit': s('benefit'), 'treatment': s('treatment')}
    return {'rows': rows, 'actual': total(), 'forecast': total('f_'), 'remaining_days': sum(len(v) for v in planned.values()),
            'unknown': [r['beneficiary'] for r in rows if r['unknown']]}

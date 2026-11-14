"""
請求の動作を確かめるための架空の利用者（既定 40 人）と、2 か月ぶんの出欠・加算・上限額管理（2026-10-28 はびりす）。
名前・番号・事業所はすべて架空。備考の先頭に MARK を入れて見分け、--remove でその利用者だけを関連の記録ごと消す。

- python manage.py billing_demo --facility 2                  … 入れる（もう入っていれば何もしない）
- python manage.py billing_demo --facility 2 --month 2026-10  … 今月の扱いにする月を指定
- python manage.py billing_demo --facility 2 --remove         … 消す

入るもの（「今月」＝ --month、前月はまるごと実績）：
- 受給者証：上限月額 0 円 4 人・37,200 円 8 人・4,600 円 28 人。月の途中から有効な人（新規）・月の途中で更新して上限月額が
  変わる人・支給量を超えて来所する人も入れる（請求の準備チェックで知らせが出る）
- 出欠：曜日の決まった来所予定。前月と今月の昨日までは請求マトリックスに「来所」（5% ほど欠席 → 欠席時対応加算）、
  今日からは「予定」（月次の見込みに出る）。平日は区分 2（1 割ほど区分 1）、土日祝は区分 3
- 加算：送迎（迎え・送り）、個別サポート加算Ⅰ、欠席時対応加算。医療的ケア区分の子 2 人
- 上限額管理：ほかの事業所も使う子 8 人（当施設が管理 5 人・ほかが管理 3 人）。前月は結果 1・2・3 が出るように額を入れる
- 減算：今月、個別支援計画未作成（1〜2 か月目）の子 1 人
施設の設定は、空のときだけ埋める（定員区分 → 11〜20 人、加算の「算定する」が 1 つも無ければ標準の組み合わせ）。消しても戻さない。
"""
import calendar
import datetime
import random

from django.db import transaction

from beneficiaries.models import Beneficiary, BeneficiaryOffice, RecipientCertificate
from facilities.models import AddonMaster, FacilityAddonSetting

from . import codes
from . import copayment as cp
from .models import BillingMatrixAddon, BillingMatrixEntry, CopaymentManagement, CopaymentOfficeRecord, MonthlyReduction

MARK = '【請求デモ】'
NOTE = MARK + '請求の動作を確かめるための架空の利用者です。不要になったら billing_demo --remove で消せます。'

LAST = [('青木', 'あおき'), ('石田', 'いしだ'), ('上野', 'うえの'), ('大森', 'おおもり'), ('加藤', 'かとう'), ('北村', 'きたむら'),
        ('久保', 'くぼ'), ('小松', 'こまつ'), ('佐野', 'さの'), ('島田', 'しまだ'), ('杉本', 'すぎもと'), ('関口', 'せきぐち'),
        ('高木', 'たかぎ'), ('千葉', 'ちば'), ('土屋', 'つちや'), ('内藤', 'ないとう'), ('西川', 'にしかわ'), ('野口', 'のぐち'),
        ('原田', 'はらだ'), ('福田', 'ふくだ')]
FIRST = [('あおい', 'あおい', 'female'), ('はると', 'はると', 'male'), ('ゆい', 'ゆい', 'female'), ('そうた', 'そうた', 'male'),
         ('ひなた', 'ひなた', 'female'), ('りく', 'りく', 'male'), ('めい', 'めい', 'female'), ('ゆうと', 'ゆうと', 'male')]
OTHER_OFFICE = ('架空こども発達支援センター', '2650099999')
# ほかの事業所も使う子：(番号, 当施設が管理するか, 前月のほかの事業所の負担額（調整前）)
SHARED = [(6, True, 5000), (7, True, 33000), (20, True, 3000), (21, True, 2500), (22, True, 4600),
          (8, False, 0), (23, False, 0), (24, False, 0)]
STANDARD_ADDONS = ['送迎加算（往・迎え）', '送迎加算（復・送り）', '欠席時対応加算', '個別サポート加算Ⅰ（ケアニーズの高い児）',
                   '専門的支援体制加算', '児童指導員等加配加算（常勤専従・経験5年以上）', '福祉・介護職員等処遇改善加算Ⅰ',
                   '利用者負担上限額管理加算']


def is_demo(b):
    return (b.notes or '').startswith(MARK)


def _cap(i):
    return 0 if i < 4 else 37200 if i < 12 else 4600


def _days(year, month, weekdays):
    n = calendar.monthrange(year, month)[1]
    return [datetime.date(year, month, d) for d in range(1, n + 1) if datetime.date(year, month, d).weekday() in weekdays]


def _setup_facility(facility):
    """空の設定だけ埋める。埋めたものの説明の一覧を返す"""
    done = []
    if not facility.capacity_class and not facility.base_unit_count:
        facility.capacity_class = '20'
        facility.save(update_fields=['capacity_class'])
        done.append('定員区分を「定員11〜20人」に')
    if not FacilityAddonSetting.objects.filter(facility=facility, is_enabled=True).exists():
        names = []
        for a in AddonMaster.objects.filter(is_active=True, name__in=STANDARD_ADDONS):
            FacilityAddonSetting.objects.update_or_create(facility=facility, addon=a, defaults={'is_enabled': True})
            names.append(a.name)
        if names:
            done.append('加算を「算定する」に：' + '・'.join(names))
    return done


@transaction.atomic
def seed(facility, year, month, count=40, today=None):
    """入れる。もう入っていれば {'skipped': True}。入れた数と、埋めた設定の説明を返す"""
    if any(is_demo(b) for b in Beneficiary.objects.filter(facility=facility, notes__startswith=MARK)):
        return {'skipped': True}
    today = today or datetime.date.today()
    rnd = random.Random(20261028)
    settings_done = _setup_facility(facility)
    addons = {a.name: a for a in AddonMaster.objects.filter(is_active=True, addon_type='individual')}
    pickup, dropoff = addons.get('送迎加算（往・迎え）'), addons.get('送迎加算（復・送り）')
    absent_addon, support = addons.get('欠席時対応加算'), addons.get('個別サポート加算Ⅰ（ケアニーズの高い児）')
    from schedules.models import ScheduledVisit
    from .views import snapshot_entry
    py, pm = (year - 1, 12) if month == 1 else (year, month - 1)
    first = datetime.date(year, month, 1)
    prev_first = datetime.date(py, pm, 1)
    counts = {'people': 0, 'entries': 0, 'planned': 0, 'addons': 0}
    people = []
    for i in range(count):
        last, last_kana = LAST[i % len(LAST)]
        given, given_kana, gender = FIRST[(i * 3 + i // len(LAST)) % len(FIRST)]
        b = Beneficiary.objects.create(
            facility=facility, last_name=last, first_name=given, last_name_kana=last_kana, first_name_kana=given_kana,
            gender=gender, date_of_birth=datetime.date(2012 + i % 7, 1 + i % 12, 1 + i % 27), status=Beneficiary.STATUS_ACTIVE,
            medical_care_level=3 if i == 15 else 16 if i == 16 else 0, notes=NOTE)
        number = f'99{i + 1:08d}'
        if i == 12:      # 今月の 15 日からの新規
            RecipientCertificate.objects.create(beneficiary=b, certificate_number=number, granted_days=23, monthly_cap=4600,
                                                valid_from=first.replace(day=15), valid_until=first.replace(year=year + 1) - datetime.timedelta(days=1),
                                                municipality='架空市', municipality_code='999999')
        elif i == 13:    # 今月の 15 日で更新。上限月額が 4,600 → 37,200 円に変わる
            RecipientCertificate.objects.create(beneficiary=b, certificate_number=number, granted_days=23, monthly_cap=4600,
                                                valid_from=first.replace(year=year - 1, day=16), valid_until=first.replace(day=15),
                                                municipality='架空市', municipality_code='999999')
            RecipientCertificate.objects.create(beneficiary=b, certificate_number=number, granted_days=23, monthly_cap=37200,
                                                valid_from=first.replace(day=16), valid_until=first.replace(year=year + 1, day=15),
                                                municipality='架空市', municipality_code='999999')
        else:
            RecipientCertificate.objects.create(beneficiary=b, certificate_number=number, granted_days=5 if i == 14 else 23,
                                                monthly_cap=_cap(i), valid_from=prev_first.replace(day=1) - datetime.timedelta(days=150),
                                                valid_until=prev_first + datetime.timedelta(days=330),
                                                municipality='架空市', municipality_code='999999')
        weekdays = sorted(rnd.sample(range(5), 3 if i == 14 else rnd.choice((2, 2, 3, 4)))) + ([5] if rnd.random() < 0.4 else [])
        rides = rnd.random() < 0.6
        people.append((i, b, weekdays, rides))
        counts['people'] += 1

    for i, b, weekdays, rides in people:
        for y, m in ((py, pm), (year, month)):
            for d in _days(y, m, weekdays):
                if i == 12 and d < first.replace(day=15):
                    continue
                if d >= today:
                    ScheduledVisit.objects.create(facility=facility, beneficiary=b, date=d, has_pickup=rides, has_dropoff=rides)
                    counts['planned'] += 1
                    continue
                status = 'absent' if rnd.random() < 0.05 else 'attended'
                ScheduledVisit.objects.create(facility=facility, beneficiary=b, date=d, status=status, has_pickup=rides, has_dropoff=rides)
                kubun = codes.default_kubun(facility, d)
                if kubun != 3 and rnd.random() < 0.1:
                    kubun = 1
                e = BillingMatrixEntry.objects.create(facility=facility, beneficiary=b, date=d, status=status, is_finalized=True, time_kubun=kubun)
                snapshot_entry(e, facility, b)
                counts['entries'] += 1
                applied = []
                if status == 'absent':
                    applied = [absent_addon]
                else:
                    if rides:
                        applied += [pickup, dropoff]
                    if 30 <= i <= 33:
                        applied.append(support)
                for a in applied:
                    if a is not None:
                        BillingMatrixAddon.objects.create(entry=e, addon=a, is_applied=True)
                        counts['addons'] += 1

    # 上限額管理：ほかの事業所も使う子
    by_index = {i: b for i, b, _, _ in people}
    for i, here, other_amount in SHARED:
        b = by_index.get(i)
        if b is None:
            continue
        BeneficiaryOffice.objects.create(beneficiary=b, name=facility.name, office_number=facility.office_number,
                                         is_this_office=True, is_manager=here, order=0)
        BeneficiaryOffice.objects.create(beneficiary=b, name=OTHER_OFFICE[0], office_number=OTHER_OFFICE[1], is_manager=not here,
                                         fax='000-0000-0000', contact_name='架空 担当', order=1)
        if here:
            total, burden = cp.this_office_amounts(facility, b, py, pm)
            rows = [{'is_this_office': True, 'original_copayment': burden or 0}, {'is_this_office': False, 'original_copayment': other_amount}]
            adjusted, result = cp.allocate(cp.cap_for(b, py, pm), rows)
            mg = CopaymentManagement.objects.create(facility=facility, beneficiary=b, year_month=f'{py}-{pm:02d}',
                                                    is_upper_limit_manager=True, management_result=result)
            CopaymentOfficeRecord.objects.create(management=mg, is_this_office=True, office_name=facility.name,
                                                 office_number=facility.office_number, total_cost=total or 0,
                                                 original_copayment=burden or 0, adjusted_copayment=adjusted[0])
            CopaymentOfficeRecord.objects.create(management=mg, office_name=OTHER_OFFICE[0], office_number=OTHER_OFFICE[1],
                                                 total_cost=other_amount * 10, original_copayment=other_amount, adjusted_copayment=adjusted[1])
        elif i == 8:     # 前月の FAX の結果を反映ずみの例（0 円）。ほかの 2 人は「結果待ち」のまま
            cp.apply_fax_result(facility, b, py, pm, 'zero')

    # 減算：今月、個別支援計画未作成（1〜2 か月目）
    if 35 in by_index:
        MonthlyReduction.objects.create(facility=facility, beneficiary=by_index[35], year_month=f'{year:04d}-{month:02d}', no_plan='70')
    counts['settings'] = settings_done
    return counts


@transaction.atomic
def remove(facility):
    """印のある利用者だけを、受給者証・出欠・加算・上限額管理・減算ごと消す。消した人数を返す"""
    people = [b for b in Beneficiary.objects.filter(facility=facility, notes__startswith=MARK) if is_demo(b)]
    ids = [b.pk for b in people]
    from schedules.models import ScheduledVisit
    BillingMatrixEntry.objects.filter(beneficiary_id__in=ids).delete()
    ScheduledVisit.objects.filter(beneficiary_id__in=ids).delete()
    CopaymentManagement.objects.filter(beneficiary_id__in=ids).delete()
    MonthlyReduction.objects.filter(beneficiary_id__in=ids).delete()
    for b in people:
        b.delete()
    return len(people)

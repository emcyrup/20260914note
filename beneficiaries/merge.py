"""
同じ子が2人に分かれてしまった利用者を1人にまとめる（重複のまとめ）

- find_groups(facility)  : 名前（空白を除いた姓＋名）が同じ利用者の組
- plan(keep, drop)       : まとめたときに何がどう動くか（画面の確認に出す）。conflicts があればまとめない
- merge(keep, drop)      : drop の記録・予約などを keep へ移し、keep の空欄を drop の値で埋めて、drop を消す

同じ日（月）に両方の記録・予約がある（1人1日1件の決まりがある）ものは、どちらを残すか職員が決めてからにする。
送迎・健康の設定（1人1つ）が両方にあるときは keep のものを使う。療育記録の留意点は両方の文をつなぐ。
"""
import re

from django.db import transaction

from .importer import PLACEHOLDER_DOB
from .models import Beneficiary

# keep の欄が空のとき drop の値で埋める欄（氏名・生年月日・在籍状況は別に扱う）
FILL_FIELDS = ('last_name_kana', 'first_name_kana', 'disability_class', 'disability_type', 'postal_code', 'address',
               'mobile_phone', 'home_phone', 'school_name', 'grade', 'admission_date', 'discharge_date')
OR_FIELDS = ('is_severe', 'has_prior_records', 'weekday_mon', 'weekday_tue', 'weekday_wed', 'weekday_thu',
             'weekday_fri', 'weekday_sat')
# 1人1日（1月）に1件の決まりがあるもの：(関連の名前, 重ならないかを見る欄, 画面の名前)
UNIQUE_BY = {
    'reservations': (('date',), '予約'),
    'scheduled_visits': (('date',), '来所予定'),
    'daily_records': (('date',), '記録（日誌）'),
    'health_logs': (('date',), '健康の記録'),
    'monthly_requests': (('year', 'month'), '月予約利用希望'),
    'billingmatrixentry': (('date',), '請求の実績'),
    'billingrecord': (('year_month',), '請求'),
    'copaymentmanagement': (('year_month',), '上限額管理'),
}
ONE_TO_ONE = {'therapy_profile': '療育記録の留意点', 'transport': '送迎の設定', 'health_profile': '健康の基本情報'}


def _norm(text):
    return re.sub(r'[\s　]+', '', text or '')


def find_groups(facility):
    """名前が同じ利用者の組（2人以上）。仮の生年月日の人を後ろにして、組は名前の順"""
    groups = {}
    for b in Beneficiary.objects.filter(facility=facility).order_by('pk'):
        groups.setdefault(_norm(b.last_name + b.first_name), []).append(b)
    out = []
    for key in sorted(groups):
        people = groups[key]
        if len(people) > 1:
            people.sort(key=lambda b: (b.date_of_birth == PLACEHOLDER_DOB, b.status != 'active', b.pk))
            out.append(people)
    return out


def _relations():
    for rel in Beneficiary._meta.related_objects:
        if rel.related_model is Beneficiary:
            continue
        yield rel


def _label(rel):
    if rel.get_accessor_name() in UNIQUE_BY:
        return UNIQUE_BY[rel.get_accessor_name()][1]
    if rel.get_accessor_name() in ONE_TO_ONE:
        return ONE_TO_ONE[rel.get_accessor_name()]
    return str(rel.related_model._meta.verbose_name)


def _rows(rel, b):
    """その利用者の関連の行（予約は有効なものだけを重なりの判定に使う）"""
    return rel.related_model._default_manager.filter(**{rel.field.name: b})


def counts(b):
    """関連の件数（0 件は除く）：[(画面の名前, 件数)]"""
    out = []
    for rel in _relations():
        if rel.one_to_one:
            n = 1 if rel.related_model._default_manager.filter(**{rel.field.name: b}).exists() else 0
        elif rel.many_to_many:
            n = getattr(b, rel.get_accessor_name()).count()
        else:
            n = _rows(rel, b).count()
        if n:
            out.append((_label(rel), n))
    return out


def _conflicts(keep, drop):
    out = []
    for rel in _relations():
        name = rel.get_accessor_name()
        if name not in UNIQUE_BY:
            continue
        keys, label = UNIQUE_BY[name]
        a, b = _rows(rel, keep), _rows(rel, drop)
        if name == 'reservations':
            from reservations.models import Reservation
            a, b = a.filter(status__in=Reservation.ACTIVE_STATUSES), b.filter(status__in=Reservation.ACTIVE_STATUSES)
        mine = set(a.values_list(*keys))
        both = sorted(k for k in set(b.values_list(*keys)) if k in mine)
        for k in both:
            when = '/'.join(str(x) for x in k) if len(k) > 1 else (f'{k[0]:%Y/%-m/%-d}' if hasattr(k[0], 'year') else str(k[0]))
            out.append(f'{label}：{when} が両方にあります')
    return out


def _fills(keep, drop):
    out = []
    if keep.date_of_birth == PLACEHOLDER_DOB and drop.date_of_birth != PLACEHOLDER_DOB:
        out.append(('date_of_birth', '生年月日', drop.date_of_birth))
    for name in FILL_FIELDS:
        if not getattr(keep, name) and getattr(drop, name):
            out.append((name, Beneficiary._meta.get_field(name).verbose_name, getattr(drop, name)))
    if keep.gender != drop.gender and keep.gender == Beneficiary._meta.get_field('gender').default:
        out.append(('gender', '性別', drop.get_gender_display()))
    return out


def plan(keep, drop):
    """まとめたときの見込み（画面の確認用）"""
    one_to_one = []
    for rel in _relations():
        if rel.one_to_one:
            has_a = rel.related_model._default_manager.filter(**{rel.field.name: keep}).exists()
            has_b = rel.related_model._default_manager.filter(**{rel.field.name: drop}).exists()
            if has_a and has_b:
                label = ONE_TO_ONE.get(rel.get_accessor_name(), _label(rel))
                one_to_one.append(f'{label}：両方の文をつなぎます' if rel.get_accessor_name() == 'therapy_profile'
                                  else f'{label}：残す方のものを使います（まとめる方のものは消えます）')
    return {'keep': keep, 'drop': drop, 'moves': counts(drop), 'fills': _fills(keep, drop),
            'conflicts': _conflicts(keep, drop), 'one_to_one': one_to_one}


@transaction.atomic
def merge(keep, drop):
    """drop を keep にまとめて drop を消す。重なり（conflicts）があれば ValueError"""
    if keep.pk == drop.pk or keep.facility_id != drop.facility_id:
        raise ValueError('同じ事業所の別の利用者を選んでください。')
    conflicts = _conflicts(keep, drop)
    if conflicts:
        raise ValueError('まとめられません。' + '／'.join(conflicts))
    for name, _label_, _value in _fills(keep, drop):
        setattr(keep, name, getattr(drop, name))
    for name in OR_FIELDS:
        if getattr(drop, name):
            setattr(keep, name, True)
    if drop.notes and drop.notes.strip() not in keep.notes:
        keep.notes = (keep.notes.rstrip() + '\n' if keep.notes.strip() else '') + drop.notes.strip()
    if keep.date_of_birth != PLACEHOLDER_DOB:
        keep.notes = '\n'.join(ln for ln in keep.notes.splitlines() if '生年月日は取り込み時の仮の値' not in ln).strip()
    if keep.status != 'active' and drop.status == 'active':
        keep.status, keep.discharge_date = 'active', None
    for rel in _relations():
        name, model, field = rel.get_accessor_name(), rel.related_model, rel.field.name
        if rel.many_to_many:          # 予約の保護者の子・クラスのメンバー
            for obj in getattr(drop, name).all():
                getattr(obj, field).add(keep)
            continue
        if rel.one_to_one:
            mine = model._default_manager.filter(**{field: keep}).first()
            theirs = model._default_manager.filter(**{field: drop}).first()
            if theirs is None:
                continue
            if mine is None:
                setattr(theirs, field, keep)
                theirs.save()
            elif name == 'therapy_profile' and theirs.cautions.strip() and theirs.cautions.strip() not in mine.cautions:
                mine.cautions = (mine.cautions.rstrip() + '\n' if mine.cautions.strip() else '') + theirs.cautions.strip()
                mine.save()
            continue
        model._default_manager.filter(**{field: drop}).update(**{field: keep})
    for name in ('siblings', 'cannot_pair', 'no_ride_with'):
        others = [b for b in getattr(drop, name).all() if b.pk != keep.pk]
        getattr(keep, name).add(*others)
        getattr(keep, name).remove(drop)
    keep.save()
    drop.delete()
    return keep

"""施設（事業所）を作るときの共通処理。管理コマンド create_facility と、自己登録（サインアップ）から使う。"""
from django.db import transaction

from .models import Facility, SupportContentTag

COPY_FIELDS = [
    'region_category', 'standard_close_time', 'base_unit_count', 'is_new_facility_r8',
    'term_staff', 'term_beneficiary', 'brand_color', 'use_billing', 'use_line', 'use_reservation',
    'journal_sections', 'layout',
]


def add_default_tags(facility):
    """標準の活動タグ・支援内容タグを入れる（画面の「標準タグを読み込む」と同じ内容）"""
    from records.models import ActivityTag
    from .views import ActivityTagLoadDefaultsView, SupportTagLoadDefaultsView
    for tag_name, order in ActivityTagLoadDefaultsView.DEFAULT_TAGS:
        ActivityTag.objects.get_or_create(facility=facility, name=tag_name, defaults={'display_order': order})
    for tag_name, order in SupportTagLoadDefaultsView.DEFAULT_TAGS:
        SupportContentTag.objects.get_or_create(facility=facility, name=tag_name, defaults={'order': order})
    return len(ActivityTagLoadDefaultsView.DEFAULT_TAGS), len(SupportTagLoadDefaultsView.DEFAULT_TAGS)


@transaction.atomic
def create_facility(name, office_number='', copy_from=None, default_tags=True):
    """施設を作り、必要なら設定を既存施設からコピーし、標準タグを入れる"""
    facility = Facility(name=name.strip(), office_number=office_number or '')
    if copy_from is not None:
        for f in COPY_FIELDS:
            setattr(facility, f, getattr(copy_from, f))
    facility.save()
    if default_tags:
        add_default_tags(facility)
    return facility

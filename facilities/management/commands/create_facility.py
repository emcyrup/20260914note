"""
別の事業所（施設）を同じアプリ内に作る。

  python manage.py create_facility "あおば教室" --admin aoba_admin --password '（初期パスワード）'
  python manage.py create_facility "あおば教室" --admin aoba_admin --password '…' --copy-settings-from 1 --demo
  python manage.py create_facility "発達支援ルーム　ゆあーず" --admin ryoiku --password '…' --copy-settings-from <なゆたのID> --preset ryoiku

- 施設を作成し、標準の活動タグ・支援内容タグを入れる
- --admin を付けると、その施設の管理者アカウント（権限区分＝管理者）を作る
- --copy-settings-from <施設ID> で、呼び方・配色・使う機能・日誌の項目順・単位数などの設定を既存施設からコピーする
- --demo を付けると架空のサンプルデータ（seed_demo）も投入する
- --preset ryoiku で、療育の事業所向けの設定にする（予約管理＋療育記録を使う。予約は時間枠：1枠45分・1枠3人・
  平日 10〜18 時・土日祝 9〜17 時・12 時は枠なし・月曜日と木曜日はお休み）
- --preset care で、介護施設（やすらぎの森）にする（検温板・介護記録・連絡帳・ヒヤリハット／事故報告・薬・送迎・人員配置・
  通所介護計画書。--demo は介護施設の架空データ（nursing_demo））
- --preset kyotaku で、居宅介護（ホームヘルプ）の事業所にする（訪問予定・サービス提供記録・居宅介護計画・実績記録票。
  職員 1 人向けで職員・運用管理の画面は出さない。請求・来所予定・予約・LINE は使わない）。--demo は居宅介護支援（ケアマネ）の架空データ（kaigo_demo）
記録（利用者・日誌・計画・請求）は施設ごとに完全に分かれる。職員は1つの施設にだけ所属する。
"""
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import StaffAccount
from facilities.models import Facility
from facilities.services import COPY_FIELDS, create_facility  # noqa: F401  (COPY_FIELDS は互換のため公開)


TRIAL_AI_LIMIT = 20   # お試しの AI を使える回数（1回あたり数十円 → 20回で 500 円ほど）


# 予約の設定で、ほかの事業所から写してよい項目（公開アドレス・LINE グループ・署名は写さない）
RESERVATION_COPY_FIELDS = [
    'capacity', 'allow_waitlist', 'closed_weekdays', 'auto_send', 'public_calendar', 'public_booking', 'public_request',
    'booking_from_days', 'booking_until_days', 'notify_vacancy', 'booking_mode', 'group_auto_apply',
    'slot_mode', 'slot_capacity', 'slot_minutes', 'weekday_first_hour', 'weekday_last_hour',
    'holiday_first_hour', 'holiday_last_hour', 'break_hours', 'legal_capacity', 'day_limit', 'avg_limit',
]


def copy_reservation_setting(src, facility):
    """予約の設定（枠・時間・休業曜日・受付の決まり）を src の事業所から写す。署名は新しい事業所名にする"""
    from reservations.models import ReservationSetting
    from reservations.services import get_setting
    src_setting = ReservationSetting.objects.filter(facility=src).first()
    if src_setting is None:
        return None
    setting = get_setting(facility)
    for f in RESERVATION_COPY_FIELDS:
        if hasattr(src_setting, f):
            setattr(setting, f, getattr(src_setting, f))
    setting.signature = facility.name
    setting.save()
    return setting


def apply_ryoiku_preset(facility):
    """療育の事業所（発達支援ルーム　ゆあーず）の設定：時間枠の予約と療育記録"""
    from reservations.services import get_setting
    facility.use_reservation = True
    facility.use_therapy_record = True
    facility.use_billing = False       # 請求と来所予定（出欠）は使わない（予約管理で枠を扱う）
    facility.use_schedule = False
    facility.layout = Facility.LAYOUT_RYOIKU          # メニュー：基本機能／お試し／設定
    facility.trial_ai_limit = TRIAL_AI_LIMIT           # お試し（記録・支援計画）の AI は回数制限
    if not facility.brand_color:
        facility.brand_color = '#c2703a'   # 療育の画面は暖色系
    facility.save(update_fields=['use_reservation', 'use_therapy_record', 'use_billing', 'use_schedule', 'layout',
                                 'trial_ai_limit', 'brand_color', 'updated_at'])
    setting = get_setting(facility)
    setting.slot_mode = True
    setting.slot_capacity = 3
    setting.slot_minutes = 45
    setting.weekday_first_hour, setting.weekday_last_hour = 10, 18
    setting.holiday_first_hour, setting.holiday_last_hour = 9, 17
    setting.break_hours = [12]
    setting.closed_weekdays = [0, 3]       # 月曜日・木曜日はお休み
    setting.signature = setting.signature or facility.name
    setting.save()
    return setting


def apply_kyotaku_preset(facility):
    """居宅介護（ホームヘルプ）の事業所：訪問予定・サービス提供記録・居宅介護計画・実績記録票。職員は 1 人"""
    facility.layout = Facility.LAYOUT_KYOTAKU
    facility.use_billing = False
    facility.use_schedule = False
    facility.use_reservation = False
    facility.use_line = False
    facility.use_therapy_record = False
    facility.plan_no_ai = True                     # 計画は AI で作文しない（ゆあーずと同じ方針）
    facility.knowledge_reflect = True              # 書類の読み取り：反映するかは人が決める
    if facility.term_staff in ('', '職員'):
        facility.term_staff = 'ヘルパー'
    if not facility.brand_color:
        facility.brand_color = '#3f7f6b'           # 居宅介護の画面は落ち着いた緑
    facility.save()
    return facility


def apply_care_preset(facility):
    """介護施設（やすらぎの森）：検温板・介護記録・連絡帳・ヒヤリハット／事故報告・薬・送迎・人員配置・計画書（nursing アプリ）"""
    facility.layout = Facility.LAYOUT_CARE
    facility.use_billing = False           # 請求は現行ソフト（LIFE・国保連は後の段階）
    facility.use_schedule = False
    facility.use_reservation = False
    facility.use_line = True               # 連絡帳を家族の LINE へ
    facility.use_therapy_record = False
    facility.use_transport = True          # 送迎表（車両・運転手・利用者ごとの送迎の設定）
    facility.plan_no_ai = True
    facility.knowledge_reflect = True
    if facility.term_beneficiary in ('', '利用者'):
        facility.term_beneficiary = 'ご利用者'
    if not facility.brand_color:
        facility.brand_color = '#4f7a4a'   # 介護施設の画面は森の緑
    facility.save()
    return facility


class Command(BaseCommand):
    help = '別の事業所（施設）を作成し、標準タグと管理者アカウントを用意する'

    def add_arguments(self, parser):
        parser.add_argument('name', help='施設名')
        parser.add_argument('--office-number', default='', help='事業所番号（任意）')
        parser.add_argument('--admin', metavar='ユーザー名', help='この施設の管理者アカウントを作る')
        parser.add_argument('--password', help='--admin の初期パスワード（省略時は入力を求める）')
        parser.add_argument('--display-name', default='', help='管理者の表示名（任意）')
        parser.add_argument('--copy-settings-from', type=int, metavar='施設ID', help='設定をコピーする既存施設の ID')
        parser.add_argument('--no-default-tags', action='store_true', help='標準の活動タグ・支援内容タグを入れない')
        parser.add_argument('--demo', action='store_true', help='架空のサンプルデータ（seed_demo）も投入する')
        parser.add_argument('--layout', choices=['standard', 'planbook'], default=None,
                            help='画面の型（planbook＝計画書中心・シンプル：利用者・完了期日一覧・スタッフ・保護者・連絡帳・施設の6メニュー）')
        parser.add_argument('--preset', choices=['ryoiku', 'kyotaku', 'care'], default=None,
                            help='ryoiku＝療育の事業所：予約管理（時間枠・月予約利用希望・月間予定表）と療育記録を使う。'
                                 'kyotaku＝居宅介護支援（ケアマネ）。care＝介護施設（やすらぎの森：検温板・介護記録・連絡帳・事故報告・薬・送迎・計画書）')

    def handle(self, *args, **o):
        name = o['name'].strip()
        if not name:
            raise CommandError('施設名を指定してください')
        if Facility.objects.filter(name=name).exists():
            raise CommandError(f'施設「{name}」はすでにあります（一覧: {self._list()}）')
        if o.get('admin') and StaffAccount.objects.filter(username=o['admin']).exists():
            raise CommandError(f'ユーザー名「{o["admin"]}」はすでに使われています')

        password = o.get('password')
        if o.get('admin') and not password:
            import getpass
            password = getpass.getpass(f'{o["admin"]} の初期パスワード: ')
            if len(password) < 8:
                raise CommandError('パスワードは8文字以上にしてください')

        with transaction.atomic():
            src = None
            if o.get('copy_settings_from'):
                try:
                    src = Facility.objects.get(pk=o['copy_settings_from'])
                except Facility.DoesNotExist:
                    raise CommandError(f'施設ID {o["copy_settings_from"]} は存在しません（一覧: {self._list()}）')
            facility = create_facility(name, o.get('office_number') or '', copy_from=src,
                                       default_tags=not o.get('no_default_tags'))
            if src is not None:
                self.stdout.write(f'設定を「{src.name}」からコピーしました（呼び方・配色・使う機能・日誌の項目・単位数）')
            if o.get('layout'):
                facility.layout = o['layout']
                if facility.layout == Facility.LAYOUT_PLANBOOK and not facility.brand_color:
                    facility.brand_color = '#6f8f4e'  # 計画書中心の画面は緑系
                facility.save(update_fields=['layout', 'brand_color', 'updated_at'])
                self.stdout.write(f'画面の型：{facility.get_layout_display()}')
            if o.get('preset') == 'ryoiku':
                apply_ryoiku_preset(facility)
                self.stdout.write('療育の事業所の設定にしました：予約管理（時間枠 1枠45分・1枠3人・平日10〜18時・土日祝9〜17時・'
                                  '月木休）と療育記録を使います')
            if o.get('preset') == 'kyotaku':
                apply_kyotaku_preset(facility)
                self.stdout.write('居宅介護の事業所の設定にしました：訪問予定・サービス提供記録・居宅介護計画・実績記録票を使います'
                                  '（職員 1 人向け。請求・来所予定・予約・LINE は使いません）')
            if o.get('preset') == 'care':
                apply_care_preset(facility)
                self.stdout.write('介護施設の設定にしました：検温板・介護記録・連絡帳（LINE）・ヒヤリハット／事故報告・薬の管理・送迎表・人員配置・'
                                  '通所介護計画書を使います（請求・予約は使いません）')
            if src is not None and getattr(src, 'use_reservation', False) and copy_reservation_setting(src, facility):
                self.stdout.write(f'予約の設定（枠・時間・休業曜日・受付の決まり）も「{src.name}」から写しました')
            self.stdout.write(self.style.SUCCESS(f'施設「{facility.name}」を作成しました（ID {facility.pk}）'))
            if not o.get('no_default_tags'):
                self.stdout.write('標準の活動タグ・支援内容タグを入れました')

            if o.get('admin'):
                StaffAccount.objects.create_user(
                    username=o['admin'], password=password, facility=facility,
                    role=StaffAccount.ROLE_ADMIN, display_name=o.get('display_name') or '',
                )
                self.stdout.write(self.style.SUCCESS(f'管理者アカウント「{o["admin"]}」を作成しました（初回ログイン後にパスワードを変えてください）'))

        if o.get('demo') and o.get('preset') == 'kyotaku':
            # 居宅介護支援（ケアマネ）の架空データ（2026-10-21 から。前の訪問・記録のサンプルは homecare/demo.py に残してある）
            call_command('kaigo_demo', '--facility', str(facility.pk), stdout=self.stdout)
        elif o.get('demo') and o.get('preset') == 'care':
            call_command('nursing_demo', '--facility', str(facility.pk), stdout=self.stdout)
        elif o.get('demo'):
            call_command('seed_demo', '--facility', str(facility.pk), stdout=self.stdout)

        if o.get('preset') in ('kyotaku', 'care'):
            self.stdout.write('次の手順: この管理者でログイン → 施設設定で事業所名・事業所番号・住所・電話を入れる → 利用者情報で利用者を登録')
        else:
            self.stdout.write('次の手順: この管理者でログイン → 施設設定で事業所番号・呼び方・使う機能を確認 → 職員・運用管理で職員を追加')

    @staticmethod
    def _list():
        return ', '.join(f'{f.pk}:{f.name}' for f in Facility.objects.order_by('pk')) or 'なし'

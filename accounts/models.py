from django.contrib.auth.models import AbstractUser
from django.db import models


class StaffAccount(AbstractUser):
    """
    職員アカウント。Djangoの標準UserをベースにFacility紐づけ・権限区分を追加する。
    """

    ROLE_ADMIN = 'admin'
    ROLE_CHILD_DEV_MANAGER = 'child_dev_manager'  # 児童発達支援管理責任者
    ROLE_STAFF = 'staff'
    ROLE_OFFICE = 'office'

    ROLE_CHOICES = [
        (ROLE_ADMIN, '管理者'),
        (ROLE_CHILD_DEV_MANAGER, '児発管'),
        (ROLE_STAFF, '職員'),
        (ROLE_OFFICE, '事務'),
    ]

    facility = models.ForeignKey(
        'facilities.Facility',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='staff_accounts',
        verbose_name='所属施設',
    )
    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        default=ROLE_STAFF,
        verbose_name='権限区分',
    )
    display_name = models.CharField(max_length=50, blank=True, verbose_name='表示名')

    # 画面の着せ替え（職員ごとに割り当てられる。記録は共有のまま）
    THEME_STANDARD = 'standard'
    THEME_LARGE    = 'large'
    THEME_STYLISH  = 'stylish'
    THEME_SIMPLE   = 'simple'
    THEME_CHOICES = [
        (THEME_STANDARD, 'スタンダード'),
        (THEME_LARGE,    '大きな文字'),
        (THEME_STYLISH,  'スタイリッシュ'),
        (THEME_SIMPLE,   'かんたん3ステップ'),
    ]
    THEME_INFO = {
        THEME_STANDARD: {'for': 'どの事業所でも', 'desc': '標準の画面。左のメニューから日誌・予定・計画まで行き来できます。'},
        THEME_LARGE:    {'for': 'パート職員・年配の方', 'desc': '文字を約1.6倍にし、ボタンも指で押しやすい大きさに。使う項目を絞り、色のコントラストを上げています。'},
        THEME_STYLISH:  {'for': '見せる場面が多い事業所', 'desc': '濃色の背景で、装飾を抑えた画面。見学や説明会でお見せする機会が多いときに。'},
        THEME_SIMPLE:   {'for': 'とにかく迷わせたくない現場', 'desc': 'メニューを隠し、その日にやることだけを順番に出す画面。①メモを入れる ②下書きをつくる ③確認して確定 の3つだけ。'},
    }
    ui_theme = models.CharField(max_length=20, choices=THEME_CHOICES, default=THEME_STANDARD,
                                verbose_name='画面の見た目')

    class Meta:
        verbose_name = '職員アカウント'
        verbose_name_plural = '職員アカウント'

    def __str__(self):
        return self.display_name or self.username

    @property
    def is_admin(self):
        return self.role == self.ROLE_ADMIN

    @property
    def is_child_dev_manager(self):
        return self.role == self.ROLE_CHILD_DEV_MANAGER

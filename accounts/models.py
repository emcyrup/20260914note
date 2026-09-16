import secrets

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.urls import reverse
from django.utils import timezone


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
    # 開発向けユーザー：すべての事業所を見られ、画面上で「いまの事業所」を切り替えて動作確認できる
    is_developer = models.BooleanField(default=False, verbose_name='開発向けユーザー',
                                       help_text='すべての事業所にアクセスでき、画面上で事業所を切り替えられます。')

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
    # 細かい表示設定（フォント・ライト/ダーク・背景色・文字の大きさ）。テーマとは独立に効く
    ui_prefs = models.JSONField(default=dict, blank=True, verbose_name='表示の細かい設定')

    FONT_CHOICES = [
        ('biz',     'BIZ UDPゴシック（標準・読みやすいユニバーサルデザイン）'),
        ('noto',    'Noto Sans JP（ゴシック）'),
        ('rounded', 'M PLUS Rounded 1c（丸ゴシック・やわらかい）'),
        ('mincho',  'Noto Serif JP（明朝）'),
        ('system',  '端末の標準フォント'),
    ]
    FONT_STACKS = {
        'biz':     '"BIZ UDPGothic", "Yu Gothic", "Hiragino Kaku Gothic ProN", sans-serif',
        'noto':    '"Noto Sans JP", "Hiragino Kaku Gothic ProN", "Yu Gothic", sans-serif',
        'rounded': '"M PLUS Rounded 1c", "Hiragino Maru Gothic ProN", "Yu Gothic", sans-serif',
        'mincho':  '"Noto Serif JP", "Yu Mincho", "Hiragino Mincho ProN", serif',
        'system':  'system-ui, -apple-system, "Segoe UI", "Hiragino Kaku Gothic ProN", "Yu Gothic", sans-serif',
    }
    FONT_GOOGLE = {
        'biz':     'BIZ+UDPGothic:wght@400;700',
        'noto':    'Noto+Sans+JP:wght@400;700',
        'rounded': 'M+PLUS+Rounded+1c:wght@400;700',
        'mincho':  'Noto+Serif+JP:wght@400;700',
    }
    MODE_CHOICES = [('light', 'ライト'), ('dark', 'ダーク'), ('auto', '端末の設定に合わせる')]
    SCALE_CHOICES = [(90, '小さめ 90%'), (100, '標準 100%'), (115, '大きめ 115%'), (130, 'かなり大きめ 130%')]
    BG_PRESETS = [
        ('', '標準（テーマの色）'), ('#ffffff', '白'), ('#f3efe7', '生成り'), ('#eaf2f5', '薄い水色'),
        ('#eef3e8', '薄い緑'), ('#f8ecec', '薄い桃'), ('#fbf3dc', '薄い黄'),
        ('#0f1f24', '濃紺（ダーク向け）'), ('#1e1e1e', '墨（ダーク向け）'),
    ]

    @property
    def prefs(self):
        """正規化した表示設定（テンプレート用）"""
        p = self.ui_prefs if isinstance(self.ui_prefs, dict) else {}
        font = p.get('font') if p.get('font') in self.FONT_STACKS else 'biz'
        mode = p.get('mode') if p.get('mode') in dict(self.MODE_CHOICES) else 'light'
        try:
            scale = int(p.get('scale', 100))
        except (TypeError, ValueError):
            scale = 100
        if scale not in dict(self.SCALE_CHOICES):
            scale = 100
        bg = str(p.get('bg', '') or '').lower()
        import re as _re
        if not _re.fullmatch(r'#[0-9a-f]{6}', bg):
            bg = ''
        return {
            'font': font, 'font_stack': self.FONT_STACKS[font], 'font_google': self.FONT_GOOGLE.get(font, ''),
            'mode': mode, 'scale': scale, 'bg': bg,
        }

    class Meta:
        verbose_name = '職員アカウント'
        verbose_name_plural = '職員アカウント'

    def __str__(self):
        return self.display_name or self.username

    @property
    def is_admin(self):
        return self.role == self.ROLE_ADMIN

    @property
    def can_switch_facility(self):
        """開発向けユーザー（またはスーパーユーザー）は事業所を切り替えられる"""
        return self.is_developer or self.is_superuser

    @property
    def can_manage_settings(self):
        """
        事業所の設定を変えられるか。
        管理者のほか、開発向けユーザー（切り替えて設定を整える役）も変えられる。
        """
        return self.is_admin or self.is_superuser or self.can_switch_facility

    def save(self, *args, **kwargs):
        # 開発向けユーザーが事業所を切り替えている間は、その一時的な所属を保存してしまわない
        if getattr(self, '_facility_switched', False) and self.pk and kwargs.get('update_fields') is None:
            kwargs['update_fields'] = [f.name for f in self._meta.concrete_fields
                                       if f.name not in ('id', 'facility')]
        super().save(*args, **kwargs)

    @property
    def is_child_dev_manager(self):
        return self.role == self.ROLE_CHILD_DEV_MANAGER


def _new_token():
    return secrets.token_urlsafe(24)


class StaffInvitation(models.Model):
    """
    職員の招待リンク。管理者が発行し、URL を渡された本人が自分でアカウントを作る。
    発行した施設・権限区分で作られ、期限と回数で無効になる。
    """
    facility   = models.ForeignKey('facilities.Facility', on_delete=models.CASCADE, related_name='invitations', verbose_name='施設')
    token      = models.CharField(max_length=64, unique=True, default=_new_token, editable=False)
    role       = models.CharField(max_length=20, choices=StaffAccount.ROLE_CHOICES, default=StaffAccount.ROLE_STAFF, verbose_name='権限区分')
    note       = models.CharField(max_length=100, blank=True, verbose_name='メモ', help_text='誰に渡すか（例：4月入職の3名）')
    max_uses   = models.PositiveSmallIntegerField(default=1, verbose_name='使える回数')
    used_count = models.PositiveSmallIntegerField(default=0, verbose_name='使われた回数')
    expires_at = models.DateTimeField(verbose_name='有効期限')
    is_active  = models.BooleanField(default=True, verbose_name='有効')
    created_by = models.ForeignKey(StaffAccount, on_delete=models.SET_NULL, null=True, blank=True, related_name='+', verbose_name='発行者')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = '職員の招待リンク'
        verbose_name_plural = '職員の招待リンク'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.facility} / {self.get_role_display()} / {self.note or self.token[:8]}'

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    @property
    def is_used_up(self):
        return self.used_count >= self.max_uses

    @property
    def is_usable(self):
        return self.is_active and not self.is_expired and not self.is_used_up

    @property
    def status_label(self):
        if not self.is_active:
            return '取り消し'
        if self.is_expired:
            return '期限切れ'
        if self.is_used_up:
            return '使用済み'
        return '有効'

    def get_absolute_url(self):
        return reverse('accounts:join', args=[self.token])

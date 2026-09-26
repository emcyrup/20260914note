"""
放課後等デイサービス業務支援システム v2 — Django設定ファイル
環境変数は .env ファイルから読み込む（python-decouple使用）
"""

from pathlib import Path
from decouple import config, Csv

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = config('SECRET_KEY')
DEBUG = config('DEBUG', default=False, cast=bool)
ALLOWED_HOSTS = config('DJANGO_ALLOWED_HOSTS', default='localhost', cast=Csv())
# コンテナ内のヘルスチェック（127.0.0.1）やサーバー内からの確認（localhost）を
# DJANGO_ALLOWED_HOSTS の設定に関係なく通す。外部からはこのホスト名で到達できないので安全
for _local_host in ('127.0.0.1', 'localhost'):
    if _local_host not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_local_host)

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # 自作アプリ
    'accounts',
    'facilities',
    'beneficiaries',
    'schedules',
    'records',
    'ai_assist',
    'support_plans',
    'billing',
    'ai_features',
    'esignatures',
    'line_integration',
    'reports',
    'custom_forms',
    'reservations',
    'planbook',
    'therapy',
    'minutes',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',  # 静的ファイルをDjangoから直接配信
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'accounts.middleware.DevFacilityMiddleware',  # 開発向けユーザーの事業所切り替え
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'facilities.context_processors.branding',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# データベース設定
# DB_NAME が設定されている場合は PostgreSQL、なければ SQLite（ローカル開発用）
DB_NAME = config('DB_NAME', default='')
if DB_NAME:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': DB_NAME,
            'USER': config('DB_USER', default=''),
            'PASSWORD': config('DB_PASSWORD', default=''),
            'HOST': config('DB_HOST', default='localhost'),
            'PORT': config('DB_PORT', default='5432'),
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# カスタムユーザーモデル（StaffAccountで職員管理を行う）
AUTH_USER_MODEL = 'accounts.StaffAccount'

LANGUAGE_CODE = 'ja'
TIME_ZONE = 'Asia/Tokyo'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = config('STATIC_ROOT', default=str(BASE_DIR / 'staticfiles'))

MEDIA_URL = '/media/'
# Docker ではボリュームを割り当てるため環境変数で差し替えられるようにする
MEDIA_ROOT = Path(config('MEDIA_ROOT', default=str(BASE_DIR / 'media')))
# DEBUG=False でも Django が /media/ を配信する（前段の nginx/Caddy が配信できない構成向け）

# リバースプロキシ（Caddy/Nginx）越しの HTTPS で POST を受けるために必要（例: https://app.example.com）
CSRF_TRUSTED_ORIGINS = config('CSRF_TRUSTED_ORIGINS', default='', cast=Csv())

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ログイン設定
LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/accounts/login/'

# Anthropic Claude API
# 自己登録
# ALLOW_FACILITY_SIGNUP=True にすると、ログイン画面から「新しい事業所として登録」ができる（事業所＋管理者を自分で作る）。
# SIGNUP_CODE を設定すると、そのコードを知っている人だけが登録できる。職員の自己登録は招待リンク（管理者が発行）で常に可能。
ALLOW_FACILITY_SIGNUP = config('ALLOW_FACILITY_SIGNUP', default=False, cast=bool)
SIGNUP_CODE = config('SIGNUP_CODE', default='')

# 予約管理を単体で別サーバーに置くとき（横展開用）
# RESERVATION_ONLY=True にすると、予約・顧客・公式LINE・設定だけの画面構成になる。
# 同じコードのまま、URL とサイドバーを予約管理だけに絞って動かすための切り替え。
RESERVATION_ONLY = config('RESERVATION_ONLY', default=False, cast=bool)
# 顧客向けページの URL をLINEの文面に載せるときの入口（例：https://yoyaku.example.jp）
RESERVATION_SITE_URL = config('RESERVATION_SITE_URL', default='')
if RESERVATION_ONLY:
    ROOT_URLCONF = 'config.urls_reservation'
    LOGIN_REDIRECT_URL = '/reservations/'

ANTHROPIC_API_KEY = config('ANTHROPIC_API_KEY', default='')
# 活動→めあて・考察の生成に使うモデル（利用できない場合は .env で差し替える）
AI_PLAN_MODEL = config('AI_PLAN_MODEL', default='claude-opus-5')
# 文章整え・一括生成・チャット・加算提案に使うモデル（日本語の品質を優先。費用を抑えるなら .env で claude-sonnet-5 に）
AI_TEXT_MODEL = config('AI_TEXT_MODEL', default='claude-opus-5')
# 音声の文字起こし（Google Cloud Speech-to-Text）。iPhone の音声入力で使う（ai_assist/speech.py）。空なら使わない
GOOGLE_SPEECH_API_KEY = config('GOOGLE_SPEECH_API_KEY', default='')
GOOGLE_SPEECH_MODEL = config('GOOGLE_SPEECH_MODEL', default='latest_long')
# 日誌を保存したときに AI が加算を提案する（API キーが無ければ何もしない）
AI_ADDON_SUGGESTIONS = config('AI_ADDON_SUGGESTIONS', default=True, cast=bool)

# メール（期限のお知らせ `python manage.py send_reminders`）。EMAIL_HOST が空なら送れない（画面のお知らせだけ使う）
EMAIL_HOST = config('EMAIL_HOST', default='')
EMAIL_PORT = config('EMAIL_PORT', default=587, cast=int)
EMAIL_HOST_USER = config('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='')
EMAIL_USE_TLS = config('EMAIL_USE_TLS', default=True, cast=bool)
DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL', default=EMAIL_HOST_USER or 'webmaster@localhost')
EMAIL_BACKEND = config('EMAIL_BACKEND', default=('django.core.mail.backends.smtp.EmailBackend' if EMAIL_HOST
                                                 else 'django.core.mail.backends.console.EmailBackend'))

# LINE Messaging API
LINE_CHANNEL_ACCESS_TOKEN = config('LINE_CHANNEL_ACCESS_TOKEN', default='')
LINE_CHANNEL_SECRET = config('LINE_CHANNEL_SECRET', default='')

# 本番環境（DEBUG=False）のみ有効にするHTTPSセキュリティ設定
if not DEBUG:
    # HTTPS で運用するか。ドメイン未設定で http://IP を検証する間だけ False にする
    # （False の間は Cookie の Secure 属性も外れ、HTTP でログインできる）
    USE_HTTPS = config('SECURE_SSL_REDIRECT', default=True, cast=bool)
    # Caddy/NginxプロキシからのHTTPSを正しく認識する
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    # セッションIDとCSRFトークンをHTTPSのみで送信（通信傍受対策）
    SESSION_COOKIE_SECURE = USE_HTTPS
    CSRF_COOKIE_SECURE = USE_HTTPS
    # HTTPアクセスをHTTPSへ強制リダイレクト
    SECURE_SSL_REDIRECT = USE_HTTPS
    # コンテナのヘルスチェックはHTTPで来るためリダイレクト対象から外す
    SECURE_REDIRECT_EXEMPT = [r'^healthz/$']
    if USE_HTTPS:
        # ブラウザに1年間HTTPSを強制させる（HSTS）
        SECURE_HSTS_SECONDS = 31536000
        SECURE_HSTS_INCLUDE_SUBDOMAINS = True

# ログ: アプリ内の例外を gunicorn の標準エラー（docker compose logs app）に出す
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {'console': {'class': 'logging.StreamHandler'}},
    'root': {'handlers': ['console'], 'level': 'WARNING'},
    'loggers': {
        'django.request': {'handlers': ['console'], 'level': 'ERROR', 'propagate': False},
    },
}

# messages.error() を Bootstrap の赤い枠（alert-danger）で出す（既定の "error" には色が付かない）
from django.contrib.messages import constants as _message_constants  # noqa: E402
MESSAGE_TAGS = {_message_constants.ERROR: 'danger'}

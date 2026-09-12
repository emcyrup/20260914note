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
    'support_plans',
    'billing',
    'ai_features',
    'esignatures',
    'line_integration',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',  # 静的ファイルをDjangoから直接配信
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
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

# リバースプロキシ（Caddy/Nginx）越しの HTTPS で POST を受けるために必要（例: https://app.example.com）
CSRF_TRUSTED_ORIGINS = config('CSRF_TRUSTED_ORIGINS', default='', cast=Csv())

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ログイン設定
LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/accounts/login/'

# Anthropic Claude API
ANTHROPIC_API_KEY = config('ANTHROPIC_API_KEY', default='')

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

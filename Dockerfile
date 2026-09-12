# =============================================
# 放課後等デイサービス業務支援システム — 本番用イメージ
#   Django 6 + Gunicorn + WeasyPrint（PDF）+ 日本語フォント
# =============================================
FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# WeasyPrint の描画ライブラリと日本語フォント（Noto CJK）、ヘルスチェック用 curl
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz0b \
        libharfbuzz-subset0 \
        libcairo2 \
        libgdk-pixbuf-2.0-0 \
        libffi8 \
        libjpeg62-turbo \
        shared-mime-info \
        fonts-noto-cjk \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 依存パッケージ（requirements.txt が変わらない限りキャッシュが効く）
COPY requirements.txt .
RUN pip install -r requirements.txt

# アプリ本体
COPY . .

# 静的ファイルを収集（ビルド時なので SECRET_KEY はダミーで可）
RUN SECRET_KEY=build-only DJANGO_ALLOWED_HOSTS=* STATIC_ROOT=/app/staticfiles \
    python manage.py collectstatic --noinput

# 非rootで実行。写真などのアップロード先は /app/media（compose でボリュームを割り当てる）
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /app/media \
    && chown -R app:app /app
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/healthz/ || exit 1

CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "2", "--threads", "2", \
     "--timeout", "120", \
     "--access-logfile", "-", "--error-logfile", "-"]

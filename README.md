# 放課後等デイサービス業務支援システム v2

放課後等デイサービス事業所の職員向け業務支援Webアプリケーションです。

## 主な機能

- 利用者台帳（受給者証・保護者管理・OCR読み取り）
- 予定管理（月次カレンダー・一括生成・ステータス管理）
- 日次記録（AI文章整え・音声入力・電子サイン・LINE配信）
- 請求管理（請求マトリックス・上限額管理・請求書PDF・領収書PDF）
- LINE連携（保護者向けメッセージ配信・Webhook）
- 施設設定（タグ管理・加算マスタ）

## 技術構成

| 役割 | 技術 |
|---|---|
| バックエンド | Python 3 / Django 6 |
| データベース | PostgreSQL（pgvector拡張対応） |
| フロントエンド | Bootstrap 5 / HTMX / Alpine.js |
| AI | Anthropic Claude API |
| PDF生成 | WeasyPrint |
| LINE連携 | LINE Messaging API |
| 本番サーバー | Gunicorn + Nginx |

## ローカル開発環境のセットアップ

```bash
# 1. リポジトリをクローン
git clone <リポジトリURL>
cd <プロジェクトフォルダ>

# 2. 仮想環境を作成・有効化
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 3. 依存パッケージをインストール
pip install -r requirements.txt

# 4. 環境変数ファイルを作成
cp .env.example .env
# .env を編集して各種APIキーを設定する

# 5. データベースを構築
python manage.py migrate

# 6. 管理者アカウントを作成
python manage.py createsuperuser

# 7. 開発サーバーを起動（macOSはWeasyPrint用にDYLD_LIBRARY_PATHを指定）
DYLD_LIBRARY_PATH=/opt/homebrew/lib python manage.py runserver
```

ブラウザで `http://localhost:8000` を開いてください。

## 環境変数

`.env.example` をコピーして `.env` を作成し、以下の項目を設定してください。

| 変数名 | 説明 |
|---|---|
| `SECRET_KEY` | Djangoのシークレットキー（本番では長いランダム文字列を使用） |
| `DEBUG` | 開発時は `True`、本番は `False` |
| `DJANGO_ALLOWED_HOSTS` | アクセスを許可するホスト名（例: `example.com,localhost`） |
| `DATABASE_URL` | PostgreSQL接続URL（例: `postgresql://user:pass@localhost/dbname`）。空欄の場合はSQLiteを使用 |
| `ANTHROPIC_API_KEY` | Anthropic Claude APIキー |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE Messaging APIのチャネルアクセストークン |
| `LINE_CHANNEL_SECRET` | LINE Messaging APIのチャネルシークレット |

## サーバーへのデプロイ

```bash
# 1. リポジトリをクローン
git clone <リポジトリURL> ~/dayservice
cd ~/dayservice

# 2. 仮想環境を有効化（サーバー共通の仮想環境を使用）
source ~/env/bin/activate

# 3. 依存パッケージをインストール
pip install -r requirements.txt

# 4. .env ファイルを作成して環境変数を設定
cp .env.example .env
vi .env

# 5. データベースを構築
python manage.py migrate

# 6. 静的ファイルを収集
python manage.py collectstatic --noinput

# 7. Gunicornで起動
gunicorn config.wsgi:application --bind 0.0.0.0:8019 --workers 2
```

## 2回目以降のデプロイ（コード更新時）

```bash
cd ~/dayservice
source ~/env/bin/activate
git pull
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
# Gunicornを再起動（systemctlまたはプロセスを再起動）
```

## LINE Webhook の設定

デプロイ後、以下の手順でLINE連携を有効にしてください。

1. 施設設定画面でLINEチャネルアクセストークン・シークレットを入力
2. LINE Developers ConsoleでWebhook URLを登録
   - URL: `https://<ドメイン>/line/webhook/`
   - 「Webhookの利用」をONにする
   - 「応答メッセージ」はOFFにする

## ライセンス

本ソフトウェアは株式会社スリーピース向けに開発されたものです。

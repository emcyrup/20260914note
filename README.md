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
| バックエンド | Python 3.12 以上 / Django 6 |
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
| `DB_NAME` / `DB_USER` / `DB_PASSWORD` / `DB_HOST` / `DB_PORT` | PostgreSQL の接続情報。`DB_NAME` が空欄の場合はSQLiteを使用 |
| `CSRF_TRUSTED_ORIGINS` | リバースプロキシ越しのHTTPSで使うオリジン（例: `https://app.example.com`） |
| `MEDIA_ROOT` | 写真などアップロードファイルの保存先（Dockerではボリュームを割り当てる） |
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

## GCP へのデプロイ（最小コスト構成）

Compute Engine 1台に Docker Compose（Caddy → Gunicorn/Django → PostgreSQL）を載せる構成です。
インフラは Terraform（`infra/terraform/`）、デプロイは GitHub Actions（`.github/workflows/deploy.yml`）が行います。

```
GitHub (main に push)
  └─ Actions: テスト → Docker イメージを GHCR に push → SSH でサーバーへ
                                                          │
Compute Engine（固定IP・e2-small）                          ▼
  └─ docker compose: caddy(443, 自動HTTPS) → app(8000) → db(PostgreSQL)
                      └─ /media は Caddy が配信          └─ 毎日 3:30 pg_dump → Cloud Storage（30日保持）
```

月額の目安（東京 asia-northeast1・e2-small）：VM 約 $16 ＋ ディスク 約 $1.5 ＋ 外部IP 約 $3.7 ＋ Cloud Storage 数十円 ≒ **約 $21**。
`machine_type = "e2-micro"` にすると約 $13、さらに `region = "us-central1"` にすると Always Free 枠で VM 代が $0 になります（日本からの遅延は増えます）。
Cloud SQL を使わないぶん安く、代わりに DB はサーバー内のコンテナで可用性は1台分です。

### 1. GCP プロジェクトと手元の準備（1回）

```bash
brew install --cask google-cloud-sdk && brew install terraform
gcloud auth login
gcloud projects create dayservice-prod-123456 --name="放デイ業務支援"   # ID は世界で一意
gcloud billing accounts list
gcloud billing projects link dayservice-prod-123456 --billing-account=XXXXXX-XXXXXX-XXXXXX
gcloud config set project dayservice-prod-123456
gcloud auth application-default login          # Terraform が使う認証
```

### 2. インフラを作る（1回）

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars   # project_id を書き換える
terraform init
terraform apply
terraform output          # static_ip / ssh_command / backup_bucket が出る
```

DNS の A レコードを `static_ip` に向けてください（ドメインなしでも `http://<IP>` で検証できます）。

### 3. サーバーに .env を置く（1回）

```bash
ssh deploy@<static_ip>
# 初回起動時に deploy/setup-server.sh が Docker などを入れている（ログ: /var/log/cloud-init-output.log）
nano /opt/dayservice/.env      # deploy/.env.production.example を元に作成
```

バックアップを Cloud Storage に送るには `.env` に `BACKUP_GCS_BUCKET=<terraform output backup_bucket>` を足すだけです（VM のサービスアカウントで認証されるためキーは不要）。

### 4. GitHub Secrets を登録する（1回）

| Secret | 値 |
|---|---|
| `DEPLOY_HOST` | `terraform output static_ip` |
| `DEPLOY_SSH_KEY` | `~/.ssh/id_ed25519` の中身（Terraform に登録した公開鍵の対） |
| `DEPLOY_USER` | `deploy`（terraform の `ssh_user` を変えた場合のみ。省略時は `deploy`） |
| `GHCR_PULL_TOKEN` | `read:packages` 権限の Personal Access Token（リポジトリが Public なら不要） |

### 5. デプロイする

`main` に push するだけです（Actions → Deploy から手動実行も可）。初回はデプロイ後に管理者を作成します。

```bash
ssh deploy@<static_ip>
cd /opt/dayservice && docker compose exec app python manage.py createsuperuser
```

### 運用

```bash
cd /opt/dayservice
docker compose ps                       # 状態
docker compose logs -f app              # アプリのログ
docker compose exec app python manage.py shell
./backup.sh                             # 手動バックアップ（毎日 3:30 に自動実行）
# 復元: gunzip -c backups/db-YYYYMMDD-HHMMSS.sql.gz | docker compose exec -T db psql -U dayservice dayservice
```

全部消すときは、バケットを空にしてから `terraform destroy` します：
`gcloud storage rm -r gs://<backup_bucket>/db && cd infra/terraform && terraform destroy`

ローカルで本番イメージを試す場合:

```bash
docker build -t dayservice .
docker run --rm -p 8000:8000 -e SECRET_KEY=dev -e DEBUG=True -e DJANGO_ALLOWED_HOSTS=localhost dayservice
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

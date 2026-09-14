# AWS 開発環境への移行手順（外部 nginx＋既存 PostgreSQL の共用サーバー）

対象: `https://st-michinotedemo.ai-labo.cloud/` → nginx（HTTPS 終端）→ `127.0.0.1:8029` → app コンテナ → サーバー上の PostgreSQL（DB `michinotedemo`、ユーザー `st_ai_labo_dbuser`）。

GCP 本番（Caddy＋DB コンテナ）とはファイルが違います。この環境では次を使います。

| 役割 | GCP 本番 | AWS 開発環境 |
|---|---|---|
| HTTPS 終端 | Caddy（compose 内） | サーバーの nginx（既存） |
| DB | compose の `db` コンテナ | サーバーの PostgreSQL（既存） |
| compose | `deploy/docker-compose.yml` | `deploy/docker-compose.external.yml`（サーバーでは `docker-compose.yml` の名前で置く） |
| `.env` の雛形 | `deploy/.env.production.example` | `deploy/.env.dev-aws.example` |
| 写真の配信 | Caddy | nginx の `alias`（推奨）または Django（`SERVE_MEDIA=True`） |
| バックアップ | `deploy/backup.sh` | `deploy/backup-external.sh`（ホストの `pg_dump`） |
| 自動配備 | `main` → Deploy | `develop` → Deploy (dev)（手動実行も可） |
| 配置先 | `/opt/dayservice` | `/opt/michinotedemo`（Secrets `DEV_APP_DIR` で変更可） |

秘密情報（DB パスワード、`SECRET_KEY`、Anthropic キー、SSH 秘密鍵）はサーバーの `.env` と GitHub Secrets だけに置きます。

## 1. サーバーの準備（1回）

```bash
# Docker が無ければ
curl -fsSL https://get.docker.com | sudo sh

# 配備用ユーザー（既存のユーザーで docker を実行できるなら不要）
sudo useradd -m -s /bin/bash deploy && sudo usermod -aG docker deploy && sudo usermod -p '*' deploy
sudo mkdir -p /home/deploy/.ssh && sudo chmod 700 /home/deploy/.ssh
# GitHub Actions 用の鍵（秘密鍵は Secrets に登録したらサーバーから消す）
sudo -u deploy ssh-keygen -t ed25519 -N '' -f /home/deploy/.ssh/github_deploy -C github-actions
sudo -u deploy sh -c 'cat /home/deploy/.ssh/github_deploy.pub >> /home/deploy/.ssh/authorized_keys && chmod 600 /home/deploy/.ssh/authorized_keys'
sudo cat /home/deploy/.ssh/github_deploy      # ← この中身を Secrets DEV_DEPLOY_SSH_KEY に
sudo rm /home/deploy/.ssh/github_deploy

# 配置先
sudo mkdir -p /opt/michinotedemo/media /opt/michinotedemo/backups
sudo chown -R deploy:deploy /opt/michinotedemo
```

## 2. PostgreSQL（既存サーバー）

DB とユーザーが未作成なら:

```sql
CREATE ROLE st_ai_labo_dbuser LOGIN PASSWORD '（強いパスワード）';
CREATE DATABASE michinotedemo OWNER st_ai_labo_dbuser ENCODING 'UTF8';
```

app はコンテナから `host.docker.internal`（= Docker ブリッジ経由のホスト）に接続します。ホストの PostgreSQL 側で次を確認してください。

- `postgresql.conf` の `listen_addresses` に `*` か Docker ブリッジのアドレス（通常 `172.17.0.1`）が含まれること（`localhost` だけだと接続できません）
- `pg_hba.conf` に `host michinotedemo st_ai_labo_dbuser 172.16.0.0/12 scram-sha-256` を追加し、`sudo systemctl reload postgresql`
- ファイアウォール（ufw など）で 5432 を Docker ブリッジからだけ許可（外部には開けない）

RDS などを使う場合は `.env` の `DB_HOST` にそのホスト名を入れるだけで、上の設定は不要です。

## 3. `.env` を置く

```bash
sudo -u deploy -i
cd /opt/michinotedemo
nano .env    # deploy/.env.dev-aws.example の内容を貼って値を埋める
chmod 600 .env
```

最低限埋めるもの: `SECRET_KEY`、`DB_PASSWORD`、`ANTHROPIC_API_KEY`（AI を使うなら）。`DJANGO_ALLOWED_HOSTS` と `CSRF_TRUSTED_ORIGINS` は雛形どおり `st-michinotedemo.ai-labo.cloud` です。

## 4. nginx

既存の server ブロックの `location /` を `deploy/nginx.example.conf` に合わせます。要点は3つです。

1. `proxy_set_header X-Forwarded-Proto $scheme;`（これが無いと Django が HTTP と判断してリダイレクトループやログイン失敗になります）
2. `client_max_body_size 100m;`（紙の日誌を最大20枚まとめて取り込むため）
3. `proxy_read_timeout 300s;`（AI 生成・PDF 生成が数十秒かかることがあるため）

写真は `location /media/ { alias /opt/michinotedemo/media/; }` で nginx が配信するのが軽くて確実です。その場合は `.env` の `SERVE_MEDIA=False` にします。alias を書けない場合は `SERVE_MEDIA=True`（既定）のままで Django が配信します。

```bash
sudo nginx -t && sudo systemctl reload nginx
```

## 5. GitHub Secrets と初回デプロイ

リポジトリ → Settings → Secrets and variables → Actions に登録:

| Secret | 値 |
|---|---|
| `DEV_DEPLOY_HOST` | サーバーのホスト名または IP |
| `DEV_DEPLOY_USER` | `deploy`（既定なら省略可） |
| `DEV_DEPLOY_SSH_KEY` | 手順1で作った秘密鍵の中身 |
| `DEV_DEPLOY_SSH_PORT` | SSH が 22 以外のときだけ |
| `DEV_APP_DIR` | `/opt/michinotedemo` 以外に置くときだけ |
| `DEV_HEALTH_URL` | 既定 `https://st-michinotedemo.ai-labo.cloud/healthz/` |

デプロイは次のどちらかです。

- ブランチ `develop` に push する（テスト → イメージ作成 → SSH で `docker compose pull / up -d / migrate` → ヘルスチェック）
- Actions → **Deploy (dev)** → Run workflow で、任意のブランチを選んで手動実行

初回はワークフローが `docker-compose.yml`（外部 nginx 版）と `backup-external.sh` をサーバーに置き、`.env` はサーバー側のものをそのまま使います。

配備後、`https://st-michinotedemo.ai-labo.cloud/healthz/` が `ok` になれば成功です。管理者を作ります。

```bash
cd /opt/michinotedemo
docker compose exec app python manage.py createsuperuser
```

`/admin/` で施設を作り、自分の職員アカウントに所属施設と権限区分を設定してください（GCP からデータを移すなら次の手順で不要です）。

## 6. GCP のデータを移す（任意）

GCP 側（`deploy@dayservice`）:

```bash
cd /opt/dayservice
docker compose exec -T db pg_dump -U "$(grep ^DB_USER .env | cut -d= -f2)" --no-owner --no-acl "$(grep ^DB_NAME .env | cut -d= -f2)" | gzip > /tmp/dayservice.sql.gz
docker run --rm -v dayservice_media:/m -v /tmp:/out alpine tar czf /out/media.tgz -C /m .
```

2つのファイルを AWS サーバーへ `scp` し、AWS 側で:

```bash
cd /opt/michinotedemo
docker compose stop app
gunzip -c /tmp/dayservice.sql.gz | PGPASSWORD="$(grep ^DB_PASSWORD .env | cut -d= -f2)" psql -h 127.0.0.1 -U st_ai_labo_dbuser michinotedemo
tar xzf /tmp/media.tgz -C media/
docker compose up -d app
docker compose exec app python manage.py migrate --noinput
rm /tmp/dayservice.sql.gz /tmp/media.tgz
```

`--no-owner --no-acl` を付けているので、GCP の DB ユーザー名と違っていても復元できます。復元先の DB が空でない場合は先に `DROP SCHEMA public CASCADE; CREATE SCHEMA public;` で空にしてください。

## 7. 運用

```bash
cd /opt/michinotedemo
docker compose ps
docker compose logs --tail=60 app
docker compose exec app python manage.py seed_demo --reset   # 架空データ（実データがあるなら実行しない）
./backup-external.sh                                          # DB バックアップ（backups/ に14日分）
# .env を変えたら
docker compose up -d app
```

cron の例（毎日 3:30）: `30 3 * * * /opt/michinotedemo/backup-external.sh >> /opt/michinotedemo/backups/backup.log 2>&1`。`media/` は別途 rsync などで保管してください。

## つまずきやすい点

| 症状 | 原因と対処 |
|---|---|
| ログインすると `/accounts/login/` に戻る、または CSRF 403 | nginx に `X-Forwarded-Proto` が無い／`CSRF_TRUSTED_ORIGINS` が `https://` 付きでない |
| 400 Bad Request | `DJANGO_ALLOWED_HOSTS` にドメインが無い |
| `could not connect to server` | PostgreSQL が `localhost` しか聞いていない、`pg_hba.conf` に Docker ブリッジの許可が無い、ufw で 5432 が閉じている |
| 写真が 404 | `SERVE_MEDIA=False` なのに nginx の `/media/` alias が無い、または alias のパスが `/opt/michinotedemo/media/` と違う |
| 紙の日誌の取り込みが 413 | nginx の `client_max_body_size` が小さい |
| AI 生成が 504 | nginx の `proxy_read_timeout` が短い |
| GHCR から pull できない | リポジトリを Private にした場合は Secrets に `GHCR_PULL_TOKEN`（read:packages） |

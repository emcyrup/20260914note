# AWS 開発環境への移行手順（共用サーバー：sudo なし・Docker なし・venv＋Gunicorn）

対象: `https://st-michinotedemo.ai-labo.cloud/` → プロバイダ管理の nginx（HTTPS 終端）→ Gunicorn `0.0.0.0:8029` → プロバイダ管理の PostgreSQL（DB `michinotedemo`、ユーザー `st_ai_labo_dbuser`）。

プロバイダの案内（「開発環境のご案内」）の前提:

- Ubuntu、SSH は配布された `.pem` でログイン（例: ユーザー `3piece_user4`、ホーム `/home/dev4`）。**sudo なし**、自分のホーム以外は触れない
- Python の venv は **`~/env`** にプロバイダが作成済み。作業前に `source ~/env/bin/activate`
- nginx・ドメイン・SSL は**プロバイダが一括管理**（自分では触らない）。アプリは割り当てポートで `0.0.0.0` にリッスンし、nohup 等でバックグラウンド起動する
- PostgreSQL の DB・ユーザー・パスワードはプロバイダが発行。**DB のダンプはプロバイダ側で自動化**（自前の cron バックアップは不要）
- 追加パッケージ（OS 側）はプロバイダに依頼

旧 GCP 本番（Docker＋Caddy。2026-09-14 に廃止）との違い:

| 役割 | 旧 GCP 本番 | AWS 開発環境 |
|---|---|---|
| 実行方式 | Docker（`ghcr.io` のイメージ） | `~/env` の venv ＋ Gunicorn（`deploy/venv-deploy.sh`） |
| HTTPS 終端 | Caddy（compose 内） | プロバイダの nginx |
| DB | compose の `db` コンテナ | プロバイダの PostgreSQL（`localhost`） |
| 写真の配信 | Caddy | Django（`.env` の `SERVE_MEDIA=True`） |
| 静的ファイル | whitenoise | whitenoise（同じ） |
| 自動配備 | `main` → Deploy | `develop` → Deploy (dev)（手動実行も可） |
| 配置先 | `/opt/dayservice` | `~/michinotedemo` |
| バックアップ | cron の pg_dump → GCS | プロバイダ側（依頼すればダンプをもらえる） |

秘密情報（DB パスワード、`SECRET_KEY`、Anthropic キー、SSH 秘密鍵）はサーバーの `.env` と GitHub Secrets だけに置きます。

## 1. サーバーに入って確認する（1回）

```bash
ssh -i id_rsa_3piece4.pem 3piece_user4@15.168.83.1
source ~/env/bin/activate
python --version        # 3.12 以上が必要（Django 6.1）。3.11 以下ならプロバイダに python3.12 以上の venv を依頼
git --version
psql --version          # 無くても動く（データ移行のときに使う）
```

PDF 出力（帳票・計画書・請求書）には WeasyPrint が使う OS のライブラリが必要です。次で確認し、無ければプロバイダに依頼してください（無くてもアプリは動き、PDF ボタンだけ「PDF を作成できません」になります。「画面で見る」から印刷はできます）。

```bash
ldconfig -p | grep -E 'libpango-1.0|libpangoft2|libharfbuzz-subset' | head
fc-list | grep -i -E 'noto.*cjk|ipa|takao' | head     # 日本語フォント（無いと PDF の日本語が豆腐になる）
```

依頼する内容（Ubuntu の場合）: `libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-noto-cjk`

## 2. リポジトリを置く

```bash
cd ~
git clone https://github.com/emcyrup/20260914note.git michinotedemo
cd michinotedemo
git checkout develop        # 開発環境用ブランチ（無ければ main）
```

Private リポジトリにした場合は、`~/.ssh` に鍵を作って GitHub に登録し、`git remote set-url origin git@github.com:emcyrup/20260914note.git` にします。

## 3. `.env` を置く

```bash
cp deploy/.env.dev-aws.example .env
nano .env
chmod 600 .env
```

埋めるもの: `SECRET_KEY`、`DB_PASSWORD`、`ANTHROPIC_API_KEY`（AI を使うなら）、`MEDIA_ROOT`（自分のホームのパスに合わせる。例 `/home/dev4/michinotedemo/media`）。`PORT` は割り当てられた **8029**、`DJANGO_ALLOWED_HOSTS` と `CSRF_TRUSTED_ORIGINS` は雛形どおりです。

## 4. 初回起動

```bash
source ~/env/bin/activate
cd ~/michinotedemo
bash deploy/venv-deploy.sh
```

このスクリプトが `pip install -r requirements.txt` → `migrate` → `collectstatic` → Gunicorn 起動（`0.0.0.0:8029`、デーモン、PID は `run/gunicorn.pid`、ログは `logs/`）→ `http://127.0.0.1:8029/healthz/` の確認までを行います。成功したらブラウザで `https://st-michinotedemo.ai-labo.cloud/healthz/` が `ok` になります。

管理者を作ります。

```bash
python manage.py createsuperuser
```

`/admin/` で施設を作り、自分の職員アカウントに所属施設と権限区分を設定してください。

よく使う操作:

```bash
bash deploy/venv-deploy.sh --restart   # .env を変えたあとの再起動（ワーカーだけ入れ替え）
bash deploy/venv-deploy.sh --stop      # 停止
tail -f logs/error.log                 # アプリのエラー
python manage.py seed_demo --reset     # 架空データ（実データがあれば実行しない）
```

サーバー再起動後は Gunicorn が自動では上がりません。`crontab -e` に次を1行入れておくと起動時に立ち上がります（cron が使える場合）。

```
@reboot sleep 20 && APP_DIR=$HOME/michinotedemo VENV=$HOME/env bash $HOME/michinotedemo/deploy/venv-deploy.sh --restart >> $HOME/michinotedemo/logs/boot.log 2>&1
```

## 5. GitHub Actions からの自動配備

リポジトリ → Settings → Secrets and variables → Actions に登録:

| Secret | 値 |
|---|---|
| `DEV_DEPLOY_HOST` | `15.168.83.1` |
| `DEV_DEPLOY_USER` | 配布された SSH ユーザー名（例 `3piece_user4`） |
| `DEV_DEPLOY_SSH_KEY` | 配布された `.pem` の中身（`-----BEGIN` から `END ...-----` まで） |
| `DEV_DEPLOY_SSH_PORT` | 22 以外のときだけ |
| `DEV_APP_DIR` | `~/michinotedemo` 以外に置いたときだけ |
| `DEV_HEALTH_URL` | 既定 `https://st-michinotedemo.ai-labo.cloud/healthz/` |

デプロイは次のどちらかです。

- ブランチ `develop` に push する（テスト → SSH で `deploy/venv-deploy.sh <コミット>` を実行 → 公開 URL のヘルスチェック）
- Actions → **Deploy (dev)** → Run workflow で、任意のブランチを選んで手動実行

ワークフローは clone が無ければ `git clone` から行うので、手順2〜3（`.env`）だけ済ませておけば初回も Actions からできます。`.env` はサーバー側のものをそのまま使い、上書きしません。

## 6. 旧 GCP のデータを移す（廃止前に必要な場合だけ）

GCP 側（`deploy@dayservice`）:

```bash
cd /opt/dayservice
docker compose exec -T db pg_dump -U "$(grep ^DB_USER .env | cut -d= -f2)" --no-owner --no-acl "$(grep ^DB_NAME .env | cut -d= -f2)" | gzip > /tmp/dayservice.sql.gz
docker run --rm -v dayservice_media:/m -v /tmp:/out alpine tar czf /out/media.tgz -C /m .
```

2つのファイルを手元経由で AWS サーバーの `~/` へ `scp` し、AWS 側で:

```bash
source ~/env/bin/activate
cd ~/michinotedemo
bash deploy/venv-deploy.sh --stop
gunzip -c ~/dayservice.sql.gz | PGPASSWORD="$(grep ^DB_PASSWORD .env | cut -d= -f2)" psql -h localhost -U st_ai_labo_dbuser michinotedemo
tar xzf ~/media.tgz -C media/
python manage.py migrate --noinput
bash deploy/venv-deploy.sh --restart
rm ~/dayservice.sql.gz ~/media.tgz
```

`--no-owner --no-acl` を付けているので、GCP の DB ユーザー名と違っていても復元できます。復元先が空でない場合は先に `DROP SCHEMA public CASCADE; CREATE SCHEMA public;`（権限があれば）で空にするか、プロバイダに DB の初期化を依頼してください。`psql` がサーバーに無い場合は、プロバイダに `.sql` の取り込みを依頼するか、`pip install pgcli` 等で代用します。

## 7. プロバイダに依頼しておくとよいこと

| 依頼 | 理由 |
|---|---|
| nginx で `proxy_set_header X-Forwarded-Proto $scheme;` を付ける | Django が HTTPS と判断するため。付いていないとログイン後に `http://` へ飛んで無限リダイレクトになる（その場合は `.env` の `SECURE_SSL_REDIRECT=False` で回避できる） |
| nginx の `client_max_body_size 100m;` | 紙の日誌を最大 20 枚まとめて取り込むため（既定 1MB だと 413） |
| nginx の `proxy_read_timeout 300s;` | AI 生成・PDF 生成が数十秒かかることがあるため（504 対策） |
| `libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-noto-cjk` のインストール | PDF 出力（WeasyPrint）と日本語フォント |
| Python 3.12 以上の venv | Django 6.1 の要件（`python --version` で確認） |

参考: `deploy/nginx.example.conf` に上記を反映した server ブロック例があります。

## つまずきやすい点

| 症状 | 原因と対処 |
|---|---|
| ログイン後に `http://` へ戻る・無限リダイレクト | nginx に `X-Forwarded-Proto` が無い → プロバイダに依頼、当面は `.env` の `SECURE_SSL_REDIRECT=False` |
| CSRF 403 | `CSRF_TRUSTED_ORIGINS` が `https://` 付きのドメインになっていない |
| 400 Bad Request | `DJANGO_ALLOWED_HOSTS` にドメインが無い |
| `could not connect to server` / `password authentication failed` | `.env` の `DB_HOST` `DB_NAME` `DB_USER` `DB_PASSWORD` を確認（プロバイダ発行の値） |
| 写真が 404 | `SERVE_MEDIA=True` になっているか、`MEDIA_ROOT` が実在するパスか |
| PDF ボタンで「PDF を作成できません」 | WeasyPrint のライブラリが無い → 手順7 の依頼。「画面で見る」から印刷は可能 |
| 取り込みが 413 / AI が 504 | nginx の上限・タイムアウト → 手順7 の依頼 |
| `pip install` が失敗 | `source ~/env/bin/activate` を忘れている、または Python が古い |
| サーバー再起動後に落ちている | `bash deploy/venv-deploy.sh --restart`。恒久対策は手順4の `@reboot` |

## 参考：Docker が使えるサーバーの場合

sudo と Docker が使えるサーバー（自前の EC2 など）で同じ「外部 nginx＋外部 PostgreSQL」構成にする場合は、`deploy/docker-compose.external.yml` を `docker-compose.yml` として置き、`.env` の `DB_HOST=host.docker.internal`、`APP_PORT` を設定して `docker compose up -d` します。ホストの PostgreSQL 側で `listen_addresses` と `pg_hba.conf` に Docker ブリッジ（172.16.0.0/12）の許可が必要です。

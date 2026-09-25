# 「発達支援ルーム　ゆあーず」を AWS 共用サーバーで動かす手順（sudo なし・Docker なし・venv＋Gunicorn）

対象: `https://michinote.yours.ai-labo.cloud/` → プロバイダ管理の nginx（HTTPS 終端）→ Gunicorn `0.0.0.0:8030` → プロバイダ管理の PostgreSQL（DB `michinoteyours`、ユーザー `ai_labo_dbuser`）。

開発環境（`https://st-michinotedemo.ai-labo.cloud/`、ポート 8029、`docs/DEPLOY_AWS_DEV.md`）と**同じ仕組み**で、置き場所・ポート・DB・`.env` が違うだけです。同じサーバーに置く場合は、開発環境の clone（`~/michinotedemo`）と venv（`~/env`）はそのまま使い、ゆあーずは別の clone（`~/michinoteyours`）で動かします。venv は共用で構いません（同じリポジトリなので必要なパッケージは同じ）。

GCP のゆあーず用サーバー（`docs/RYOIKU_SERVER.md`、`/opt/ryoiku`、Docker）との違い:

| 項目 | GCP（お試し・現行） | AWS（この手順） |
|---|---|---|
| 実行方式 | Docker（`ghcr.io` のイメージ）＋ Caddy | `~/env` の venv ＋ Gunicorn（`deploy/venv-deploy.sh`） |
| HTTPS 終端 | Caddy（compose 内、`enable-https.sh`） | プロバイダの nginx（自分では触らない） |
| DB | compose の `db` コンテナ（`ryoiku`） | プロバイダの PostgreSQL（`michinoteyours` / `ai_labo_dbuser`） |
| 置き場所 | `/opt/ryoiku` | `~/michinoteyours` |
| ワークフロー | Deploy (ryoiku) | **Deploy (yours)**（`.github/workflows/deploy-yours.yml`） |
| GitHub Secrets | `RYOIKU_*` | **`YOURS_*`** |
| 配備するブランチ | `ryoiku` | `ryoiku`（同じ。push すると両方に配備される） |
| バックアップ | cron の `backup.sh` → GCS | プロバイダ側（依頼すればダンプをもらえる） |
| 写真の配信 | Django | 同じ（nginx で `/media/` を直接配信しない） |

秘密情報（DB パスワード、`SECRET_KEY`、Anthropic キー、Google の API キー、LINE のトークン、SSH 秘密鍵）はサーバーの `.env`・施設設定の画面・GitHub Secrets だけに置きます（リポジトリ・Notion・チャットには書かない）。

## 1. サーバーに入って確認する（1回）

開発環境と同じサーバーなら、Python・WeasyPrint のライブラリ・日本語フォントはすでに確認ずみです。別のサーバーなら `docs/DEPLOY_AWS_DEV.md` の手順 1 と同じ確認をしてください。

```bash
ssh -i <配布された .pem> <SSHユーザー>@<サーバーのIP>
source ~/env/bin/activate
python --version        # 3.12 以上（Django 6.1）
psql --version          # 無くても動く（GCP からデータを移すときに使う）
```

nginx はプロバイダ管理です。`https://michinote.yours.ai-labo.cloud/` → `127.0.0.1:8030` の中継が設定ずみであることが前提です（設定内容の希望は手順 8）。

## 2. リポジトリを置く

```bash
cd ~
git clone https://github.com/emcyrup/20260914note.git michinoteyours
cd michinoteyours
git checkout ryoiku         # ゆあーず用のブランチ（GCP のゆあーずと同じコード）
```

## 3. `.env` を置く

```bash
cp deploy/.env.yours-aws.example .env
nano .env
chmod 600 .env
```

埋めるもの:

| 項目 | 値 |
|---|---|
| `SECRET_KEY` | `python -c "import secrets; print(secrets.token_urlsafe(50))"` で作ったもの（開発環境とは別の値） |
| `DB_PASSWORD` | プロバイダ発行の `ai_labo_dbuser` のパスワード |
| `MEDIA_ROOT` | 自分のホームの絶対パス。例 `/home/dev4/michinoteyours/media`（開発環境の `media` とは別にする） |
| `ANTHROPIC_API_KEY` | AI を使うなら（開発環境と同じキーでよい） |
| `GOOGLE_SPEECH_API_KEY` | iPhone の音声入力を使うなら（手順 7） |

`PORT=8030`、`DJANGO_ALLOWED_HOSTS=michinote.yours.ai-labo.cloud`、`CSRF_TRUSTED_ORIGINS=https://michinote.yours.ai-labo.cloud`、`DB_NAME=michinoteyours`、`DB_USER=ai_labo_dbuser` は雛形どおりです。

## 4. 初回起動

```bash
source ~/env/bin/activate
cd ~/michinoteyours
bash deploy/venv-deploy.sh
```

`venv-deploy.sh` は、自分が置かれている clone（ここでは `~/michinoteyours`）を対象に、`pip install -r requirements.txt` → `migrate` → `collectstatic` → Gunicorn 起動（`0.0.0.0:8030`、PID は `run/gunicorn.pid`、ログは `logs/`）→ `http://127.0.0.1:8030/healthz/` の確認までを行います。成功したらブラウザで `https://michinote.yours.ai-labo.cloud/healthz/` が `ok` になります。

開発環境の Gunicorn（8029）とは PID ファイルもログも別なので、同じサーバーで両方動かせます。

よく使う操作（`cd ~/michinoteyours` で）:

```bash
bash deploy/venv-deploy.sh --restart   # .env を変えたあとの再起動（ワーカーだけ入れ替え）
bash deploy/venv-deploy.sh --stop      # 停止
tail -f logs/error.log                 # アプリのエラー
```

サーバー再起動後は Gunicorn が自動では上がりません。`crontab -e` に開発環境の行と並べて次を1行入れておきます。

```
@reboot sleep 25 && APP_DIR=$HOME/michinoteyours VENV=$HOME/env bash $HOME/michinoteyours/deploy/venv-deploy.sh --restart >> $HOME/michinoteyours/logs/boot.log 2>&1
```

## 5. 事業所と管理者を作る（どちらか）

サーバーに入らずに GitHub の画面から行うこともできます：Actions → **Manage (yours)** → Run workflow（`.github/workflows/manage-yours.yml`。手順 6 の Secrets が登録ずみであること）。

| task | すること |
|---|---|
| `create_facility` | 事業所（既定「発達支援ルーム　ゆあーず」）と管理者（既定 `ryoiku`）を `--preset ryoiku` で作る。「サンプルデータも入れる」がオンなら 5-a の `--demo` と同じ |
| `set_password` | 管理者（職員）のパスワードを「パスワード」欄の値にする。`create_facility` でパスワード欄を空にしたときは、このあと必ず実行する |
| `seed_demo` | サンプルデータを入れ直す（`seed_demo --reset`） |
| `seed_demo_reset` | サンプルデータを消す（実データを入れる前に） |
| `list` | 事業所と職員の一覧を表示する |

パスワード欄の値はログに出ません（マスクし、サーバーへは環境変数で渡します）。このリポジトリは公開なので、ログに秘密の値が出る形の作業は足さないでください。

### 5-a. 新しく作る（GCP のデータを移さない場合）

```bash
source ~/env/bin/activate
cd ~/michinoteyours
python manage.py create_facility "発達支援ルーム　ゆあーず" --admin <管理者のユーザー名> --password '<初期パスワード>' --preset ryoiku
```

`--preset ryoiku` で、時間枠の予約（1枠45分・1枠3人・平日 10〜18 時・土日祝 9〜17 時・月木休）、療育記録、メニュー「基本機能／お試し／設定」、お試しの AI 20 回、請求と予定を使わない設定まで入ります。管理者でログインし、施設設定 → 施設基本情報で住所などを入れてください。職員は、ログイン画面の「職員の新規登録」から登録し、管理者が運用管理で承認します。

### 5-b. GCP のゆあーずからデータを移す

GCP 側（ブラウザで SSH → `sudo -u deploy -i`）:

```bash
cd /opt/ryoiku
docker compose exec -T db pg_dump -U ryoiku --no-owner --no-acl ryoiku | gzip > /tmp/yours.sql.gz
docker run --rm -v ryoiku_media:/m -v /tmp:/out alpine tar czf /out/yours-media.tgz -C /m .
ls -la /tmp/yours.sql.gz /tmp/yours-media.tgz
```

（media ボリュームの名前は `docker volume ls` で確認。`/opt/ryoiku` なら `ryoiku_media`）

2つのファイルを手元経由で AWS サーバーの `~/` へ `scp` し、AWS 側で:

```bash
source ~/env/bin/activate
cd ~/michinoteyours
bash deploy/venv-deploy.sh --stop
# 手順 4 の migrate で作ったテーブルを空にしてから復元する（権限が無ければプロバイダに DB の初期化を依頼）
PGPASSWORD="$(grep ^DB_PASSWORD .env | cut -d= -f2-)" psql -h localhost -U ai_labo_dbuser michinoteyours -c 'DROP SCHEMA public CASCADE; CREATE SCHEMA public;'
gunzip -c ~/yours.sql.gz | PGPASSWORD="$(grep ^DB_PASSWORD .env | cut -d= -f2-)" psql -h localhost -U ai_labo_dbuser michinoteyours
tar xzf ~/yours-media.tgz -C media/
python manage.py migrate --noinput
bash deploy/venv-deploy.sh --restart
rm ~/yours.sql.gz ~/yours-media.tgz
```

`--no-owner --no-acl` を付けているので、DB ユーザー名が `ryoiku` から `ai_labo_dbuser` に変わっても復元できます。職員のアカウント・パスワード・LINE の設定（チャネルシークレット等）もそのまま移ります。

## 6. GitHub Actions からの自動配備

リポジトリ → Settings → Secrets and variables → Actions に登録（開発環境の `DEV_*` とは別に）:

| Secret | 値 |
|---|---|
| `YOURS_DEPLOY_HOST` | サーバーの IP（開発環境と同じサーバーなら `DEV_DEPLOY_HOST` と同じ値） |
| `YOURS_DEPLOY_USER` | 配布された SSH ユーザー名 |
| `YOURS_DEPLOY_SSH_KEY` | 配布された `.pem` の中身（`-----BEGIN` から `END ...-----` まで） |
| `YOURS_DEPLOY_SSH_PORT` | 22 以外のときだけ |
| `YOURS_APP_DIR` | `~/michinoteyours` 以外に置いたときだけ |
| `YOURS_VENV` | `~/env` 以外の venv を使うときだけ |
| `YOURS_HEALTH_URL` | 既定 `https://michinote.yours.ai-labo.cloud/healthz/` |

デプロイは次のどちらかです。

- ブランチ `ryoiku` に push する（テスト → SSH で `deploy/venv-deploy.sh <コミット>` を実行 → 公開 URL のヘルスチェック）。同じ push で GCP の Deploy (ryoiku) も走るので、両方のサーバーが同じコードになります
- Actions → **Deploy (yours)** → Run workflow で、任意のブランチを選んで手動実行

`YOURS_DEPLOY_HOST` `YOURS_DEPLOY_USER` `YOURS_DEPLOY_SSH_KEY` が未登録の間は、テストだけ通して配備をスキップします（失敗にはなりません）。ワークフローは clone が無ければ `git clone` から行うので、手順 3（`.env`）だけ済ませておけば初回も Actions からできます。`.env` はサーバー側のものをそのまま使い、上書きしません。

GCP のサーバーをやめるときは、`RYOIKU_DEPLOY_HOST` と `RYOIKU_DEPLOY_SSH_KEY` の Secret を消せば Deploy (ryoiku) は配備をスキップするようになります（VM・固定 IP・バケットの削除は `docs/RYOIKU_SERVER.md` 3-8）。

## 7. 公式 LINE と iPhone の音声入力（ドメインが変わるので確認）

**公式 LINE**（`docs/RYOIKU_SERVER.md` 3-7b）
- LINE Developers → Messaging API 設定 → **Webhook URL** を新しいドメインに変える：`https://michinote.yours.ai-labo.cloud/line/webhook/<施設ID>/`（アプリの「予約 → 公式LINE」の一番上に出る URL をそのまま貼る）→「検証」で成功を確認。
- チャネルシークレットとチャネルアクセストークンは施設設定の画面に入れます（データを移した場合はそのまま移っています。新しく作った場合は入れ直す）。
- 保護者に渡した利用希望の入力 URL・登録コードは、新しいドメインで送り直します（古い GCP の URL は GCP を止めると開けなくなります）。

**iPhone の音声入力**（`docs/RYOIKU_SERVER.md` 3-7c）
- Google Cloud の API キーを「IP アドレス」で制限している場合は、AWS サーバーの IP（外向きの IP。`curl -s https://api.ipify.org` で確認）を許可に足す。制限がキーの種類（Cloud Speech-to-Text API だけ）だけなら変更なし。
- `.env` の `GOOGLE_SPEECH_API_KEY` に同じキーを入れて `bash deploy/venv-deploy.sh --restart`。

## 8. プロバイダに依頼しておくとよいこと（開発環境と同じ）

nginx の `michinote.yours.ai-labo.cloud` の server ブロックに、開発環境と同じ3点をお願いします（`deploy/nginx.example.conf` 参照。ポートは 8030）。

| 依頼 | 理由 |
|---|---|
| `proxy_set_header X-Forwarded-Proto $scheme;` | Django が HTTPS と判断するため。無いとログイン後に無限リダイレクト（当面は `.env` の `SECURE_SSL_REDIRECT=False` で回避） |
| `client_max_body_size 100m;` | 紙の利用希望・紙の日誌を写真でまとめて取り込むため（既定 1MB だと 413） |
| `proxy_read_timeout 300s;` | AI 生成・PDF 生成・音声の文字起こしが数十秒かかることがあるため（504 対策） |

別のサーバーの場合は `libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-noto-cjk`（PDF と日本語フォント）と Python 3.12 以上の venv も依頼します。

## つまずきやすい点

| 症状 | 原因と対処 |
|---|---|
| `healthz` が `ok` にならない | `tail logs/error.log`。`.env` の `DB_*`（`michinoteyours` / `ai_labo_dbuser`）と `PORT=8030` を確認。nginx の中継先が 8030 か |
| 開発環境が止まった・入れ替わった | `~/michinotedemo` と `~/michinoteyours` を取り違えている。`cd` してから `venv-deploy.sh` を実行する（スクリプトは自分の clone を対象にする） |
| ログイン後に `http://` へ戻る・無限リダイレクト | nginx に `X-Forwarded-Proto` が無い → 手順 8。当面は `SECURE_SSL_REDIRECT=False` |
| CSRF 403 | `CSRF_TRUSTED_ORIGINS=https://michinote.yours.ai-labo.cloud` になっていない |
| 400 Bad Request | `DJANGO_ALLOWED_HOSTS` にドメインが無い |
| `password authentication failed` | `DB_PASSWORD` がプロバイダ発行の値と違う |
| 写真が 404 | `MEDIA_ROOT` が実在するパスか（データを移した場合は `tar` の展開先がそのパスか） |
| LINE の「検証」が失敗 | Webhook URL が新しいドメインになっているか、施設設定にシークレットとトークンが入っているか |
| iPhone の録音が文字にならない | `.env` の `GOOGLE_SPEECH_API_KEY`、キーの IP 制限（手順 7）。画面の「うまく文字にならないとき」→「記録を写す」で `server=true` か |
| 取り込みが 413 / AI が 504 | nginx の上限・タイムアウト → 手順 8 |
| Actions の Deploy (yours) が「スキップ」 | `YOURS_DEPLOY_HOST` `YOURS_DEPLOY_USER` `YOURS_DEPLOY_SSH_KEY` の登録 |

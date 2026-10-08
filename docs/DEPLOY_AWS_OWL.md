# 「児童発達支援センター　オウル」を AWS 共用サーバーで動かす手順（ゆあーずと同じサーバー・別ポート・別 DB）

対象: `https://michinote.owl.ai-labo.cloud/` → プロバイダ管理の nginx（HTTPS 終端。設定はゆあーずと同じ）→ Gunicorn `0.0.0.0:8034` → プロバイダ管理の PostgreSQL（DB `michinoteowl`、ユーザー `ai_labo_dbuser`）。

ゆあーず（`docs/DEPLOY_AWS_YOURS.md`。`~/michinoteyours`・ポート 8030・DB `michinoteyours`）と**同じサーバー・同じ仕組み**で、置き場所・ポート・DB・`.env` が違うだけです。venv（`~/env`）は共用します。同じアプリを別々に動かすので、オウルの職員・利用者・予約はゆあーずからは見えません（DB が別）。

| 項目 | ゆあーず | オウル |
|---|---|---|
| URL | `https://michinote.yours.ai-labo.cloud/` | `https://michinote.owl.ai-labo.cloud/` |
| Gunicorn | `0.0.0.0:8030` | `0.0.0.0:8034` |
| 置き場所 | `~/michinoteyours` | `~/michinoteowl` |
| DB | `michinoteyours` / `ai_labo_dbuser` | `michinoteowl` / `ai_labo_dbuser`（同じユーザー・同じパスワード） |
| `.env` の雛形 | `deploy/.env.yours-aws.example` | `deploy/.env.owl-aws.example` |
| 配備 | Deploy (yours) | **Deploy (owl)**（`.github/workflows/deploy-owl.yml`） |
| 管理 | Manage (yours) | **Manage (owl)**（既定の事業所名「児童発達支援センター　オウル」・管理者 `owl`） |
| .env の決まった項目 | Set env (yours) | Set env (owl) |
| 外からの確認・ログ | Site check (yours) / Log check (yours) | Site check (owl) / Log check (owl) |
| Secrets | `YOURS_*` | **`YOURS_*` をそのまま使う**（別にしたいときだけ `OWL_*`） |
| 配備するブランチ | `ryoiku` | `ryoiku`（同じ push で両方に配備される） |

秘密情報（DB パスワード、`SECRET_KEY`、Anthropic キー、SSH 秘密鍵）はサーバーの `.env`・GitHub Secrets だけに置きます（リポジトリ・Notion・チャット・Actions のログには書かない）。

## 1. 準備（プロバイダ側。2026-10-06 に設定ずみ）

- nginx：`michinote.owl.ai-labo.cloud` → `127.0.0.1:8034`（HTTPS。`X-Forwarded-Proto`・`client_max_body_size 100m`・`proxy_read_timeout 300s` はゆあーずと同じ）
- PostgreSQL：DB `michinoteowl`、ユーザー `ai_labo_dbuser`（ゆあーずと同じユーザー）

## 2. 初回の配備（Actions から。サーバーに入らなくてよい）

Actions → **Deploy (owl)** → Run workflow（ブランチ `ryoiku`）。`ryoiku` に push したときも自動で動きます。

初回は、サーバーに `~/michinoteowl` を `git clone` し、`.env` が無ければ `deploy/.env.owl-aws.example` から作ります：

- `SECRET_KEY` はサーバー上で生成（ゆあーずとは別の値）
- `DB_PASSWORD`・`ANTHROPIC_API_KEY`・`AI_TEXT_MODEL`・`AI_PLAN_MODEL`・`GOOGLE_SPEECH_API_KEY`・`SPEECH_BACKEND`・`WHISPER_*` は **`~/michinoteyours/.env` から写す**（同じ DB ユーザー・同じサーバーなので同じ値でよい。ログには項目名だけ出て、値は出ない）
- `MEDIA_ROOT=~/michinoteowl/media`（ゆあーずの写真・書類とは別の場所）
- `EMAIL_*`（期限のお知らせのメール）は写さない。使うときは手順 5

そのあと `deploy/venv-deploy.sh` が `pip install` → `migrate` → `collectstatic` → Gunicorn 起動（`0.0.0.0:8034`、PID は `~/michinoteowl/run/gunicorn.pid`、ログは `~/michinoteowl/logs/`）→ `https://michinote.owl.ai-labo.cloud/healthz/` の確認まで行います。2 回目以降は `.env` を上書きしません。

ゆあーずの Deploy (yours)・Manage (yours) と同じ列（concurrency `deploy-yours`）に並ぶので、同じ venv で `pip` が同時に動くことはありません。

## 3. 事業所と管理者を作る

Actions → **Manage (owl)** → Run workflow：

1. `task=create_facility`、事業所名「児童発達支援センター　オウル」（既定）、管理者 `owl`（既定）、パスワード欄に決めたパスワード（空ならランダムな仮の値になり、あとで `set_password` が要る）、サンプルデータは既定で入れない。`--preset ryoiku`（時間枠の予約・療育記録・メニュー「基本機能／お試し／設定」・お試しの AI 20 回・請求と予定を使わない）に加えて、**送迎・配車と 5領域アセスメントを使う**設定にします。
2. `https://michinote.owl.ai-labo.cloud/` に `owl` でログインし、施設設定 → 施設基本情報で住所などを入れる。職員はログイン画面の「職員の新規登録」から登録し、管理者が運用管理で承認する。

ほかの task（`set_password`・`list`・`seed_demo`・`delete_facility`・`error_log`・`set_media_root`）は Manage (yours) と同じです（`set_media_root` は `~/michinoteowl/media` に直す）。

## 4. サーバー再起動のとき

Gunicorn は自動では上がりません。ゆあーずの行と並べて `crontab -e` に 1 行入れておくか、Deploy (owl) を手動実行します。

```
@reboot sleep 30 && APP_DIR=$HOME/michinoteowl VENV=$HOME/env bash $HOME/michinoteowl/deploy/venv-deploy.sh --restart >> $HOME/michinoteowl/logs/boot.log 2>&1
```

**いまの手順**：上の行を手で入れる代わりに、Actions の **Manage (owl)** を task `autostart_install` で実行すると、`@reboot` と 5 分ごとの見回り（`--ensure`：止まっていれば起動）が crontab に入ります（この環境の行だけ。手で入れた行は置き換え）。確かめるのは `autostart_check`。詳しくは [docs/OPERATIONS.md](OPERATIONS.md) 4 章。

## 5. サーバーに入って行うこと（必要なときだけ）

```bash
ssh -i <配布された .pem> <SSHユーザー>@<サーバーのIP>
source ~/env/bin/activate
cd ~/michinoteowl
nano .env                               # EMAIL_* など、Set env (owl) で変えられない項目
bash deploy/venv-deploy.sh --restart    # .env を変えたあと
tail -f logs/error.log                  # アプリのエラー
```

期限のお知らせのメールを使うときは、`.env` に `EMAIL_HOST` などを入れ、`crontab -e` に毎朝の 1 行を足します（`docs/DEPLOY_AWS_YOURS.md` 4 と同じ。パスは `~/michinoteowl`、`--base-url https://michinote.owl.ai-labo.cloud`）。

## 6. 公式 LINE・音声入力

- 公式 LINE はオウル用のチャネルを用意し、Webhook URL を `https://michinote.owl.ai-labo.cloud/line/webhook/<施設ID>/`（予約 → 公式LINE の一番上に出る URL）にして、チャネルシークレットとトークンを施設設定に入れます。
- 音声の文字起こし（Whisper）はゆあーずの設定（`SPEECH_BACKEND=whisper`・`WHISPER_MODEL`・`WHISPER_THREADS`）を写しているので、そのまま使えます。モデルは `~/.cache/huggingface` にあるものを共用します。ゆあーずとオウルで同時に文字起こしが走ると、それぞれの Gunicorn の中で 1 つずつ処理されます（ロックは clone ごと）。

## つまずきやすい点

| 症状 | 原因と対処 |
|---|---|
| Deploy (owl) の health check が失敗 | nginx の中継先が 8034 か。`Log check (owl)` / `Manage (owl)` の `error_log` でアプリのエラーを見る |
| `password authentication failed` | 初回に `~/michinoteyours/.env` が無く `DB_PASSWORD` が空のまま → サーバーで `.env` に入れて `--restart` |
| CSRF 403 / 400 Bad Request | `.env` の `CSRF_TRUSTED_ORIGINS` / `DJANGO_ALLOWED_HOSTS` が `michinote.owl.ai-labo.cloud` か |
| ゆあーずの画面に出てしまう | URL がゆあーずのもの。オウルは `michinote.owl.ai-labo.cloud` |
| 送迎・配車・5領域アセスメントのメニューが無い | 施設設定 → 使う機能 で「送迎・配車」「5領域アセスメント」を入れる（Manage (owl) の `create_facility` で作った事業所は入っている） |

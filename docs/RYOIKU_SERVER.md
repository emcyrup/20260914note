# 「発達支援ルーム　ゆあーず」（旧名 りょういく）を GCP の別サーバーで動かす

療育の事業所「発達支援ルーム　ゆあーず」（時間枠の予約・月予約利用希望・月間予定表・療育記録）を、開発環境やシンプルのサーバーとは**別のサーバー・別のデータベース**で動かすための手引きです。
配備先は **GCP Compute Engine の VM 1台**（Docker Compose：Caddy → Django/Gunicorn → PostgreSQL）で、コードは同じリポジトリのブランチ `ryoiku` を配備します。

Cloud Console（ブラウザ）と GitHub の画面だけで構築できます。サーバー内の作業は Console の「ブラウザで SSH」を使います。
シンプルのサーバー（`docs/SIMPLE_SERVER.md`）と同じ作りなので、一度やったことがあれば同じ手順です。

---

## 0. シンプルのサーバーとの違い

| 項目 | シンプル | ゆあーず |
|---|---|---|
| ブランチ | `simple` | **`ryoiku`** |
| ワークフロー | Deploy (simple) | **Deploy (ryoiku)** |
| GitHub Secrets | `SIMPLE_*` | **`RYOIKU_*`** |
| サーバーの置き場所 | `/opt/simple` | **`/opt/ryoiku`** |
| 固定 IP の名前（例） | `simple-ip` | `ryoiku-ip` |
| VM の名前（例） | `simple` | `ryoiku` |
| DB 名・ユーザー | `simple` | `ryoiku` |
| イメージのタグ | `:simple` | `:ryoiku` |

サーバーの部品（`deploy/setup-server.sh`・`docker-compose.yml`・`Caddyfile`・`backup.sh`）は共通です。

## 1. いまある状態

- 「ゆあーず」の機能（時間枠の予約・月予約利用希望・月間予定表・療育記録）は `develop`・`simple`・`ryoiku` に入っています（README「療育の事業所向け」）。コードは `reservations/`（`monthly.py` と時間枠の設定）と `therapy/` アプリ、祝日判定は `config/jp_holidays.py`。
- 事業所は `create_facility "発達支援ルーム　ゆあーず" --preset ryoiku` で作ります（予約管理＋療育記録を ON にし、1枠45分・1枠3人・平日10〜18時・土日祝9〜17時・月木休を入れます）。開発環境（AWS）にも作ってあります。2026-09-23 に事業所名を「りょういく」から「発達支援ルーム　ゆあーず」に変えました（名前が「りょういく」の事業所は、配備のときの migration `reservations/0009` で自動で書き換わります）。ファイル名・ブランチ・置き場所・Secrets・VM などの英字の名前は `ryoiku` のままです。
- 配備用のワークフロー **Deploy (ryoiku)**（`.github/workflows/deploy-ryoiku.yml`）：ブランチ `ryoiku` への push で、テスト → イメージを GHCR に push → SSH で `docker compose pull / up / migrate` → ヘルスチェック。**Secrets が未登録のうちはテストだけ通して配備をスキップ**します。

構成と月額の目安：東京（asia-northeast1）の **e2-small で約 21 ドル/月**。シンプルのサーバーは e2-micro（1GB）で始めてページ遷移が数秒かかり、e2-small（2GB）に上げて解消しました。**最初から e2-small を選んでください。**

## 2. GCP の準備（Cloud Console）

### 2-1. プロジェクトと請求先

シンプルと同じプロジェクトに相乗りしても、別プロジェクトにしても構いません（事業所ごとに請求を分けたいなら別プロジェクト）。

1. [console.cloud.google.com](https://console.cloud.google.com/) → プロジェクトを選択、または **新しいプロジェクト** → 名前「yours」など → 作成。**プロジェクト ID** を控える。
2. **お支払い** → 請求先アカウントをこのプロジェクトに**リンク**。**予算とアラート** で月額の上限（例 3,000 円）も決めておく。
3. **Compute Engine → VM インスタンス → 有効にする**（1〜2 分。同じプロジェクトなら済んでいます）。

### 2-2. 固定 IP を予約する

**VPC ネットワーク → IP アドレス → 外部静的 IP アドレスを予約**

| 項目 | 値 |
|---|---|
| 名前 | `ryoiku-ip` |
| タイプ | リージョン |
| リージョン | asia-northeast1（東京） |
| 接続先 | なし |

表示された IP を控える（以後「固定IP」）。

### 2-3. VM を作る

先に GitHub で `deploy/setup-server.sh` を開き **Raw** → 全文をコピーしておく。

**Compute Engine → VM インスタンス → インスタンスを作成**

| 画面 | 項目 | 値 |
|---|---|---|
| マシン構成 | 名前 | `ryoiku` |
| | リージョン / ゾーン | asia-northeast1 / asia-northeast1-a |
| | シリーズ / マシンタイプ | E2 / **e2-small**（1GB の e2-micro は遅い） |
| OS とストレージ → 変更 | OS / バージョン | Ubuntu / **Ubuntu 22.04 LTS（x86/64）** |
| | ブートディスク | 標準永続ディスク / 30 GB |
| ネットワーキング | ファイアウォール | ☑ HTTP トラフィックを許可 ☑ HTTPS トラフィックを許可 |
| | ネットワーク インターフェース → 外部 IPv4 | 2-2 の `ryoiku-ip` |
| セキュリティ | サービス アカウント | Compute Engine default service account |
| | アクセス スコープ | **すべての Cloud API に完全アクセス権を許可**（バックアップをバケットへ送るため） |
| 詳細設定 → 管理 → メタデータ | **項目を追加** | キー `app-dir` / 値 **`/opt/ryoiku`** |
| 詳細設定 → 管理 → 自動化 | 起動スクリプト | `setup-server.sh` の全文を貼り付け |

> ⚠️ **メタデータ `app-dir` を忘れないでください。** 起動スクリプトはこの値を読んで `/opt/ryoiku` を作ります。入れ忘れると `/opt/simple` が作られ、配備のときに「`.env` がありません」で止まります（あとから直すには、下の 2-3 補足のとおり SSH で作り直します）。

**作成** → 3〜5 分待つ → VM 一覧の **SSH** ボタンでブラウザの端末を開いて確認：

```bash
sudo journalctl -u google-startup-scripts --no-pager | tail -n 5   # setup-server: done
id deploy && docker --version && ls -d /opt/ryoiku && echo "準備OK"
```

**2-3 補足**：`no such user` や `docker: command not found` が出たら起動スクリプトが走っていません。リポジトリは Private なので `curl` では取れません。`sudo nano /root/setup-server.sh` に `deploy/setup-server.sh` の全文を貼って保存し、その場で流します（2〜4 分、末尾に `setup-server: done`）：

```bash
sudo APP_DIR=/opt/ryoiku bash /root/setup-server.sh
```

### 2-4. バックアップ用バケット（任意・推奨）

**Cloud Storage → バケット → 作成**

| 項目 | 値 |
|---|---|
| 名前 | `ryoiku-backups-<プロジェクトID>` |
| ロケーション | Region / asia-northeast1 |
| ストレージ クラス | Standard |
| アクセス制御 | 均一 |
| 公開アクセスの防止 | 適用する |

- **ライフサイクル** → ルールを追加：オブジェクトを削除 / 経過日数 30 / 名前の接頭辞 `db/`
- **権限** → アクセス権を付与：プリンシパル `<プロジェクト番号>-compute@developer.gserviceaccount.com`、ロール **Storage オブジェクト管理者**（プロジェクト番号は IAM と管理 → 設定）

## 3. サーバーの設定（ブラウザで SSH）

### 3-1. GitHub Actions がログインするための鍵

`sudo -u deploy -i` は **1 行だけ先に実行**し、プロンプトが `deploy@ryoiku:~$` に変わってから残りを貼ります（まとめて貼ると後続行が実行されないことがあります）。

```bash
sudo -u deploy -i
```

```bash
mkdir -p ~/.ssh && chmod 700 ~/.ssh
ssh-keygen -t ed25519 -N "" -C deploy -f ~/.ssh/github_deploy
cat ~/.ssh/github_deploy.pub >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys
ssh -i ~/.ssh/github_deploy -o StrictHostKeyChecking=no deploy@localhost 'echo 鍵OK'   # 鍵OK が出ること
cat ~/.ssh/github_deploy        # BEGIN〜END の全文を控える（3-4 で GitHub Secrets に登録）
rm ~/.ssh/github_deploy         # 登録したらサーバーから秘密鍵を消す
```

### 3-2. 秘密の値を作る

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(64))'   # SECRET_KEY
openssl rand -base64 24                                          # DB_PASSWORD
```

### 3-3. `.env` を置く

`deploy/.env.ryoiku.example` を元に `/opt/ryoiku/.env` を作ります（`nano /opt/ryoiku/.env` → 貼り付け → 保存 → `chmod 600 /opt/ryoiku/.env`）。まず IP で動かす最小構成：

```
SECRET_KEY=（3-2 で生成）
DEBUG=False
DJANGO_ALLOWED_HOSTS=<固定IP>
CSRF_TRUSTED_ORIGINS=http://<固定IP>
SECURE_SSL_REDIRECT=False
DOMAIN=
GUNICORN_WORKERS=2
GUNICORN_THREADS=2
DB_NAME=ryoiku
DB_USER=ryoiku
DB_PASSWORD=（3-2 で生成）
ALLOW_FACILITY_SIGNUP=False
ANTHROPIC_API_KEY=
BACKUP_GCS_BUCKET=ryoiku-backups-<プロジェクトID>
```

`exit` で deploy ユーザーから抜けます。

### 3-4. GitHub Secrets

リポジトリ → **Settings → Secrets and variables → Actions → New repository secret**

| Secret | 値 |
|---|---|
| `RYOIKU_DEPLOY_HOST` | 固定IP |
| `RYOIKU_DEPLOY_SSH_KEY` | 3-1 の秘密鍵の全文（`-----BEGIN` から `END ...-----` まで） |
| `RYOIKU_HEALTH_URL` | 省略可（既定 `http://<固定IP>/healthz/`。ドメインを付けたら `https://<ドメイン>/healthz/`） |
| `RYOIKU_DEPLOY_USER` / `RYOIKU_APP_DIR` | 既定（`deploy` / `/opt/ryoiku`）と違うときだけ |
| `GHCR_PULL_TOKEN` | **シンプルのときに登録ずみならそのままで使えます**（リポジトリ共通）。まだなら、GitHub の自分のアイコン → Settings → Developer settings → Personal access tokens → **Tokens (classic)** → Generate new token。スコープは `read:packages` だけ |

### 3-5. 初回の配備

Secrets を入れたら **Actions → Deploy (ryoiku) → Run workflow**（ブランチ `ryoiku`）で実行します（初回 5〜8 分。以後は `ryoiku` への push で自動）。完了後、ブラウザで `http://<固定IP>/healthz/` が `ok` になれば成功です。

### 3-6. 事業所と管理者を作る

```bash
sudo -u deploy -i
cd /opt/ryoiku
docker compose exec app python manage.py create_facility "発達支援ルーム　ゆあーず" --admin ryoiku --password '初期パスワード' --preset ryoiku --demo
exit
```

- `--preset ryoiku` で、予約管理と療育記録が ON になり、時間枠の初期値（1枠45分・1枠3人・平日 10:00〜18:00・土日祝 9:00〜17:00・12時は枠なし・月木休）が入ります。
- `--demo` で架空のサンプル（利用者7名・今月と来月の月予約利用希望14枚・今月の月間予定表・療育の留意点と記録）が入ります。**実データを入れる前だけ**にしてください。消すときは `docker compose exec app python manage.py seed_demo --facility <ID> --reset`。
- パスワードを変えるときは `docker compose exec app python manage.py changepassword ryoiku`。

`http://<固定IP>/` に `ryoiku` でログインし、**予約 → 利用希望／月間予定表**と**療育記録**が出ることを確認します。予約の決まりごと（1枠の人数・時間帯・お休み）は、予約カレンダー下の設定で管理者が変えられます。

### 3-7. ドメインと HTTPS（LINE を使うなら必須。ドメインが決まるまでは IP のままでよい）

ドメインが無い間は `http://<固定IP>/` で使えます（LINE の Webhook と音声入力だけ使えません）。急ぐ場合は DuckDNS（無料。`xxx.duckdns.org` を固定 IP に向けるだけ）でも Caddy の自動 HTTPS は動きます。

1. ドメインの DNS で A レコードを固定IPに向ける（Cloudflare はプロキシをオフ）。
2. `/opt/ryoiku/.env` を `DJANGO_ALLOWED_HOSTS=<ドメイン>` `CSRF_TRUSTED_ORIGINS=https://<ドメイン>` `SECURE_SSL_REDIRECT=True` `DOMAIN=<ドメイン>` に直し、`docker compose up -d`（deploy ユーザーで `/opt/ryoiku` にて）。Caddy が証明書を自動取得します。
3. GitHub Secrets の `RYOIKU_HEALTH_URL` を `https://<ドメイン>/healthz/` にする。
4. LINE Developers の Webhook URL に `https://<ドメイン>/line/webhook/<施設ID>/` を登録し、施設設定でチャネルアクセストークン・シークレットを入れる。詳しくは `docs/HTTPS.md`。

**ゆあーずで LINE を使う場面**：保護者の顧客ページ（月予約利用希望の送信・予約の確認）のアドレスを LINE で配るとき、月間予定表を作ったあとの「ご利用日が決まりました」を送るときです。ドメインが無くても、顧客ページのアドレスは `http://<固定IP>/yoyaku/mypage/<アドレス>/` として画面からコピーして渡せます。

### 3-8. 運用

```bash
sudo -u deploy -i && cd /opt/ryoiku
docker compose ps
docker compose logs -f app
./backup.sh                                  # 毎日 3:30 に自動実行（backups/ と GCS）
gunzip -c backups/db-YYYYMMDD-HHMMSS.sql.gz | docker compose exec -T db psql -U ryoiku ryoiku   # 復元
```

- 更新：`ryoiku` ブランチに push（または Actions → Deploy (ryoiku) → Run workflow）。
- 全部消す：VM 削除（ブートディスクも）、固定 IP の解放、バケット削除。固定 IP だけ残すと課金が続きます。
- 秘密情報（`.env` の中身・SSH 鍵・DB パスワード）はサーバーと GitHub Secrets だけに置き、リポジトリや Notion には書きません。`main` への push は毎回依頼者の許可を得てから行います。

### 3-9. つまずいたら

| 症状 | 対処 |
|---|---|
| Compute Engine を有効にできない | 請求先がこのプロジェクトにリンクされていない |
| `setup-server: done` が出ない／`no such user` | 2-3 補足の `sudo APP_DIR=/opt/ryoiku bash /root/setup-server.sh` |
| `/opt/ryoiku` が無く `/opt/simple` ができている | メタデータ `app-dir` の入れ忘れ。2-3 補足のコマンドで作り直す |
| 3-1 の `ssh … deploy@localhost` が `Permission denied` | `exit` → `sudo usermod -p '*' deploy` → 確認行を再実行 |
| deploy ジョブ `.env がありません` | 3-3 が未完了、または `/opt/ryoiku` ではない場所に置いた |
| deploy ジョブ `Permission denied (publickey)` | `RYOIKU_DEPLOY_SSH_KEY` とサーバーの `authorized_keys` が対になっていない。3-1 をやり直す |
| deploy ジョブ `Connection timed out` | VM が停止していないか。VPC ネットワーク → ファイアウォールに `default-allow-ssh` があるか |
| 502 Bad Gateway | `docker compose logs app`（SECRET_KEY 未設定・DB_PASSWORD 不一致が多い） |
| `CSRF verification failed` | `CSRF_TRUSTED_ORIGINS` が実際の URL と違う |
| IP で動かしているのにログインできない | `SECURE_SSL_REDIRECT=False` を確認して `docker compose up -d` |
| バックアップが GCS に届かない | VM のアクセススコープ（2-3）とバケットの権限（2-4） |
| ページ遷移が数秒かかる（`free -m` で Swap の used が大きい） | メモリ不足。VM を e2-small（2GB）以上にする |
| 月間予定表に名前が出ない | 利用希望を保存してから「月間予定表を作る」を押したか。希望回数が 0 だと割り当てません |

## 4. ブランチの運用

| ブランチ | 用途 | 配備先 |
|---|---|---|
| `develop` | 開発環境（AWS：ウィズユー藤森・はぴねす・なゆた・シンプル・ゆあーず） | Deploy (dev) |
| `simple` | シンプル用の GCP サーバー | Deploy (simple) |
| `ryoiku` | **ゆあーず用の GCP サーバー** | Deploy (ryoiku) |
| `main` | 本番（許可制） | — |

`ryoiku` は `develop` から切ってあります。共通の修正は `develop` に入れてから各ブランチに取り込みます（`git merge develop`）。事業所ごとの違いは施設の設定（使う機能・画面の型・予約の決まりごと）で出し分けているので、ゆあーずだけに必要な機能も `develop` に入れて構いません。

事業所をもう1つ別サーバーに出すときは、このファイルと `.github/workflows/deploy-ryoiku.yml`・`deploy/.env.ryoiku.example` を同じ形でコピーし、`RYOIKU_` と `/opt/ryoiku` を新しい名前に置き換えます。

## 5. 参考

- 用紙（月間予定表・療育記録・月予約利用希望）は依頼者提供のものに合わせた。README「療育の事業所向け」に画面と印刷の説明がある。
- シンプルのサーバーの手引きは `docs/SIMPLE_SERVER.md`。作りは同じ。
- Notion のプロジェクトページ「放デイ20260914」に経緯と決めごとがある。

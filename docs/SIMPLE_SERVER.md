# 「シンプル」を GCP の別サーバーで動かす（次のセッションへの引き継ぎ）

計画書中心の画面（シンプル）を、開発環境（はぴねす・ウィズユー藤森・なゆた）とは**別のサーバー・別のデータベース**で動かすための手引きです。
配備先は **GCP Compute Engine の VM 1台**（Docker Compose：Caddy → Django/Gunicorn → PostgreSQL）で、コードは同じリポジトリのブランチ `simple` を配備します。次のセッションでは、ここに **療育日記** と **シフト** を足します。

Cloud Console（ブラウザ）と GitHub の画面だけで構築できます。サーバー内の作業は Console の「ブラウザで SSH」を使います。

---

## 1. いまある状態（2026-09-20 時点）

- 画面の型「計画書中心（シンプル）」は `develop` と `simple` に入っています（README「計画書中心の画面（シンプル）」）。コードは `planbook/` アプリと `templates/planbook/`、ナビは `templates/base.html` の `features.planbook` の分岐、色は `static/css/base.css` の `.layout-planbook`。
- 事業所は `create_facility --layout planbook` で作ります。開発環境（AWS）にも事業所「シンプル」を作ってあります。
- 面談記録・連絡帳は `planbook` のテーブル、それ以外（利用者・保護者・受給者証・計画・目標・モニタリング）は既存のテーブルを使います。
- 配備用のワークフロー **Deploy (simple)**（`.github/workflows/deploy-simple.yml`）：ブランチ `simple` への push で、テスト → イメージを GHCR に push → SSH で `docker compose pull / up / migrate` → ヘルスチェック。**Secrets が未登録のうちはテストだけ通して配備をスキップ**します。
- サーバー側の部品：`deploy/setup-server.sh`（VM の起動スクリプト）、`deploy/docker-compose.yml`、`deploy/Caddyfile`、`deploy/backup.sh`（毎日 3:30 の DB バックアップ）、`deploy/.env.simple.example`（`.env` の雛形）、`Dockerfile`。

構成と月額の目安：東京（asia-northeast1）の **e2-small で約 21 ドル/月**、e2-micro で約 13 ドル/月（PDF 生成のためスワップ 2GB を起動スクリプトで作ります）。

## 2. GCP の準備（Cloud Console）

### 2-1. プロジェクトと請求先

1. [console.cloud.google.com](https://console.cloud.google.com/) → プロジェクトを選択 → **新しいプロジェクト** → 名前「シンプル」など → 作成。自動で付く **プロジェクト ID** を控える。
2. **お支払い** → 請求先アカウントを作り、このプロジェクトに**リンク**。**予算とアラート** で月額の上限（例 3,000 円）も決めておく。
3. **Compute Engine → VM インスタンス → 有効にする**（1〜2 分）。

### 2-2. 固定 IP を予約する

**VPC ネットワーク → IP アドレス → 外部静的 IP アドレスを予約**

| 項目 | 値 |
|---|---|
| 名前 | `simple-ip` |
| タイプ | リージョン |
| リージョン | asia-northeast1（東京） |
| 接続先 | なし |

表示された IP を控える（以後「固定IP」）。

### 2-3. VM を作る

先に GitHub で `deploy/setup-server.sh` を開き **Raw** → 全文をコピーしておく。

**Compute Engine → VM インスタンス → インスタンスを作成**

| 画面 | 項目 | 値 |
|---|---|---|
| マシン構成 | 名前 | `simple` |
| | リージョン / ゾーン | asia-northeast1 / asia-northeast1-a |
| | シリーズ / マシンタイプ | E2 / **e2-small**（安く: e2-micro） |
| OS とストレージ → 変更 | OS / バージョン | Ubuntu / **Ubuntu 22.04 LTS（x86/64）** |
| | ブートディスク | 標準永続ディスク / 30 GB |
| ネットワーキング | ファイアウォール | ☑ HTTP トラフィックを許可 ☑ HTTPS トラフィックを許可 |
| | ネットワーク インターフェース → 外部 IPv4 | 2-2 の `simple-ip` |
| セキュリティ | サービス アカウント | Compute Engine default service account |
| | アクセス スコープ | **すべての Cloud API に完全アクセス権を許可**（バックアップをバケットへ送るため） |
| 詳細設定 → 管理 → 自動化 | 起動スクリプト | `setup-server.sh` の全文を貼り付け |

**作成** → 3〜5 分待つ → VM 一覧の **SSH** ボタンでブラウザの端末を開いて確認：

```bash
sudo journalctl -u google-startup-scripts --no-pager | tail -n 5   # setup-server: done
id deploy && docker --version && echo "準備OK"
```

`no such user` や `docker: command not found` が出たら起動スクリプトが走っていない。リポジトリは Private なので `curl` では取れない。`sudo nano /root/setup-server.sh` に `deploy/setup-server.sh` の全文を貼って保存し、その場で流す（2〜4 分、末尾に `setup-server: done`）：

```bash
sudo bash /root/setup-server.sh
```

### 2-4. バックアップ用バケット（任意・推奨）

**Cloud Storage → バケット → 作成**

| 項目 | 値 |
|---|---|
| 名前 | `simple-backups-<プロジェクトID>` |
| ロケーション | Region / asia-northeast1 |
| ストレージ クラス | Standard |
| アクセス制御 | 均一 |
| 公開アクセスの防止 | 適用する |

- **ライフサイクル** → ルールを追加：オブジェクトを削除 / 経過日数 30 / 名前の接頭辞 `db/`
- **権限** → アクセス権を付与：プリンシパル `<プロジェクト番号>-compute@developer.gserviceaccount.com`、ロール **Storage オブジェクト管理者**（プロジェクト番号は IAM と管理 → 設定）

## 3. サーバーの設定（ブラウザで SSH）

### 3-1. GitHub Actions がログインするための鍵

`sudo -u deploy -i` は **1 行だけ先に実行**し、プロンプトが `deploy@simple:~$` に変わってから残りを貼る（まとめて貼ると後続行が実行されないことがある）。

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

`deploy/.env.simple.example` を元に `/opt/simple/.env` を作る（`nano /opt/simple/.env` → 貼り付け → 保存 → `chmod 600 /opt/simple/.env`）。まず IP で動かす最小構成：

```
SECRET_KEY=（3-2 で生成）
DEBUG=False
DJANGO_ALLOWED_HOSTS=<固定IP>
CSRF_TRUSTED_ORIGINS=http://<固定IP>
SECURE_SSL_REDIRECT=False
DOMAIN=
GUNICORN_WORKERS=1
GUNICORN_THREADS=2
DB_NAME=simple
DB_USER=simple
DB_PASSWORD=（3-2 で生成）
ALLOW_FACILITY_SIGNUP=False
ANTHROPIC_API_KEY=
BACKUP_GCS_BUCKET=simple-backups-<プロジェクトID>
```

`exit` で deploy ユーザーから抜ける。

### 3-4. GitHub Secrets

リポジトリ → **Settings → Secrets and variables → Actions → New repository secret**

| Secret | 値 |
|---|---|
| `SIMPLE_DEPLOY_HOST` | 固定IP |
| `SIMPLE_DEPLOY_SSH_KEY` | 3-1 の秘密鍵の全文（`-----BEGIN` から `END ...-----` まで） |
| `SIMPLE_HEALTH_URL` | 省略可（既定 `http://<固定IP>/healthz/`。ドメインを付けたら `https://<ドメイン>/healthz/`） |
| `SIMPLE_DEPLOY_USER` / `SIMPLE_APP_DIR` | 既定（`deploy` / `/opt/simple`）と違うときだけ |
| `GHCR_PULL_TOKEN` | **必須**（リポジトリが Private のため、VM がイメージを取るのに要る）。GitHub の自分のアイコン → Settings → Developer settings → Personal access tokens → **Tokens (classic)** → Generate new token。スコープは `read:packages` だけ、期限は 1 年など。表示された `ghp_…` を登録する |

### 3-5. 初回の配備

Secrets を入れたら **Actions → Deploy (simple) → Run workflow**（ブランチ `simple`）で実行する（初回 5〜8 分。以後は `simple` への push で自動）。完了後、ブラウザで `http://<固定IP>/healthz/` が `ok` になれば成功。

### 3-6. 事業所と管理者を作る

```bash
sudo -u deploy -i
cd /opt/simple
docker compose exec app python manage.py create_facility "シンプル" --admin simple --password '初期パスワード' --layout planbook
exit
```

`http://<固定IP>/` に `simple` でログインすると、利用者一覧がホームの6メニューの画面になります。`--demo` を付けると架空のサンプルも入ります（実データを入れる前だけ）。

### 3-7. ドメインと HTTPS（LINE を使うなら必須。ドメインが決まるまでは IP のままでよい）

ドメインが無い間は `http://<固定IP>/` で使えます（LINE の Webhook と音声入力だけ使えません）。急ぐ場合は DuckDNS（無料。`xxx.duckdns.org` を固定 IP に向けるだけ）でも Caddy の自動 HTTPS は動きます。ドメインが決まったら次のとおり切り替えます。


1. ドメインの DNS で A レコードを固定IPに向ける（Cloudflare はプロキシをオフ）。
2. `/opt/simple/.env` を `DJANGO_ALLOWED_HOSTS=<ドメイン>` `CSRF_TRUSTED_ORIGINS=https://<ドメイン>` `SECURE_SSL_REDIRECT=True` `DOMAIN=<ドメイン>` に直し、`docker compose up -d`（deploy ユーザーで `/opt/simple` にて）。Caddy が証明書を自動取得します。
3. GitHub Secrets の `SIMPLE_HEALTH_URL` を `https://<ドメイン>/healthz/` にする。
4. LINE Developers の Webhook URL に `https://<ドメイン>/line/webhook/<施設ID>/` を登録し、施設設定でチャネルアクセストークン・シークレットを入れる。詳しくは `docs/HTTPS.md`。

### 3-8. 運用

```bash
sudo -u deploy -i && cd /opt/simple
docker compose ps
docker compose logs -f app
./backup.sh                                  # 毎日 3:30 に自動実行（backups/ と GCS）
gunzip -c backups/db-YYYYMMDD-HHMMSS.sql.gz | docker compose exec -T db psql -U simple simple   # 復元
```

- 更新：`simple` ブランチに push（または Actions → Deploy (simple) → Run workflow）。
- 全部消す：VM 削除（ブートディスクも）、固定 IP の解放、バケット削除。固定 IP だけ残すと課金が続く。
- 秘密情報（`.env` の中身・SSH 鍵・DB パスワード）はサーバーと GitHub Secrets だけに置き、リポジトリや Notion には書かない。`main` への push は毎回依頼者の許可を得てから行う。

### 3-9. つまずいたら

| 症状 | 対処 |
|---|---|
| Compute Engine を有効にできない | 請求先がこのプロジェクトにリンクされていない |
| `setup-server: done` が出ない／`no such user` | 2-3 の `curl … \| sudo bash` でその場で実行 |
| 3-1 の `ssh … deploy@localhost` が `Permission denied` | `exit` → `sudo usermod -p '*' deploy` → 確認行を再実行 |
| deploy ジョブ `.env がありません` | 3-3 が未完了 |
| deploy ジョブ `Permission denied (publickey)` | `SIMPLE_DEPLOY_SSH_KEY` とサーバーの `authorized_keys` が対になっていない。3-1 をやり直す |
| deploy ジョブ `Connection timed out` | VPC ネットワーク → ファイアウォールに `default-allow-ssh` があるか |
| 502 Bad Gateway | `docker compose logs app`（SECRET_KEY 未設定・DB_PASSWORD 不一致が多い） |
| `CSRF verification failed` | `CSRF_TRUSTED_ORIGINS` が実際の URL と違う |
| IP で動かしているのにログインできない | `SECURE_SSL_REDIRECT=False` を確認して `docker compose up -d` |
| バックアップが GCS に届かない | VM のアクセススコープ（2-3）とバケットの権限（2-4） |
| ページ遷移が数秒かかる（`free -m` で Swap の used が大きい） | メモリ不足。VM を e2-small（2GB）以上にするか、`.env` に `GUNICORN_WORKERS=1` を入れて `docker compose up -d app` |

## 3-10. ブランチの運用

| ブランチ | 用途 | 配備先 |
|---|---|---|
| `develop` | 開発環境（AWS：はぴねす・ウィズユー藤森・なゆた・シンプル） | Deploy (dev) |
| `simple` | シンプル用の GCP サーバー | Deploy (simple) |
| `main` | 本番（許可制） | — |

`simple` は `develop` から切ってあります。共通の修正は `develop` に入れてから `simple` に取り込む（`git merge develop`）のが基本です。シンプルだけに必要な機能も、施設の「画面の型」で出し分けているので `develop` に入れて構いません。

## 4. 次のセッションで作るもの

### 4-1. 療育日記

シンプルのメニューに「療育日記」を足し、利用者ごとの日々の記録を残す機能です。標準画面の日誌（`records.DailyRecord`：AI 整形・写真・LINE 配信・加算の入力）をそのまま土台にできます。

提案する形：

- メニュー「療育日記」→ 今日の来所者（または利用者一覧）から1人を選び、Bansou 風の1画面フォームで記入。項目は日付・記録者・活動・様子（本人の様子／支援内容／保護者へのひとこと）・体調・写真。既存の `DailyRecord` の項目に対応させ、新しいテーブルは作らない。
- 一覧は利用者ごとの月別（`records:list`）と、日別の全員分（業務日誌）の2つ。
- 保護者へのひとことは、連絡帳（`planbook.ContactNote`）に自動で転記し、LINE 連携があれば送る（日誌の「保護者向けメッセージ」と同じ送り方）。
- 施設設定の「日誌の項目と順番」をそのまま使えば、事業所ごとの項目の増減に対応できる。

確認したいこと：紙の療育日記の様式（項目名・並び）があるか。写真を保護者に送るか。加算の入力欄を出すか（請求機能を使わない事業所なら不要）。

### 4-2. シフト

職員の勤務シフトです。既存のアプリにはないので、新しいアプリ `shifts` を作ります。

提案する形：

- モデル：`ShiftPattern`（事業所ごとの勤務パターン：早番 9:00〜18:00 など、名前・開始・終了・休憩分・色）、`Shift`（職員 × 日付 × パターン or 時刻・メモ・休み区分）、`ShiftRequest`（職員からの希望休・希望シフト。任意）。
- 画面：月間の職員×日付の表（セルをタップしてパターンを選ぶ。スマホは日別の縦並び）、日別の出勤者一覧、職員本人の「自分のシフト」、パターンの設定（施設設定）。CSV 出力。
- 権限：入力・確定は管理者と児発管、閲覧は全員。
- 利用者の来所予定（`schedules.ScheduledVisit`）と並べて、その日の人員配置の目安（配置基準の人数）を出す。
- LINE 連携があれば、確定したシフトを職員のグループ LINE に流す（予約管理のグループ通知の仕組みが使える）。

確認したいこと：シフトの単位（パターン制か時刻入力か）、希望休の受付を使うか、月の締め・確定の運用、印刷様式の有無。

### 4-3. 進め方

1. このファイルと README を読み、`python manage.py test` が通ることを確認（2026-09-19 時点で 337 件）。
2. 2〜3 章の GCP 設定と Secrets を入れて Deploy (simple) を1回通す。
3. 療育日記 → シフトの順に、施設の型が planbook のときだけメニューに出す形で作る。
4. 動作確認は開発環境の事業所「シンプル」（`develop` に push）でも、GCP のサーバー（`simple` に push）でもできる。

## 5. 参考

- 画面の見本（Bansou）は依頼者のスクリーンショットをもとにした。ロゴ・配色・文言はこのアプリのテーマ設定で作っており、Bansou のものは使っていない。
- Notion のプロジェクトページ「放デイ20260914」に経緯と決めごとがある。

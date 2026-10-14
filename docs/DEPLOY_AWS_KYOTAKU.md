# 居宅介護の事業所「居宅介護支援事業所コト」を AWS 共用サーバーで動かす手順（ゆあーず・オウルと同じサーバー・別ポート・別 DB）

対象: `https://<アドレス>/`（仮：`michinote.kyotaku.ai-labo.cloud`）→ プロバイダ管理の nginx（HTTPS 終端）→ Gunicorn `0.0.0.0:<ポート>`（仮：8036）→ プロバイダ管理の PostgreSQL（DB `michinotekyotaku`、ユーザー `ai_labo_dbuser`）。

**居宅介護の機能（画面の型「居宅介護」・`homecare` アプリ）は `kyotaku` ブランチだけにあり、ゆあーず・オウル（`ryoiku`）には入れません**（2026-10-14 の依頼）。ゆあーず・オウルと共通の部分を直したときは、`ryoiku` の変更を `kyotaku` に取り込んで配備します。

ゆあーず（`~/michinoteyours`・8030）・オウル（`~/michinoteowl`・8034）と**同じサーバー・同じ仕組み**で、置き場所・ポート・DB・`.env` が違うだけです。venv（`~/env`）は共用します。DB が別なので、居宅介護の利用者・記録はゆあーず・オウルからは見えません。

| | オウル | 居宅介護 |
|---|---|---|
| 置き場所 | `~/michinoteowl` | `~/michinotekyotaku` |
| Gunicorn | `0.0.0.0:8034` | `0.0.0.0:<ポート>`（仮 8036） |
| DB | `michinoteowl` | `michinotekyotaku`（同じユーザー・同じパスワード） |
| 配備 | Deploy (owl)（`ryoiku` への push） | Deploy (kyotaku)（**`kyotaku` ブランチ**への push） |
| 管理 | Manage (owl) | Manage (kyotaku) |

秘密情報（DB パスワード、`SECRET_KEY`、Anthropic キー、SSH 秘密鍵、管理者のパスワード）はサーバーの `.env`・GitHub Secrets だけに置きます（リポジトリ・Notion・チャット・Actions のログには書かない）。

## 画面と機能（画面の型「居宅介護」＝ `Facility.LAYOUT_KYOTAKU`、`create_facility --preset kyotaku`）

- メニュー：きょうの訪問（ホーム）・訪問予定・サービス提供記録・利用者情報・居宅介護計画・実績記録票・議事録・施設設定
- 職員は 1 人の想定：職員・運用管理の画面は出さない（担当はログインしている人）。請求・来所予定・予約・LINE は使わない
- 呼び方：職員＝ヘルパー、利用者＝利用者。画面のタイトルは「居宅介護業務支援」
- 書類の AI 読み取り（利用者情報）・音声入力と「書き言葉に整える」（サービス提供記録の様子）・議事録はそのまま使える
- 計画は AI で作文しない（`plan_no_ai`）

## 1. 準備（プロバイダ側。まだ）

- アドレスを決める（例 `michinote.kyotaku.ai-labo.cloud`）
- nginx：そのアドレス → `127.0.0.1:<ポート>`（HTTPS。`X-Forwarded-Proto`・`client_max_body_size 100m`・`proxy_read_timeout 300s` はゆあーずと同じ）
- PostgreSQL：DB `michinotekyotaku`、ユーザー `ai_labo_dbuser`（ゆあーずと同じユーザー）
- ポート番号（ほかと重ならない番号。8036 が空いていればそれ）

## 2. GitHub Secrets（Settings → Secrets and variables → Actions）

| 名前 | 入れるもの |
|---|---|
| `KYOTAKU_DOMAIN` | 決まったアドレス（`michinote.kyotaku.ai-labo.cloud` のままなら不要） |
| `KYOTAKU_PORT` | ポート番号（8036 なら不要） |
| `KYOTAKU_ADMIN_PASSWORD` | 管理者の最初のパスワード（create_facility・set_password で使う。済んだら消してよい） |
| `KYOTAKU_READY` | `true`（**1 の準備が済んでから**登録する。無いあいだは Deploy (kyotaku) は配備をスキップする） |

SSH は同じサーバーなので `YOURS_DEPLOY_*` をそのまま使います（別にしたいときだけ `KYOTAKU_DEPLOY_*`）。

## 3. 初回の配備（Actions から。サーバーに入らなくてよい）

Actions → **Deploy (kyotaku)** → Run workflow（ブランチ **`kyotaku`**）。初回は `~/michinotekyotaku` に clone し、`.env` を `deploy/.env.kyotaku-aws.example` から作ります。

- `SECRET_KEY` はサーバー上で生成、アドレスとポートは Secrets の値に置き換え
- `DB_PASSWORD`・`ANTHROPIC_API_KEY`・`AI_TEXT_MODEL`・`AI_PLAN_MODEL`・`GOOGLE_SPEECH_API_KEY`・`SPEECH_BACKEND`・`WHISPER_*` は `~/michinoteyours/.env` から写す（ログには項目名だけ出る）

そのあと `deploy/venv-deploy.sh` が `pip install` → `migrate` → `collectstatic` → Gunicorn 起動 → `https://<アドレス>/healthz/` の確認まで行います。2 回目以降は `.env` を上書きしません。以後は `kyotaku` ブランチへの push で配備されます。

## 4. 事業所と管理者を作る

Actions → **Manage (kyotaku)** → Run workflow：`task=create_facility`、事業所名「居宅介護支援事業所コト」（既定）、管理者 `kyotaku`（既定）。パスワードは Secrets の `KYOTAKU_ADMIN_PASSWORD`。「サンプルデータも入れる」に印を付けると、架空の利用者 3 人・今月の訪問・計画が入ります（実データを入れる前に利用者情報から消す）。

そのあと `https://<アドレス>/` に `kyotaku` でログインし、施設設定で事業所名（正式な名前）・事業所番号・住所・電話を入れます。

## 5. サーバー再起動のとき

Manage (kyotaku) を task `autostart_install` で実行すると、`@reboot` と 5 分ごとの見回りが crontab に入ります（この環境の行だけ）。確かめるのは `autostart_check`。

## 使い方（ヘルパー 1 人）

1. 利用者情報で利用者を登録 →「居宅介護を開く」で障害支援区分・支給量（時間／月）・援助の留意点を入れる
2. 居宅介護計画を作る（前の計画があれば写して始まる）→ 印刷して説明・同意 → 同意日を入れる
3. 訪問予定で訪問を入れる（「この月の同じ曜日にも入れる」で毎週ぶんをまとめて）。時間が重なると知らせる
4. 訪問のあと「きょうの訪問」から「記録する」：実際の時刻（「今」ボタン）・したこと・体温／血圧・様子（音声入力 →「書き言葉に整える」）
5. 月末に実績記録票を印刷（算定時間は 30 分単位の目安。請求の前に計画・受給者証と合わせて確かめる）。サービス提供記録も利用者ごとに印刷できる

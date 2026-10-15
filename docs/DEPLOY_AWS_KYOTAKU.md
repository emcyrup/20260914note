# 居宅介護の事業所「居宅介護支援事業所コト」を AWS 開発環境のサーバーで動かす手順（開発環境と同じサーバー・別ポート・別 DB）

対象: `https://st-kaigonote.kyotaku.ai-labo.cloud/` → プロバイダ管理の nginx（HTTPS 終端）→ Gunicorn `0.0.0.0:8039` → プロバイダ管理の PostgreSQL（DB `kaigonotekyotaku`、ユーザー `st_ai_labo_dbuser`）。2026-10-14 にプロバイダ側で設定ずみ（nginx・HTTPS・ポート 8039・DB）。

**居宅介護の機能（画面の型「居宅介護」・`homecare` アプリ）は `kyotaku` ブランチだけにあり、ゆあーず・オウル（`ryoiku`）には入れません**（2026-10-14 の依頼）。ゆあーず・オウルと共通の部分を直したときは、`ryoiku` の変更を `kyotaku` に取り込んで配備します。

開発環境（`docs/DEPLOY_AWS_DEV.md`。`~/michinotedemo`・8029・DB `michinotedemo`）と**同じサーバー・同じ仕組み**で、置き場所・ポート・DB・`.env` が違うだけです。venv（`~/env`）は共用します。DB が別なので、コトの利用者・記録は開発環境・ゆあーず・オウルからは見えません。

| | 開発環境 | コト（居宅介護） |
|---|---|---|
| アドレス | `st-michinotedemo.ai-labo.cloud` | `st-kaigonote.kyotaku.ai-labo.cloud` |
| 置き場所 | `~/michinotedemo` | `~/kaigonotekyotaku` |
| Gunicorn | `0.0.0.0:8029` | `0.0.0.0:8039` |
| DB | `michinotedemo` | `kaigonotekyotaku`（同じユーザー `st_ai_labo_dbuser`・同じパスワード） |
| 配備 | Deploy (dev)（`develop` への push） | Deploy (kyotaku)（**`kyotaku` ブランチ**への push） |
| 管理 | Manage (dev) | Manage (kyotaku) |

秘密情報（DB パスワード、`SECRET_KEY`、Anthropic キー、SSH 秘密鍵、管理者のパスワード）はサーバーの `.env`・GitHub Secrets だけに置きます（リポジトリ・Notion・Actions のログには書かない）。DB パスワードは開発環境と同じユーザーなので、初回の配備でサーバーの中で `~/michinotedemo/.env` から写します。

## 画面と機能（画面の型「居宅介護」＝ `Facility.LAYOUT_KYOTAKU`、`create_facility --preset kyotaku`）

- メニュー：きょうの訪問（ホーム）・訪問予定・サービス提供記録・利用者情報・居宅介護計画・実績記録票・議事録・施設設定
- 職員は 1 人の想定：職員・運用管理の画面は出さない（担当はログインしている人）。請求・来所予定・予約・LINE は使わない
- 呼び方：職員＝ヘルパー、利用者＝利用者。画面のタイトルは「居宅介護業務支援」
- 書類の AI 読み取り（利用者情報）・音声入力と「書き言葉に整える」（サービス提供記録の様子）・議事録はそのまま使える
- 計画は AI で作文しない（`plan_no_ai`）

## 1. 準備（プロバイダ側。2026-10-14 に設定ずみ）

- nginx：`st-kaigonote.kyotaku.ai-labo.cloud` → `127.0.0.1:8039`（HTTPS）
- PostgreSQL：DB `kaigonotekyotaku`、ユーザー `st_ai_labo_dbuser`（開発環境と同じユーザー）

## 2. GitHub Secrets（Settings → Secrets and variables → Actions）

SSH は同じサーバーなので開発環境の `DEV_DEPLOY_*` をそのまま使います（別にしたいときだけ `KYOTAKU_DEPLOY_*`）。追加で要るのは管理者のパスワードだけです。

| 名前 | 入れるもの |
|---|---|
| `KYOTAKU_ADMIN_PASSWORD` | 管理者 `kyotaku` のパスワード（set_password で使う。済んだら消してよい） |
| `KYOTAKU_DOMAIN`・`KYOTAKU_PORT` | アドレス・ポートを変えるときだけ（既定は上の表） |

## 3. 初回の配備（Actions から。サーバーに入らなくてよい）

Actions → **Deploy (kyotaku)** → Run workflow（ブランチ **`kyotaku`**）。初回は `~/kaigonotekyotaku` に clone し、`.env` を `deploy/.env.kyotaku-aws.example` から作ります。

- `SECRET_KEY` はサーバー上で生成
- `DB_PASSWORD`・`ANTHROPIC_API_KEY`・`AI_TEXT_MODEL`・`AI_PLAN_MODEL`・`GOOGLE_SPEECH_API_KEY`・`SPEECH_BACKEND`・`WHISPER_*` は `~/michinotedemo/.env` から写す（ログには項目名だけ出る）

そのあと `deploy/venv-deploy.sh` が `pip install` → `migrate` → `collectstatic` → Gunicorn 起動 → `https://st-kaigonote.kyotaku.ai-labo.cloud/healthz/` の確認まで行います。2 回目以降は `.env` を上書きしません。以後は `kyotaku` ブランチへの push で配備されます。

## 4. 事業所と管理者を作る

Actions → **Manage (kyotaku)** → Run workflow：`task=create_facility`、事業所名「居宅介護支援事業所コト」（既定）、管理者 `kyotaku`（既定）。パスワードは Secrets の `KYOTAKU_ADMIN_PASSWORD`（未登録ならランダムな仮の値になるので、登録してから `task=set_password` を実行する）。「サンプルデータも入れる」に印を付けると、架空の利用者 3 人・今月の訪問・計画が入ります（実データを入れる前に利用者情報から消す）。

そのあと `https://st-kaigonote.kyotaku.ai-labo.cloud/` に `kyotaku` でログインし、施設設定で事業所番号・住所・電話を入れます。

## 5. サーバー再起動のとき

Manage (kyotaku) を task `autostart_install` で実行すると、`@reboot` と 5 分ごとの見回りが crontab に入ります（この環境の行だけ）。確かめるのは `autostart_check`。

## 使い方（ヘルパー 1 人）

1. 利用者情報で利用者を登録 →「居宅介護を開く」で障害支援区分・支給量（時間／月）・援助の留意点を入れる
2. 居宅介護計画を作る（前の計画があれば写して始まる）→ 印刷して説明・同意 → 同意日を入れる
3. 訪問予定で訪問を入れる（「この月の同じ曜日にも入れる」で毎週ぶんをまとめて）。時間が重なると知らせる
4. 訪問のあと「きょうの訪問」から「記録する」：実際の時刻（「今」ボタン）・したこと・体温／血圧・様子（音声入力 →「書き言葉に整える」）
5. 月末に実績記録票を印刷（算定時間は 30 分単位の目安。請求の前に計画・受給者証と合わせて確かめる）。サービス提供記録も利用者ごとに印刷できる

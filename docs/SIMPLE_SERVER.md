# 「シンプル」を別サーバーで動かす（次のセッションへの引き継ぎ）

計画書中心の画面（シンプル）を、開発環境（はぴねす・ウィズユー藤森・なゆた）とは**別のサーバー・別のデータベース**で動かすための手引きです。
コードは同じリポジトリのまま、ブランチ `simple` を配備します。次のセッションでは、ここに **療育日記** と **シフト** を足します。

---

## 1. いまある状態（2026-09-19 時点）

- 画面の型「計画書中心（シンプル）」は `develop` に入っています（README「計画書中心の画面（シンプル）」）。コードは `planbook/` アプリと `templates/planbook/`、ナビは `templates/base.html` の `features.planbook` の分岐、色は `static/css/base.css` の `.layout-planbook`。
- 事業所は `create_facility --layout planbook` で作ります。開発環境には事業所「シンプル」（管理者 `simple`）を作ってあります。
- 面談記録・連絡帳は `planbook` のテーブル、それ以外（利用者・保護者・受給者証・計画・目標・モニタリング）は既存のテーブルを使います。
- 配備用のワークフロー **Deploy (simple)**（`.github/workflows/deploy-simple.yml`）を用意しました。ブランチ `simple` への push で動きます。Secrets が未登録のうちはテストだけ通して配備をスキップします。

## 2. サーバーの準備（1回だけ）

前提は開発環境と同じです（sudo なし・Docker なし・`~/env` の venv・プロバイダの nginx と PostgreSQL）。違う構成なら `docs/DEPLOY_AWS_DEV.md` と `deploy/docker-compose.external.yml` を参考にしてください。

1. プロバイダから受け取るもの：SSH の接続先とユーザー・鍵、割り当てポート、公開 URL、PostgreSQL の DB 名・ユーザー・パスワード。
2. サーバーで確認：`source ~/env/bin/activate && python --version`（3.12 以上）。PDF を使うなら `libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-noto-cjk` をプロバイダに依頼。
3. clone と `.env`：

```bash
cd ~ && git clone https://github.com/emcyrup/20260914note.git simple
cd simple && git checkout simple
cp deploy/.env.simple.example .env && nano .env && chmod 600 .env
```

4. 初回起動と事業所の作成：

```bash
source ~/env/bin/activate
cd ~/simple
bash deploy/venv-deploy.sh
python manage.py create_facility "シンプル" --admin simple --password '初期パスワード' --layout planbook
```

5. GitHub の Secrets（Settings → Secrets and variables → Actions）：`SIMPLE_DEPLOY_HOST`、`SIMPLE_DEPLOY_USER`、`SIMPLE_DEPLOY_SSH_KEY`、`SIMPLE_HEALTH_URL`（`https://…/healthz/`）。ポート・clone 先・venv が既定と違うときだけ `SIMPLE_DEPLOY_SSH_PORT`、`SIMPLE_APP_DIR`（既定 `~/simple`）、`SIMPLE_VENV`（既定 `~/env`）。
6. 以後は `simple` ブランチに push すると自動で配備されます（テスト → SSH で `deploy/venv-deploy.sh <コミット>` → ヘルスチェック）。Actions → Deploy (simple) → Run workflow で任意のブランチを手動配備もできます。
7. LINE を使うなら、施設設定でチャネルアクセストークンとシークレットを入れ、LINE Developers の Webhook URL に `https://<公開URL>/line/webhook/<施設ID>/` を登録します。

秘密情報（`.env` の中身・SSH 鍵・DB パスワード）はサーバーと GitHub Secrets だけに置き、リポジトリや Notion には書きません。`main` への push は毎回依頼者の許可を得てから行います。

## 3. ブランチの運用

| ブランチ | 用途 | 配備先 |
|---|---|---|
| `develop` | 開発環境（はぴねす・ウィズユー藤森・なゆた・シンプル） | Deploy (dev) |
| `simple` | シンプル用の別サーバー | Deploy (simple) |
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
2. サーバー設定と Secrets を入れて Deploy (simple) を1回通す。
3. 療育日記 → シフトの順に、施設の型が planbook のときだけメニューに出す形で作る。
4. 動作確認は開発環境の事業所「シンプル」（`develop` に push）でも、別サーバー（`simple` に push）でもできる。

## 5. 参考

- 画面の見本（Bansou）は依頼者のスクリーンショットをもとにした。ロゴ・配色・文言はこのアプリのテーマ設定で作っており、Bansou のものは使っていない。
- Notion のプロジェクトページ「放デイ20260914」に経緯と決めごとがある。

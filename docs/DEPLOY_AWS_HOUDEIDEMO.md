# 放課後等デイサービスのデモ事業所「放デイ　デモ」の環境（開発環境の AWS サーバー・別ポート・別の置き場所）

2026-11-06 の依頼：はびりすをベースに、見込み客に試用してもらう放課後等デイサービスのデモ事業所を作る。環境（アドレス・ポート・DB）はプロバイダが用意する。

はびりす（検証環境 `st-michinotedemo.ai-labo.cloud` の事業所 ID 2。ブランチ `develop`）と**同じコード・同じサーバー・同じ仕組み**で、置き場所・ポート・DB・`.env` が違うだけです。事業所の型は `create_facility --preset houdei`（標準の型で、はびりす様式・請求・来所予定・LINE を使う。定員区分 11〜20 人、はびりすが算定している標準の加算を「算定する」に）。

| | 検証環境（はびりす ほか） | 放デイ デモ |
|---|---|---|
| アドレス | `st-michinotedemo.ai-labo.cloud` | **プロバイダに用意してもらう**（Secrets `HOUDEIDEMO_DOMAIN`） |
| 置き場所 | `~/michinotedemo` | `~/michinotehoudei` |
| Gunicorn | `0.0.0.0:8029` | `0.0.0.0:<HOUDEIDEMO_PORT>` |
| DB | `michinotedemo` | `<HOUDEIDEMO_DB_NAME>`（ユーザー `st_ai_labo_dbuser`） |
| ブランチ | `develop` | `develop` |
| 配備 | Deploy (dev)（push で動く） | **Deploy (houdeidemo)**（Actions から手で。ブランチ `develop` を選ぶ） |
| 管理 | Manage (dev) | **Manage (houdeidemo)** |

別の環境にする理由：検証環境にはゆあーず・オウル・はびりすの事業所が同居している。見込み客に見せる環境は、ほかの事業所のデータと同じ DB に入れず、アドレスも分ける。

秘密情報（DB パスワード、`SECRET_KEY`、Anthropic キー、SSH 秘密鍵、管理者のパスワード）はサーバーの `.env`・GitHub Secrets だけに置きます。

## 1. 準備（プロバイダに頼む）

- nginx：`<HOUDEIDEMO_DOMAIN>` → `127.0.0.1:<HOUDEIDEMO_PORT>`（HTTPS。検証環境と同じ設定：`X-Forwarded-Proto`・`client_max_body_size 100m`・`proxy_read_timeout 300s`）
- PostgreSQL：DB `<HOUDEIDEMO_DB_NAME>`、ユーザー `st_ai_labo_dbuser`（開発環境と同じユーザー）

## 2. GitHub Secrets

| 名前 | 入れるもの |
|---|---|
| `HOUDEIDEMO_DOMAIN` | プロバイダが用意したアドレス（必須。無ければ配備しない） |
| `HOUDEIDEMO_PORT` | Gunicorn のポート（必須） |
| `HOUDEIDEMO_DB_NAME` | DB 名（必須。`michinotedemo`・`kaigonotekyotaku`・`kaigonotemidorikai`・`kaigonotedemo` などほかの環境の DB 名は拒む） |
| `HOUDEIDEMO_ADMIN_PASSWORD` | 管理者 `houdeidemo` のパスワード（Manage (houdeidemo) の `create_facility`・`set_password` で使う。デモの相手に伝える値） |

SSH は同じサーバーなので開発環境の `DEV_DEPLOY_*` をそのまま使います。

## 3. 配備（Actions から）

1. Actions → **Deploy (houdeidemo)** → Run workflow → ブランチ **`develop`**。テスト → SSH → 初回は `.env` を雛形（`deploy/.env.houdeidemo-aws.example`）から作り（`SECRET_KEY` は生成、DB パスワード・Anthropic キー・音声の設定は `~/michinotedemo/.env` から写す）→ `migrate` → `collectstatic` → Gunicorn 起動 → `https://<HOUDEIDEMO_DOMAIN>/healthz/` で確認。介護のブランチ（`kyotaku`・`yasuragi`）を選ぶと止まる
2. Manage (houdeidemo) → `task=autostart_install`（サーバーの再起動のあとに自動で上がる・5 分ごとの見回り・毎日 3:15 のバックアップ）

## 4. 事業所と管理者を作る

Manage (houdeidemo) → `task=create_facility`。事業所名「放デイ　デモ」（既定）、管理者 `houdeidemo`（既定）、`with_demo`（既定で印）でサンプルも入る。パスワードは Secrets の `HOUDEIDEMO_ADMIN_PASSWORD`。

デモの相手には、アドレスとユーザー名 `houdeidemo`・パスワードをメールで送る（このリポジトリ・Notion には書かない）。

## 5. 架空のサンプルデータ

`with_demo` で次の 2 つが入ります（すべて架空）。

- `seed_demo`：利用者 6 人（保護者・受給者証つき）、来所予定（前月〜来月）、直近 3 週間の日誌、請求マトリックスの実績、スタッフメモ、はびりす様式の報告書 2 件・専門的支援実施計画書 2 件、計画書の様式追加項目
- `billing_demo`：請求の動作確認用の利用者 40 人と 2 か月ぶんの出欠・加算・上限額管理（上限月額 0／4,600／37,200 円、月の途中からの受給者証、支給量超過、ほかの事業所との上限管理、減算）。「月次の見込み」「請求の準備チェック」「上限額管理の結果票」がそのまま見られる

消すときは `seed_demo_reset`（日誌などのサンプル）と `billing_demo_remove`（請求デモの 40 人）。

## 6. 見せどころ（はびりすで作ったもの）

- 日誌（AI の下書き・重身用・確定の前のチェック・保護者向けメッセージの LINE）、個別支援計画（5 ステップ・様式のまま編集・はびりす様式 別紙 1／詳細版）
- 来所予定と出欠 → 請求マトリックス → 上限額管理（結果票・負担額一覧表）→ 請求書・領収書、月次の見込み（延べ人数・利用者負担・売上概算・処遇改善額）
- 事業所様式（関係機関連携報告書・専門的支援実施計画書）、書類の AI 読み取り、加算の提案（RAG）
- まだ無いもの：国保連の伝送 CSV、多子軽減・無償化、人員配置からの加算判定（はびりすのページの残タスク）

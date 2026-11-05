# 介護施設のデモ事業所（老健・通所リハの法人の試用）の環境（開発環境の AWS サーバー・別ポート・別の置き場所）

2026-11-05 の相談（病院併設の介護老人保健施設・通所リハビリ・居宅介護支援事業所・デイサービスの 4 事業を運営する法人。関係者向けのオンライン説明会と運営推進会議での検討に向けて、デモ環境のリンクとログイン ID を送る）のための環境です。

やすらぎの森（`docs/DEPLOY_AWS_YASURAGI.md`。画面の型「介護施設」・`nursing` アプリ・ブランチ **`yasuragi`**）と**同じコード・同じサーバー・同じ仕組み**で、置き場所・ポート・DB・`.env` が違うだけです。事業所の型は `--preset roken`（介護施設の型で、家族への LINE は使わない。送迎表で車いす・運転できる車両・同乗NG・定員を見る）。

| | やすらぎの森（みどり会） | デモ事業所（老健・通所リハ） |
|---|---|---|
| アドレス | `st-kaigonote.midorikai.ai-labo.cloud` | **プロバイダに用意してもらう**（Secrets `KAIGODEMO_DOMAIN`。例 `st-kaigonote-demo.ai-labo.cloud`） |
| 置き場所 | `~/kaigonotemidorikai` | `~/kaigonotedemo` |
| Gunicorn | `0.0.0.0:8045` | `0.0.0.0:<KAIGODEMO_PORT>`（例 8046） |
| DB | `kaigonotemidorikai` | `<KAIGODEMO_DB_NAME>`（例 `kaigonotedemo`。ユーザー `st_ai_labo_dbuser`） |
| ブランチ | `yasuragi` | `yasuragi` |
| 配備 | Deploy (yasuragi) | **Deploy (kaigodemo)**（Actions から手で。ブランチ `yasuragi` を選ぶ） |
| 管理 | Manage (yasuragi) | **Manage (kaigodemo)** |

別の環境にする理由：デモを見せる相手は別の法人なので、みどり会のアドレス・DB には入れない（事業所ごとにデータは分かれるが、アドレスに法人名が出る・同じ DB に別の顧客のデータが入る）。

秘密情報（DB パスワード、`SECRET_KEY`、Anthropic キー、SSH 秘密鍵、管理者のパスワード）はサーバーの `.env`・GitHub Secrets だけに置きます。

## 1. 準備（プロバイダに頼む）

- nginx：`<KAIGODEMO_DOMAIN>` → `127.0.0.1:<KAIGODEMO_PORT>`（HTTPS。やすらぎの森と同じ設定：`X-Forwarded-Proto`・`client_max_body_size 100m`・`proxy_read_timeout 300s`）
- PostgreSQL：DB `<KAIGODEMO_DB_NAME>`、ユーザー `st_ai_labo_dbuser`（開発環境と同じユーザー）

## 2. GitHub Secrets

| 名前 | 入れるもの |
|---|---|
| `KAIGODEMO_DOMAIN` | プロバイダが用意したアドレス（必須。無ければ配備しない） |
| `KAIGODEMO_PORT` | Gunicorn のポート（必須） |
| `KAIGODEMO_DB_NAME` | DB 名（必須。`kaigonotekyotaku`・`kaigonotemidorikai`・`michinotedemo` などほかの環境の DB 名は拒む） |
| `KAIGODEMO_ADMIN_PASSWORD` | 管理者 `kaigodemo` のパスワード（Manage (kaigodemo) の `create_facility`・`set_password` で使う。デモの相手に伝える値） |

SSH は同じサーバーなので開発環境の `DEV_DEPLOY_*` をそのまま使います。

## 3. 配備（Actions から）

1. Actions → **Deploy (kaigodemo)** → Run workflow → ブランチ **`yasuragi`**。テスト → SSH → 初回は `.env` を雛形（`deploy/.env.kaigodemo-aws.example`）から作り（`SECRET_KEY` は生成、DB パスワード・Anthropic キー・音声の設定は `~/michinotedemo/.env` から写す）→ `migrate` → `collectstatic` → Gunicorn 起動 → `https://<KAIGODEMO_DOMAIN>/healthz/` で確認
2. Manage (kaigodemo) → `task=autostart_install`（サーバーの再起動のあとに自動で上がる・5 分ごとの見回り・毎日 3:15 のバックアップ）

## 4. 事業所と管理者を作る

Manage (kaigodemo) → `task=create_facility`。事業所名「介護老人保健施設　デモ」（既定）、法人名「医療法人（デモ）」（既定）、管理者 `kaigodemo`（既定）、`preset=roken`、`with_demo` に印を付けると老健・通所リハのサンプル（下）も入る。パスワードは Secrets の `KAIGODEMO_ADMIN_PASSWORD`。

デモの相手には、アドレスとユーザー名 `kaigodemo`・パスワードをメールで送る（このリポジトリ・Notion には書かない）。

## 5. 架空のサンプルデータ（`nursing_demo --scenario roken`）

- **通所リハ 36 人**（要支援 1〜要介護 5。週 2〜3 回の曜日。1 日 15〜20 人が送迎）と**入所 8 人**（曜日なし。朝夕の検温と毎日の介護記録）
- **送迎**：車両 4 台（1 号車 ハイエース 定員 10、2 号車 リフト車 定員 6・車いす 2 台、3 号車 セレナ 定員 7・車いす 1 台、4 号車 軽ワゴン 定員 3）、運転手 4 人（A は 1・3 号車、B はリフト車だけ、C はどれでも、D は 4 号車だけ）、車いすのまま乗る人 4 人、同乗NG の組 2 つ。**わざと数か所引っかかる**ようにしてある（車いすの人が車いす枠の無い 4 号車に入っている・4 号車の定員超・3 号車の運転手が運転できない車・同乗NG の 2 人が同じ車）ので、送迎表の赤い表示がそのまま説明になる
- 薬（期限切れ・14 日以内）、計画書、ヒヤリハット 3 件と事故報告 1 件（送迎の固定ベルトのかけ忘れを含む）、人員配置 7 日分
- 消すときは `task=demo_remove`（備考が【サンプル】の利用者とその記録・報告、名前に「サンプル」の付いた車両・運転手、サンプルの人員配置だけ）

## 6. 相談で出た要望と、この環境で見せられること

| 要望 | いま見せられるもの | まだ無いもの |
|---|---|---|
| 検温表・体温推移・ホーム | 検温板（1 人 1 週間 1 枚・折れ線グラフ）・ホーム | — |
| 記録の作成・転記の手間（ノーマンの文を既存ソフトへ貼る） | 介護記録の AI 下書き（はい／いいえ・キーワード・音声）・承認・印刷。記録の種類（介護記録・ヒヤリ／事故・連絡帳）ごとの入力 | ノーマンの API 連携（公開状況の確認待ち）・ほのぼのへの取り込み |
| 送迎表（1 日 60 人・車両定員・車いす・運転できる車種・相性） | 送迎表の下書きと条件の確認（定員・車いす枠・運転できる車両・同乗NG） | 住所・ルートを見た自動の割り振り（条件がそろってから） |
| 権限・操作履歴 | 権限区分（管理者・リーダー・職員）、記録ごとの記録者・承認者、ログインの記録 | 画面ごとの閲覧範囲の細かい設定・操作履歴の一覧 |
| シフト・人員配置（4 事業で違う） | 人員配置（日ごとの利用者数と職種ごとの人数の判定） | 事業ごとのシフト表 |
| 請求（報酬計算・CSV・返戻の確認） | — | 国保連 CSV・返戻の確認（項目・仕様の確認待ち） |
| R4 様式（リハビリ記録） | — | 様式を受け取ってから |
| 施設内の電波 | 書きかけの自動保存（端末に残る） | オフラインでの利用 |

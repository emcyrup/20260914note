# 居宅介護の事業所「居宅介護支援事業所コト」の本番環境（ゆあーず・オウルと同じサーバー・別ポート・別 DB）

対象: `https://kaigonote.koto.ai-labo.cloud/` → プロバイダ管理の nginx（HTTPS 終端）→ Gunicorn `0.0.0.0:8043` → プロバイダ管理の PostgreSQL（DB `kaigonotekoto`、ユーザー `ai_labo_dbuser`）。2026-10-21 にプロバイダ側で設定ずみ（nginx・HTTPS・ポート 8043・DB）。

コトの機能（画面の型「居宅介護」・`homecare`・`kaigo` アプリ）は **`kyotaku` ブランチだけ**にあります。開発環境（`docs/DEPLOY_AWS_KYOTAKU.md`。`st-kaigonote.kyotaku.ai-labo.cloud`）で確かめてから、本番へは**手で**出します（push では動きません）。

ゆあーず（`docs/DEPLOY_AWS_YOURS.md`。`~/michinoteyours`・8030・DB `michinoteyours`）と**同じサーバー・同じ仕組み**で、置き場所・ポート・DB・`.env` が違うだけです。venv（`~/env`）はゆあーず・オウルと共用します。DB が別なので、コトの利用者・記録はゆあーず・オウル・開発環境からは見えません。

| | 開発環境のコト | 本番のコト |
|---|---|---|
| アドレス | `st-kaigonote.kyotaku.ai-labo.cloud` | `kaigonote.koto.ai-labo.cloud` |
| サーバー | 開発環境のサーバー | ゆあーず・オウルと同じサーバー |
| 置き場所 | `~/kaigonotekyotaku` | `~/kaigonotekoto` |
| Gunicorn | `0.0.0.0:8039` | `0.0.0.0:8043` |
| DB | `kaigonotekyotaku`（`st_ai_labo_dbuser`） | `kaigonotekoto`（`ai_labo_dbuser`。ゆあーずと同じユーザー・同じパスワード） |
| 配備 | Deploy (kyotaku)（`kyotaku` への push） | **Deploy (koto)**（Actions から手で。ブランチ `kyotaku` を選ぶ） |
| 管理 | Manage (kyotaku) | **Manage (koto)** |

秘密情報（DB パスワード、`SECRET_KEY`、Anthropic キー、SSH 秘密鍵、管理者のパスワード）はサーバーの `.env`・GitHub Secrets だけに置きます（リポジトリ・Notion・Actions のログには書かない）。DB パスワードはゆあーずと同じユーザーなので、初回の配備でサーバーの中で `~/michinoteyours/.env` から写します。

## 1. GitHub Secrets

SSH は同じサーバーなのでゆあーずの `YOURS_DEPLOY_*` をそのまま使います（別にしたいときだけ `KOTO_DEPLOY_*`）。

| 名前 | 入れるもの |
|---|---|
| `KOTO_ADMIN_PASSWORD` | 本番の管理者のパスワード（無ければ開発環境と同じ `KYOTAKU_ADMIN_PASSWORD` を使う） |
| `KOTO_DOMAIN`・`KOTO_PORT` | アドレス・ポートを変えるときだけ（既定は上の表） |

## 2. 配備（Actions から。サーバーに入らなくてよい）

Actions → **Deploy (koto)** → Run workflow（ブランチ **`kyotaku`**）。

- 初回は `~/kaigonotekoto` に clone し、`.env` を `deploy/.env.koto-aws.example` から作る（`SECRET_KEY` はサーバー上で生成、`DB_PASSWORD`・`ANTHROPIC_API_KEY`・音声の設定は `~/michinoteyours/.env` から写す。ログには項目名だけ）。2 回目以降は `.env` を上書きしない
- コトの機能（`kaigo`）が無いコミット（ゆあーずのブランチなど）を選ぶと止まる
- venv はゆあーずと共用なので、`requirements.txt` がゆあーずと違うと止まる（ゆあーずの部品を変えないため）
- そのあと `deploy/venv-deploy.sh` が `pip install` → `migrate` → `collectstatic` → Gunicorn 起動 → `https://kaigonote.koto.ai-labo.cloud/healthz/` の確認まで行う。再起動するのはコトの Gunicorn だけ（ゆあーず・オウルは止まらない）
- 切り戻し：Run workflow の「配備するコミット」に前のコミットの番号を入れる

## 3. 事業所と管理者を作る（初回だけ）

Actions → **Manage (koto)** → `task=create_facility`（事業所名「居宅介護支援事業所コト」・管理者 `kyotaku` が既定。サンプルデータは入れない）。そのあと `task=autostart_install` で、サーバー再起動のあとの自動起動・5 分ごとの見回り・毎日 3:15 の DB バックアップを入れる。

`https://kaigonote.koto.ai-labo.cloud/` にログインし、施設設定で事業所番号・住所・電話を入れる。利用者はカイポケの CSV を「カイポケ CSV から取り込む」で入れる。

## 4. 困ったとき

- 画面がエラー：Manage (koto) の `error_log`（日本語と長い数字は伏せて出る）
- 動いているか・いまのコミット：`status`
- 写真・書類が開けない：`set_media_root`
- バックアップ：`backup`・`backup_list`・`backup_check`

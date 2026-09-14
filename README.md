# 放課後等デイサービス業務支援システム v2

放課後等デイサービス事業所の職員向け業務支援Webアプリケーションです。

## 主な機能

- 利用者台帳（受給者証・保護者管理・OCR読み取り）
- 予定管理（月次カレンダー・一括生成・ステータス管理）
- 日次記録（AI文章整え・音声入力・電子サイン・LINE配信）
- 個別支援計画（アセスメント → 原案 → 担当者会議 → 説明・同意・交付 → モニタリング の5ステップ制。前のステップが完了しないと次に進めず、各ステップの残り項目をチェックリストで表示）
- 請求管理（請求マトリックス・上限額管理・請求書PDF・領収書PDF）
- LINE連携（保護者向けメッセージ配信・Webhook）
- 施設設定（タグ管理・加算マスタ）
- 画面の着せ替え（スタンダード／大きな文字／スタイリッシュ／かんたん3ステップ。職員ごとに割り当て可、記録は共有のまま）
- AI加算提案（日誌を保存すると算定できそうな加算を提案。「採用する」で請求マトリックスへ反映）
- AIチャットボット（右下の💬。利用者の出席日数・最近の様子、加算の要件などを質問）
- 算定要件資料（PDF をアップロードして「ベクトル化」すると、加算提案とチャットが資料を根拠に答える）
- AI が出した観察の観点に「はい／いいえ」を付け、その結果をもとに考察を作り直せる

## 画面の着せ替え

サイドバー下の「画面の見た目」（`/accounts/theme/`）で、同じ記録を共有したまま見た目を切り替えられます。管理者は職員ごとに割り当てできます（管理画面の職員アカウントにも「画面の見た目」欄があります）。

| 画面 | 向いている人 | 内容 |
|---|---|---|
| スタンダード | どの事業所でも | 標準の画面。ホームには今週の来所状況（人数と日誌の作成状況）が出ます |
| 大きな文字 | パート職員・年配の方 | 文字を約1.6倍、ボタンを大きく、請求・設定などの項目を隠し、コントラストを上げる |
| スタイリッシュ | 見せる場面が多い事業所 | 濃色の背景で装飾を抑える |
| かんたん3ステップ | とにかく迷わせたくない現場 | メニューを隠し「きょうの きろく」（`/records/simple/`）に今日来る子だけを並べる。①メモを入れる ②下書きをつくる ③確認して確定 の3つだけ |

### 細かい設定（フォント・ダークモード・背景色・文字の大きさ）

「画面の見た目」ページの下の「細かい設定」で、4パターンとは独立に自分だけの表示を変えられます。

| 項目 | 選べるもの |
|---|---|
| フォント | BIZ UDPゴシック（標準）／Noto Sans JP／M PLUS Rounded 1c（丸ゴシック）／Noto Serif JP（明朝）／端末の標準 |
| ライト／ダーク | ライト／ダーク／端末の設定に合わせる（ダークはどのパターンでも使えます） |
| 文字の大きさ | 90／100／115／130% |
| 背景色 | プリセット9色、または好きな色（カラーピッカー）。「標準」でテーマの色に戻る |

保存前にプレビューで確かめられます。設定は職員ごとに保存され、端末を変えても同じ見た目になります。

## 日誌の観点（はい／いいえ）

活動名から AI が「めあて」と観察の観点を出します。観点ごとに「はい」（できていた）「いいえ」（できていなかった）を付け、「はい／いいえを反映して考察を作り直す」を押すと、その結果を事実として考察を書き直します。観点は手で追加・修正・削除でき、日誌の詳細と かんたん3ステップ画面にも表示されます。

## 日誌テンプレート（ある利用者の日誌を他の利用者でも使う）

- 日誌の詳細右上の <i>ファイル</i> ボタン（テンプレートにする）で、その日誌を施設共通のテンプレートとして保存します。活動・めあて・観点（はい／いいえは空に戻す）・記録文・保護者向けメッセージ・タグ・5領域を引き継ぎ、写真・時刻・体調は含みません。
- 保存時に、元の利用者の姓名・名・姓（かな含む）は文中で `{名前}` に置き換えられます。他の利用者の「日誌を追加」「編集」の上部にある「テンプレートから入力…」→「反映」で、その利用者の名前に置き換えた内容がフォームに入ります。
- 「記録」画面右上の「テンプレート」から一覧・編集（文面・観点・タグ・5領域）・削除ができます。使用回数の多い順に並びます。テンプレートは施設ごとに分かれ、他施設からは見えません。

## AI加算提案・チャットボット・算定要件資料

すべて `ANTHROPIC_API_KEY` が必要です（未設定なら何もしません）。

- **AI加算提案**: 日誌を保存すると、日誌の内容（タグ・記録文・送迎の有無）と個別加算の一覧、資料の抜粋をもとに、算定できる可能性のある加算を提案します（`AI_ADDON_SUGGESTIONS=False` で停止）。日誌の詳細の「AI加算提案」欄で「採用する」を押すと請求マトリックスのセルに加算が入り、未確認（◆）に戻ります。ポップアップで確認・保存してください。「見送る」で除外、どちらも取り消せます。「提案を作り直す」で再判定します
- **AIチャットボット**: 画面右下の💬。質問に利用者名が含まれると、その利用者の今月の出席・欠席、受給者証、支援計画の目標、直近5件の日誌を参考情報として答えます。同じ名前の利用者が複数いるときは選択肢を出します。加算の質問には加算マスタと資料の抜粋を根拠にします。会話はブラウザのタブを閉じるまで保持されます。「かんたん3ステップ」画面では表示しません
- **算定要件資料**: 施設設定 → 「算定要件資料」で PDF（30MB まで、文字が埋め込まれたもの）をアップロードし「ベクトル化する」を押します（管理者のみ）。文字を取り出して 600 字前後の断片に分け、文字バイグラムの BM25 で検索できるようにします。外部の埋め込み API は使わないため追加のキーは不要です。資料が無くても動きますが、AI が一般知識だけで判断するため精度が下がります

## 職員・運用管理、呼び方・ロゴ・配色

- **職員・運用管理**（管理者のみ、サイドバー「設定」の下）：職員の追加（ログインID・表示名・権限区分・画面・初期パスワード）、編集、パスワード再設定、無効化。退職者は削除せず無効にすると記録者名が残ります
- **呼び方・ロゴ・配色**（施設設定 → 施設基本情報）：「職員」「利用者」の呼び方（支援員／利用児 など）をメニューと主な見出しに反映。ロゴ画像はサイドバーに、コーポレートカラーは見出し・ボタン・進捗の色に反映されます
- 呼び方は、サイドバー・各画面の見出し・表の列名・空のときの案内・保存時のメッセージ・ページタイトルまで置き換わります（法令上の用語「児童発達支援管理責任者」「利用者負担上限額」などはそのままです）。

## 別の事業所（施設）を追加する

記録・利用者・計画・請求は施設ごとに完全に分かれ、職員は1つの施設にだけ所属します。同じアプリ内に事業所を増やすには、サーバーで次を実行します。

```bash
python manage.py create_facility "あおば教室" --admin aoba_admin --password '初期パスワード' \
    --copy-settings-from 1      # 既存施設（ID）の呼び方・配色・使う機能・日誌の項目順・単位数をコピー（任意）
    # --office-number 1234567890  --display-name "管理者名"  --demo（架空データも投入）
```

標準の活動タグ・支援内容タグが入り、管理者アカウント（権限区分＝管理者）ができます。その管理者でログインし、施設設定で事業所番号・呼び方・使う機能を確認してから、「職員・運用管理」で職員を追加してください。`/admin/`（Django 管理画面）の「施設」「職員アカウント」から手で作ることもできます。

## 使う機能の選択、日誌の項目の順番

施設設定 →「使う機能と、日誌で AI が作る項目」（管理者のみ）で次を設定します。

- **使う機能**：請求機能（請求マトリックス・上限額管理・請求書・AI加算提案）と LINE 連携を、それぞれ使う／使わないにできます。使わない機能はメニュー・ボタン・設定欄から消え、URL を直接開いてもホームに戻ります。記録は消えません。既存の施設は「使わない」で始まります（新しく作る施設は「使う」）。
- **日誌の項目と順番**：AI が作る項目（活動・めあて・観点・考察／観察・活動内容／支援内容／本人の反応／保護者向けメッセージ）の使う・使わないと順番を決めます。日誌の入力・詳細画面はこの順番で並び、AI の一括生成は上の項目ほど優先して丁寧に書きます。

## 紙の日誌の取り込み（カメラ）

「記録」→「紙の日誌を取り込む」（ホームのクイックアクセスにもあります）で、過去の紙の日誌をスマートフォンのカメラで撮るか画像を選んで取り込みます。

1. 写真を取り込む（1回に 20 枚まで。1枚に1日分）
2. 「AIで読み取る」で、日付・利用者名・入退室時間・体調・活動・めあて・観点（○×）・観察・支援・反応・考察・保護者向けの各項目に分けます。読めなかった箇所は「読めない箇所」として表示します
3. 写真を見ながら内容を確認・修正し、「日誌として保存」。写真は日誌に添付され、同じ日の日誌がすでにある場合は上書きの確認が出ます

読み取りには `AI_PLAN_MODEL`（既定 `claude-opus-5`）の画像入力を使います。画像は送信前に長辺 1600px に縮小します。

## 帳票出力（CSV・PDF）

サイドバー「請求」→「帳票出力」から、次の帳票を CSV（Excel で開ける BOM 付き UTF-8）・PDF・画面表示（印刷確認用）で出力できます。

| 帳票 | 単位 | 内容 |
|---|---|---|
| サービス提供実績記録票 | 利用者×月 | 日付ごとの提供状況（利用／欠席／振替）、入退室時間、送迎、加算、備考、保護者確認欄。受給者証番号・支給量・利用日数の集計つき |
| 出欠・利用実績一覧 | 施設×月 | 在籍者全員の予定・来所・欠席・振替・送迎回数・日誌作成数・請求マトリックスの状態 |
| 業務日誌（日別） | 施設×日 | その日の日誌（入退室・体調・活動・めあて・観察・支援・反応・記録者） |
| 支援記録（日誌）一覧 | 利用者×期間 | 期間内の日誌を時系列で（観点のはい／いいえ、観察・支援・反応・考察） |
| 利用者名簿 | 施設 | 基本情報・利用曜日・保護者連絡先・受給者証（番号・有効期間・支給量・上限月額） |
| 個別支援計画一覧 | 施設 | 状態・現在のステップ・計画期間・次回モニタリング期限 |

個別支援計画書・モニタリング報告書の PDF は各計画の画面、請求書・領収書・請求集計 CSV は「請求」メニューから出力します。帳票の列やレイアウトは `reports/services.py` と `templates/reports/print.html` で調整できます。

## AI が作る文章の品質

AI が生成する記録文（日誌の一括生成・文章整え・めあて／考察、支援計画の下書き、加算提案、チャット）には共通の「日本語の書き方」ルールを付け、返答は `ai_assist/text.py` の `clean_ai_text` で整えてから画面に入れます（文字のまま残ったエスケープや半角カタカナ、ゼロ幅文字、マークダウン、先頭のラベルを除去）。文章生成のモデルは既定で `claude-opus-5` です。費用を抑えたい場合は `.env` に `AI_TEXT_MODEL=claude-sonnet-5` を指定してください。

## 個別支援計画の進め方

サイドバー「支援計画」→「新しい計画を作る」で利用者を選ぶと、次の5ステップを順番に進めます。

| ステップ | 完了に必要な項目 |
|---|---|
| 1. アセスメント | 面談日／面談相手／心身の状況／置かれている環境／希望する生活 |
| 2. 計画（原案）の作成 | 計画期間／総合的な支援の方針／長期目標（達成時期）／短期目標（達成時期・具体的な支援内容） |
| 3. 担当者会議 | 開催日／出席者／本人の出席（欠席なら理由）／協議内容／修正の有無（修正した場合は内容） |
| 4. 説明・同意・交付 | 説明日と相手／文書での同意（電子サイン、または書面の署名者と同意日）／本人・家族への交付／相談支援事業所への交付／サービス開始日 |
| 5. モニタリング | 初回の面談記録。以後は記録ごとに次回期限を設定し、期限超過は一覧と利用者ページに表示（間隔の既定は3か月） |

- 各ステップは「途中保存」でいつでも保存でき、右側のチェックリストに残り項目が出ます。すべて揃うと「保存してステップNを完了」で次へ進みます
- 完了したステップは読み取り専用になります。修正したいときは直前に完了したステップだけ「完了を取り消して修正」できます（さらに先へは進めなくなります）
- ステップ4の完了でサービス開始（実施中）になり、ステップ5で見直しが必要になったら「次の計画を作成」で第2期を始められます（前回のアセスメントを引き継ぎ、新しい計画の交付で前の計画は終了）
- 「計画書」「計画書PDF」で交付用の計画書（署名欄つき）、ステップ5では「モニタリング報告書」と PDF を出力できます
- **AI の下書き**（`ANTHROPIC_API_KEY` が必要）：ステップ1「過去の日誌から AI で下書き」で直近の日誌から心身の状況・環境・希望の下書きを入れます。ステップ2「期間の日誌から AI で原案の下書き」で方針と目標を下書きし、根拠になった日誌を目標にひもづけます。下書きは編集して完了するまで計画になりません
- **根拠になった記録**：各目標の「選ぶ」で期間内の日誌を関連度順に並べ、根拠としてひもづけられます。計画書と PDF に根拠の日付が出ます
- 日誌画面の左メニュー「モニタリング」「アセスメント」「計画書をつくる」から、その利用者の進行中の計画に移動できます

## 技術構成

| 役割 | 技術 |
|---|---|
| バックエンド | Python 3.12 以上 / Django 6 |
| データベース | PostgreSQL（pgvector拡張対応） |
| フロントエンド | Bootstrap 5 / HTMX / Alpine.js |
| AI | Anthropic Claude API |
| PDF生成 | WeasyPrint |
| LINE連携 | LINE Messaging API |
| 本番サーバー | Gunicorn + Nginx |

## ローカル開発環境のセットアップ

```bash
# 1. リポジトリをクローン
git clone <リポジトリURL>
cd <プロジェクトフォルダ>

# 2. 仮想環境を作成・有効化
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 3. 依存パッケージをインストール
pip install -r requirements.txt

# 4. 環境変数ファイルを作成
cp .env.example .env
# .env を編集して各種APIキーを設定する

# 5. データベースを構築
python manage.py migrate

# 6. 管理者アカウントを作成
python manage.py createsuperuser

# 7. 開発サーバーを起動（macOSはWeasyPrint用にDYLD_LIBRARY_PATHを指定）
DYLD_LIBRARY_PATH=/opt/homebrew/lib python manage.py runserver
```

ブラウザで `http://localhost:8000` を開いてください。

## 環境変数

`.env.example` をコピーして `.env` を作成し、以下の項目を設定してください。

| 変数名 | 説明 |
|---|---|
| `SECRET_KEY` | Djangoのシークレットキー（本番では長いランダム文字列を使用） |
| `DEBUG` | 開発時は `True`、本番は `False` |
| `DJANGO_ALLOWED_HOSTS` | アクセスを許可するホスト名（例: `example.com,localhost`） |
| `DB_NAME` / `DB_USER` / `DB_PASSWORD` / `DB_HOST` / `DB_PORT` | PostgreSQL の接続情報。`DB_NAME` が空欄の場合はSQLiteを使用 |
| `CSRF_TRUSTED_ORIGINS` | リバースプロキシ越しのHTTPSで使うオリジン（例: `https://app.example.com`） |
| `MEDIA_ROOT` | 写真などアップロードファイルの保存先（Dockerではボリュームを割り当てる） |
| `ANTHROPIC_API_KEY` | Anthropic Claude APIキー |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE Messaging APIのチャネルアクセストークン |
| `LINE_CHANNEL_SECRET` | LINE Messaging APIのチャネルシークレット |

## サーバーへのデプロイ

```bash
# 1. リポジトリをクローン
git clone <リポジトリURL> ~/dayservice
cd ~/dayservice

# 2. 仮想環境を有効化（サーバー共通の仮想環境を使用）
source ~/env/bin/activate

# 3. 依存パッケージをインストール
pip install -r requirements.txt

# 4. .env ファイルを作成して環境変数を設定
cp .env.example .env
vi .env

# 5. データベースを構築
python manage.py migrate

# 6. 静的ファイルを収集
python manage.py collectstatic --noinput

# 7. Gunicornで起動
gunicorn config.wsgi:application --bind 0.0.0.0:8019 --workers 2
```

## 2回目以降のデプロイ（コード更新時）

```bash
cd ~/dayservice
source ~/env/bin/activate
git pull
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
# Gunicornを再起動（systemctlまたはプロセスを再起動）
```

## AWS 開発環境（共用サーバー：sudo なし・Docker なし・venv＋Gunicorn）へのデプロイ

`https://st-michinotedemo.ai-labo.cloud/` のように、プロバイダ管理の nginx が HTTPS を終端して割り当てポート（8029）の Gunicorn に中継し、DB もプロバイダ管理の PostgreSQL を使う構成向けです。サーバー上では `deploy/venv-deploy.sh` が git checkout → pip → migrate → collectstatic → Gunicorn 再起動を行い、ワークフロー **Deploy (dev)**（`develop` への push または手動実行）がそれを SSH で呼びます。`.env` の雛形は `deploy/.env.dev-aws.example`。手順と GCP からのデータ移行は [docs/DEPLOY_AWS_DEV.md](docs/DEPLOY_AWS_DEV.md) を参照してください（Docker が使えるサーバー向けの `deploy/docker-compose.external.yml` も同じ文書に載せています）。

## GCP へのデプロイ（最小コスト構成）

> ターミナルや Terraform を使わず **Cloud Console の画面だけ**で構築する手順は [docs/DEPLOY_GCP_CONSOLE.md](docs/DEPLOY_GCP_CONSOLE.md) を参照してください。以下は CLI / Terraform で行う手順です。

Compute Engine 1台に Docker Compose（Caddy → Gunicorn/Django → PostgreSQL）を載せる構成です。
インフラは Terraform（`infra/terraform/`）、デプロイは GitHub Actions（`.github/workflows/deploy.yml`）が行います。

```
GitHub (main に push)
  └─ Actions: テスト → Docker イメージを GHCR に push → SSH でサーバーへ
                                                          │
Compute Engine（固定IP・e2-small）                          ▼
  └─ docker compose: caddy(443, 自動HTTPS) → app(8000) → db(PostgreSQL)
                      └─ /media は Caddy が配信          └─ 毎日 3:30 pg_dump → Cloud Storage（30日保持）
```

月額の目安（東京 asia-northeast1・e2-small）：VM 約 $16 ＋ ディスク 約 $1.5 ＋ 外部IP 約 $3.7 ＋ Cloud Storage 数十円 ≒ **約 $21**。
`machine_type = "e2-micro"` にすると約 $13、さらに `region = "us-central1"` にすると Always Free 枠で VM 代が $0 になります（日本からの遅延は増えます）。
Cloud SQL を使わないぶん安く、代わりに DB はサーバー内のコンテナで可用性は1台分です。

### 1. GCP プロジェクトと手元の準備（1回）

```bash
brew install --cask google-cloud-sdk && brew install terraform
gcloud auth login
gcloud projects create dayservice-prod-123456 --name="放デイ業務支援"   # ID は世界で一意
gcloud billing accounts list
gcloud billing projects link dayservice-prod-123456 --billing-account=XXXXXX-XXXXXX-XXXXXX
gcloud config set project dayservice-prod-123456
gcloud auth application-default login          # Terraform が使う認証
```

### 2. インフラを作る（1回）

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars   # project_id を書き換える
terraform init
terraform apply
terraform output          # static_ip / ssh_command / backup_bucket が出る
```

DNS の A レコードを `static_ip` に向けてください（ドメインなしでも `http://<IP>` で検証できます）。

### 3. サーバーに .env を置く（1回）

```bash
ssh deploy@<static_ip>
# 初回起動時に deploy/setup-server.sh が Docker などを入れている（ログ: /var/log/cloud-init-output.log）
nano /opt/dayservice/.env      # deploy/.env.production.example を元に作成
```

バックアップを Cloud Storage に送るには `.env` に `BACKUP_GCS_BUCKET=<terraform output backup_bucket>` を足すだけです（VM のサービスアカウントで認証されるためキーは不要）。

### 4. GitHub Secrets を登録する（1回）

| Secret | 値 |
|---|---|
| `DEPLOY_HOST` | `terraform output static_ip` |
| `DEPLOY_SSH_KEY` | `~/.ssh/id_ed25519` の中身（Terraform に登録した公開鍵の対） |
| `DEPLOY_USER` | `deploy`（terraform の `ssh_user` を変えた場合のみ。省略時は `deploy`） |
| `GHCR_PULL_TOKEN` | `read:packages` 権限の Personal Access Token（リポジトリが Public なら不要） |

### 5. デプロイする

`main` に push するだけです（Actions → Deploy から手動実行も可）。初回はデプロイ後に管理者を作成します。

```bash
ssh deploy@<static_ip>
cd /opt/dayservice && docker compose exec app python manage.py createsuperuser
```

### サンプルデータを入れる（任意）

画面の動きを確認したいときは、架空の利用者6名・予定・日誌・請求データ・進み具合の違う個別支援計画5件をまとめて投入できます（利用者の備考に「サンプルデータ」と入ります）。

```bash
cd /opt/dayservice
docker compose exec app python manage.py seed_demo            # 投入
docker compose exec app python manage.py seed_demo --reset    # 消して入れ直す
```

サンプルだけを消すには、管理画面で備考が「サンプルデータ」の利用者を削除します（予定・日誌・請求は連動して消えます）。

### ドメインと HTTPS

手順は [docs/HTTPS.md](docs/HTTPS.md)。`.env` の4項目（DOMAIN / DJANGO_ALLOWED_HOSTS / CSRF_TRUSTED_ORIGINS / SECURE_SSL_REDIRECT）を変えて `docker compose up -d` するだけです。

### 運用

```bash
cd /opt/dayservice
docker compose ps                       # 状態
docker compose logs -f app              # アプリのログ
docker compose exec app python manage.py shell
./backup.sh                             # 手動バックアップ（毎日 3:30 に自動実行）
# 復元: gunzip -c backups/db-YYYYMMDD-HHMMSS.sql.gz | docker compose exec -T db psql -U dayservice dayservice
```

全部消すときは、バケットを空にしてから `terraform destroy` します：
`gcloud storage rm -r gs://<backup_bucket>/db && cd infra/terraform && terraform destroy`

ローカルで本番イメージを試す場合:

```bash
docker build -t dayservice .
docker run --rm -p 8000:8000 -e SECRET_KEY=dev -e DEBUG=True -e DJANGO_ALLOWED_HOSTS=localhost dayservice
```

## LINE Webhook の設定

デプロイ後、以下の手順でLINE連携を有効にしてください。

1. 施設設定画面でLINEチャネルアクセストークン・シークレットを入力
2. LINE Developers ConsoleでWebhook URLを登録
   - URL: `https://<ドメイン>/line/webhook/`
   - 「Webhookの利用」をONにする
   - 「応答メッセージ」はOFFにする

## ライセンス

本ソフトウェアは株式会社スリーピース向けに開発されたものです。

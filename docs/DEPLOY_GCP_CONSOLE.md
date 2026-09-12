# GCP へのデプロイ（Cloud Console だけで行う手順）

Terraform やターミナルを使わず、**Cloud Console（ブラウザ）と GitHub の画面だけ**で構築する手順です。
サーバー内の作業は Console の「ブラウザで SSH」を使います。CLI / Terraform で行う場合は README の「GCP へのデプロイ」を参照してください。

構成と月額の目安は README と同じです（東京・e2-small で約 $21/月、e2-micro で約 $13/月、us-central1 の e2-micro なら Always Free 枠）。

## 1. プロジェクトと請求先

1. [console.cloud.google.com](https://console.cloud.google.com/) → **プロジェクトを選択 → 新しいプロジェクト** → 名前「放デイ業務支援」→ 作成。自動で付く **プロジェクト ID** を控える
2. **お支払い** → 請求先アカウントを作成し、このプロジェクトに **リンク**
3. **Compute Engine → VM インスタンス → 有効にする**（1〜2分）

## 2. 固定IPを予約する

**VPC ネットワーク → IP アドレス → 外部静的 IP アドレスを予約**

| 項目 | 値 |
|---|---|
| 名前 | `dayservice-ip` |
| タイプ | リージョン |
| リージョン | asia-northeast1（東京） |
| 接続先 | なし |

表示された IP アドレスを控える（以後「固定IP」）。

## 3. VM を作る

先に GitHub で `deploy/setup-server.sh` を開き **Raw** → 全文をコピーしておく。

**Compute Engine → VM インスタンス → インスタンスを作成**

| 画面 | 項目 | 値 |
|---|---|---|
| マシン構成 | 名前 | `dayservice` |
| | リージョン / ゾーン | asia-northeast1 / asia-northeast1-a |
| | シリーズ / マシンタイプ | E2 / **e2-small**（安く: e2-micro） |
| OS とストレージ → 変更 | OS / バージョン | Ubuntu / **Ubuntu 22.04 LTS（x86/64）** |
| | ブートディスクの種類 / サイズ | 標準永続ディスク / 30 GB |
| ネットワーキング | ファイアウォール | ☑ HTTP トラフィックを許可 ☑ HTTPS トラフィックを許可 |
| | ネットワーク インターフェース → 外部 IPv4 | 手順2の `dayservice-ip` |
| セキュリティ | サービス アカウント | Compute Engine default service account |
| | アクセス スコープ | **すべての Cloud API に完全アクセス権を許可** |
| 詳細設定 → 管理 → 自動化 | 起動スクリプト | `setup-server.sh` の全文を貼り付け |

**作成** → 3〜5分待つ → VM 一覧の **SSH** ボタンでブラウザの端末を開いて確認：

```bash
sudo journalctl -u google-startup-scripts --no-pager | tail -n 5   # setup-server: done
docker --version
```

## 4. バックアップ用バケット

**Cloud Storage → バケット → 作成**

| 項目 | 値 |
|---|---|
| 名前 | `dayservice-backups-<プロジェクトID>` |
| ロケーション | Region / asia-northeast1 |
| ストレージ クラス | Standard |
| アクセス制御 | 均一 |
| 公開アクセスの防止 | 適用する |

- **ライフサイクル** タブ → ルールを追加：オブジェクトを削除 / 経過日数 30 / 名前の接頭辞 `db/`
- **権限** タブ → アクセス権を付与：プリンシパル `<プロジェクト番号>-compute@developer.gserviceaccount.com`、ロール **Storage オブジェクト管理者**
  （プロジェクト番号は IAM と管理 → 設定 に表示）

## 5. ブラウザで SSH：デプロイ用の鍵と .env

VM 一覧の **SSH** ボタンで端末を開く。

### 5-0. 準備ができているか確認（必ず最初に）

```bash
id deploy && docker --version && echo "準備OK"
```

`no such user` や `docker: command not found` が出たら起動スクリプトが走っていない。その場で実行し直す（2〜4分、末尾に `setup-server: done`）：

```bash
curl -fsSL https://raw.githubusercontent.com/emcyrup/20260914note/claude/awesome-franklin-2ixofk/deploy/setup-server.sh | sudo bash
```

curl が使えない場合は `sudo nano /root/setup-server.sh` に `deploy/setup-server.sh`（Raw）の全文を貼って保存し、`sudo bash /root/setup-server.sh`。

### 5-1. GitHub Actions がログインするための鍵

`sudo -u deploy -i` は **1行だけ先に実行**し、プロンプトが `deploy@dayservice:~$` に変わってから残りを貼る（まとめて貼ると後続行が正しく実行されないことがある）。

```bash
sudo -u deploy -i
```

```bash
mkdir -p ~/.ssh && chmod 700 ~/.ssh
ssh-keygen -t ed25519 -N "" -C deploy -f ~/.ssh/github_deploy
cat ~/.ssh/github_deploy.pub >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys
cat ~/.ssh/github_deploy        # BEGIN〜END の全文を控える（手順7で GitHub に登録）
rm ~/.ssh/github_deploy         # 登録後、サーバーから秘密鍵を消す
```

### 5-2. 秘密の値を生成

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(64))'   # SECRET_KEY
openssl rand -base64 24                                          # DB_PASSWORD
```

### 5-3. .env を作る

```bash
nano /opt/dayservice/.env      # deploy/.env.production.example を元に作成
chmod 600 /opt/dayservice/.env
exit
```

まず IP で動かす場合の最小構成：

```
SECRET_KEY=（5-2で生成）
DEBUG=False
DJANGO_ALLOWED_HOSTS=<固定IP>
CSRF_TRUSTED_ORIGINS=http://<固定IP>
SECURE_SSL_REDIRECT=False
DOMAIN=
DB_NAME=dayservice
DB_USER=dayservice
DB_PASSWORD=（5-2で生成）
ANTHROPIC_API_KEY=
BACKUP_GCS_BUCKET=dayservice-backups-<プロジェクトID>
```

ドメインを付けるときは `DJANGO_ALLOWED_HOSTS` / `CSRF_TRUSTED_ORIGINS=https://…` / `SECURE_SSL_REDIRECT=True` / `DOMAIN=…` に直して `docker compose up -d`。

## 6. ドメインを向ける（任意）

ドメイン管理画面で A レコードを固定IPに向ける。HTTPS が自動で付き、LINE の Webhook に必要。

## 7. GitHub Secrets と main ブランチ

**Settings → Secrets and variables → Actions → New repository secret**

| Secret | 値 |
|---|---|
| `DEPLOY_HOST` | 固定IP |
| `DEPLOY_SSH_KEY` | 5-1 の秘密鍵の全文 |
| `GHCR_PULL_TOKEN` | Private リポジトリのときだけ（Tokens (classic)、スコープ `read:packages`） |

リポジトリのトップでブランチ名をクリック → `main` と入力 → **Create branch main from …**。
作成と同時に **Actions → Deploy** が走る（初回 5〜8分）。完了後 `http://<固定IP>/healthz/` で `ok` が出れば成功。
Settings → General → Default branch を `main` にしておくと以後の運用が楽。

## 8. 初期設定

```bash
sudo -u deploy -i
cd /opt/dayservice
docker compose exec app python manage.py createsuperuser
exit
```

`/admin/` にログイン → **施設** を追加 → **職員アカウント** で自分に所属施設と権限区分（管理者）を設定 → `/` でホームが開く。

## 9. 運用

```bash
sudo -u deploy -i && cd /opt/dayservice
docker compose ps
docker compose logs -f app
./backup.sh                                  # 毎日 3:30 に自動実行
gunzip -c backups/db-YYYYMMDD-HHMMSS.sql.gz | docker compose exec -T db psql -U dayservice dayservice   # 復元
```

- 更新: GitHub で `main` に変更を入れると自動デプロイ（Actions → Deploy → Run workflow で手動も可）
- 料金: お支払い → 予算とアラート で上限を決めておく
- 全部消す: VM 削除（ブートディスクも）、固定IPの解放、バケット削除。固定IPだけ残すと課金が続く

## 10. つまずいたら

| 症状 | 対処 |
|---|---|
| Compute Engine を有効にできない | 請求先がこのプロジェクトにリンクされていない |
| `setup-server: done` が出ない | 起動スクリプトが貼れていない。手順 5-0 の `curl … \| sudo bash` でその場で実行（VM を編集して起動スクリプトに貼っておくと再起動時も自動実行） |
| `sudo -u deploy -i` で「no such user」／`docker: command not found` | 同上（手順 5-0 の修復ブロック） |
| 複数行を貼ったのに一部しか実行されない | `sudo -u deploy -i` は単独で実行し、プロンプトが `deploy@` に変わってから残りを貼る |
| deploy ジョブ `.env がありません` | 5-3 が未完了 |
| deploy ジョブ `Permission denied (publickey)` | `DEPLOY_SSH_KEY` が 5-1 の全文と一致していない |
| deploy ジョブ `Connection timed out` | VPC ネットワーク → ファイアウォールに `default-allow-ssh` があるか |
| 502 Bad Gateway | `docker compose logs app` を確認（SECRET_KEY 未設定・DB_PASSWORD 不一致が多い） |
| `CSRF verification failed` | `CSRF_TRUSTED_ORIGINS` が実際の URL と違う |
| IP で動かしているのにログインできない | `SECURE_SSL_REDIRECT=False` を確認して `docker compose up -d` |
| バックアップが Cloud Storage に届かない | VM のアクセススコープ（手順3）とバケットの権限（手順4） |

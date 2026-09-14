# ドメインと HTTPS の導入手順

音声入力（ブラウザのマイク）と LINE の写真送信・Webhook は HTTPS が必須です。
Caddy が証明書（Let's Encrypt）を自動で取得・更新するため、サーバー側の作業は `.env` の4項目だけです。

## 1. ドメインを用意する

- 例：`dayservice.example.jp`（お名前.com・ムームードメイン・Cloudflare などで取得。サブドメインでも可）
- DNS の管理画面で **A レコード** を追加：

| 種別 | ホスト名 | 値 |
|---|---|---|
| A | `dayservice`（または `@`） | `<サーバーの固定IP>`（固定IP） |

- Cloudflare を使う場合は、プロキシ（オレンジの雲）を **オフ（DNS only）** にする（Caddy が証明書を取るため）
- 反映を待つ。手元のパソコンで確認：

```bash
nslookup dayservice.example.jp     # <サーバーの固定IP> が返ればOK
```

## 2. ファイアウォールの確認（Cloud Console）

VM 作成時に「HTTPS トラフィックを許可」にチェックしていれば不要。
確認は **VPC ネットワーク → ファイアウォール** に `default-allow-https`（tcp:443）があるか。無ければ VM を編集して「HTTPS トラフィックを許可」をオンにする。

## 3. サーバーの .env を変える（ブラウザで SSH）

```bash
sudo -u deploy -i
```

```bash
cd /opt/dayservice
sed -i 's|^DOMAIN=.*|DOMAIN=dayservice.example.jp|' .env
sed -i 's|^DJANGO_ALLOWED_HOSTS=.*|DJANGO_ALLOWED_HOSTS=dayservice.example.jp,<サーバーの固定IP>|' .env
sed -i 's|^CSRF_TRUSTED_ORIGINS=.*|CSRF_TRUSTED_ORIGINS=https://dayservice.example.jp|' .env
sed -i 's|^SECURE_SSL_REDIRECT=.*|SECURE_SSL_REDIRECT=True|' .env
grep -E '^(DOMAIN|DJANGO_ALLOWED_HOSTS|CSRF_TRUSTED_ORIGINS|SECURE_SSL_REDIRECT)=' .env
docker compose up -d
```

`docker compose up -d` で Caddy が再作成され、初回アクセス時に証明書を取得します（1分ほど）。

## 4. 確認

```bash
curl -sI https://dayservice.example.jp/healthz/ | head -1     # HTTP/2 200
curl -sI http://dayservice.example.jp/healthz/  | head -1     # 308（HTTPS へ転送）
docker compose logs --tail=30 caddy                          # certificate obtained successfully
```

ブラウザで `https://dayservice.example.jp/` を開き、鍵マークが出ればログインして音声入力（マイクボタン）が使えることを確認します。

## 5. GitHub Actions の確認先を変える（任意）

Settings → Secrets → `DEPLOY_HOST` はそのまま IP でも配備できます（SSH は IP、ヘルスチェックは `http://IP/healthz/` が 200 を返す）。
ドメインでヘルスチェックしたい場合は `DEPLOY_HOST` をドメインに変更します（301/308 も成功扱い）。

## 6. LINE を使う場合

LINE Developers のチャネル設定で Webhook URL を `https://dayservice.example.jp/line/webhook/` にし、「Webhookの利用」をオン、「応答メッセージ」をオフ。
チャネルアクセストークンとシークレットは 施設設定 → 施設基本情報 に入力します。

## つまずいたら

| 症状 | 対処 |
|---|---|
| `https://` が開かない、証明書エラー | DNS がまだ反映されていない（`nslookup`）。Cloudflare のプロキシがオン。443 のファイアウォール。`docker compose logs caddy` に `obtaining certificate` の失敗理由が出る |
| `CSRF verification failed` | `CSRF_TRUSTED_ORIGINS` が `https://` 付きの正しいドメインか |
| `Bad Request (400)` | `DJANGO_ALLOWED_HOSTS` にドメインが入っているか |
| IP で開くと何も出ない | ドメイン運用に切り替えると Caddy はドメイン宛だけを受けます。IP で開きたい場合は `DOMAIN=` を空に戻す |
| 証明書の更新 | Caddy が自動で更新するため作業不要。VM を止めない限り切れません |

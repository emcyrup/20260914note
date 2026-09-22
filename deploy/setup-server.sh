#!/bin/bash
# =============================================
# サーバー初期設定（Ubuntu 22.04 / Compute Engine）
#   VM 作成時の「起動スクリプト」に貼るか、ブラウザ SSH で `sudo bash` で流す（冪等。何度実行してもよい）
#   - deploy ユーザー（GitHub Actions が SSH でログインする）
#   - Docker / Docker Compose plugin
#   - スワップ 2GB（e2-micro でも PDF 生成が落ちないように）
#   - APP_DIR（compose・Caddyfile・.env を置く場所。既定 /opt/simple）
#   - 毎日 3:30 の DB バックアップ cron
#   置き場所を変えるには、次のどれかを使う（上から優先）
#     1. VM のカスタムメタデータ `app-dir` に /opt/ryoiku などを入れる（起動スクリプトに貼るときはこれが楽）
#     2. APP_DIR=/opt/xxx bash setup-server.sh
# =============================================
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

# GCE のカスタムメタデータ app-dir があれば使う（メタデータサーバーが無い環境では無視する）
metadata_app_dir() {
  curl -fsS --max-time 2 -H 'Metadata-Flavor: Google' \
    http://metadata.google.internal/computeMetadata/v1/instance/attributes/app-dir 2>/dev/null || true
}

DEPLOY_USER=${DEPLOY_USER:-deploy}
APP_DIR=${APP_DIR:-$(metadata_app_dir)}
APP_DIR=${APP_DIR:-/opt/simple}
APP_NAME=$(basename "$APP_DIR")

# 初回起動直後は unattended-upgrades が apt を掴んでいることが多いので、空くまで待つ（最大10分）
wait_for_apt() {
  for _ in $(seq 1 120); do
    if ! fuser /var/lib/dpkg/lock-frontend /var/lib/apt/lists/lock >/dev/null 2>&1; then
      return 0
    fi
    sleep 5
  done
  echo "setup-server: apt lock timeout" >&2
  return 1
}
apt_install() {
  wait_for_apt
  apt-get -o DPkg::Lock::Timeout=300 install -y --no-install-recommends "$@"
}

# デプロイ用ユーザー。パスワード欄は '*'（パスワードなし・鍵ログインは可）にする。
# useradd の既定の '!' は「ロック」扱いで、sshd が鍵ログインも拒否する（Permission denied (publickey)）
id -u "$DEPLOY_USER" >/dev/null 2>&1 || useradd --create-home --shell /bin/bash --password '*' "$DEPLOY_USER"
usermod -p '*' "$DEPLOY_USER"

wait_for_apt
apt-get -o DPkg::Lock::Timeout=300 update
# rsync は GitHub Actions がファイルを同期するのに必要（サーバー側にも要る）
apt_install ca-certificates curl git rsync nano

# Docker（公式スクリプト）
if ! command -v docker >/dev/null 2>&1; then
  wait_for_apt
  curl -fsSL https://get.docker.com | sh
fi
usermod -aG docker "$DEPLOY_USER" || true

# gcloud（GCE の Ubuntu イメージには入っている。無ければ apt で）
if ! command -v gcloud >/dev/null 2>&1; then
  apt_install google-cloud-cli || true
fi

# スワップ 2GB（メモリが少ないマシンでも WeasyPrint の PDF 生成が落ちないように）
if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# アプリ配置先
mkdir -p "$APP_DIR/backups"
chown -R "$DEPLOY_USER:$DEPLOY_USER" "$APP_DIR"

# 毎日 3:30 に DB バックアップ（backup.sh は GitHub Actions のデプロイで配置される）
cat > "/etc/cron.d/${APP_NAME}-backup" <<CRON
30 3 * * * $DEPLOY_USER [ -x $APP_DIR/backup.sh ] && $APP_DIR/backup.sh >> $APP_DIR/backups/backup.log 2>&1
CRON
chmod 644 "/etc/cron.d/${APP_NAME}-backup"

# Docker のログが肥大化しないように
mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<'JSON'
{ "log-driver": "json-file", "log-opts": { "max-size": "20m", "max-file": "5" } }
JSON
systemctl restart docker || true

echo "setup-server: done"

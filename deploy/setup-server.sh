#!/bin/bash
# =============================================
# サーバー初期設定（Ubuntu 22.04）
#   Terraform の user_data として起動時に1回実行される。手動で流しても可（冪等）
#   - Docker / Docker Compose plugin
#   - AWS CLI（バックアップの S3 送信用・任意）
#   - /opt/dayservice（compose・Caddyfile・.env を置く場所）
#   - 毎日 3:30 の DB バックアップ cron
# =============================================
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

DEPLOY_USER=ubuntu
APP_DIR=/opt/dayservice

apt-get update
apt-get install -y --no-install-recommends ca-certificates curl git unzip

# Docker（公式スクリプト）
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
usermod -aG docker "$DEPLOY_USER" || true

# AWS CLI v2（S3 バックアップ用。不要なら削っても動作に影響なし）
if ! command -v aws >/dev/null 2>&1; then
  tmp=$(mktemp -d)
  curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "$tmp/awscliv2.zip"
  unzip -q "$tmp/awscliv2.zip" -d "$tmp"
  "$tmp/aws/install" >/dev/null
  rm -rf "$tmp"
fi

# アプリ配置先
mkdir -p "$APP_DIR/backups"
chown -R "$DEPLOY_USER:$DEPLOY_USER" "$APP_DIR"

# 毎日 3:30 に DB バックアップ（backup.sh は GitHub Actions のデプロイで配置される）
cat > /etc/cron.d/dayservice-backup <<'CRON'
30 3 * * * ubuntu [ -x /opt/dayservice/backup.sh ] && /opt/dayservice/backup.sh >> /opt/dayservice/backups/backup.log 2>&1
CRON
chmod 644 /etc/cron.d/dayservice-backup

# Docker のログが肥大化しないように
mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<'JSON'
{ "log-driver": "json-file", "log-opts": { "max-size": "20m", "max-file": "5" } }
JSON
systemctl restart docker || true

echo "setup-server: done"

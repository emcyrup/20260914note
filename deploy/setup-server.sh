#!/bin/bash
# =============================================
# サーバー初期設定（Ubuntu 22.04 / Compute Engine）
#   Terraform がメタデータ user-data に載せ、初回起動時に cloud-init が1回実行する。手動で流しても可（冪等）
#   - deploy ユーザー（メタデータの ssh-keys と同じ名前）
#   - Docker / Docker Compose plugin
#   - スワップ 2GB（e2-micro でも PDF 生成が落ちないように）
#   - /opt/dayservice（compose・Caddyfile・.env を置く場所）
#   - 毎日 3:30 の DB バックアップ cron
# =============================================
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

DEPLOY_USER=deploy
APP_DIR=/opt/dayservice

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

# デプロイ用ユーザー（GCE のゲストエージェントがメタデータの公開鍵をこのユーザーに配る）
id -u "$DEPLOY_USER" >/dev/null 2>&1 || useradd --create-home --shell /bin/bash "$DEPLOY_USER"

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
cat > /etc/cron.d/dayservice-backup <<CRON
30 3 * * * $DEPLOY_USER [ -x $APP_DIR/backup.sh ] && $APP_DIR/backup.sh >> $APP_DIR/backups/backup.log 2>&1
CRON
chmod 644 /etc/cron.d/dayservice-backup

# Docker のログが肥大化しないように
mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<'JSON'
{ "log-driver": "json-file", "log-opts": { "max-size": "20m", "max-file": "5" } }
JSON
systemctl restart docker || true

echo "setup-server: done"

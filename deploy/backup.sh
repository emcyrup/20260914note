#!/bin/bash
# =============================================
# DB バックアップ（pg_dump → gzip → ローカル14日保持 → 任意で Cloud Storage へ）
#   cron から毎日実行。手動: /opt/dayservice/backup.sh
# =============================================
set -euo pipefail
APP_DIR=/opt/dayservice
cd "$APP_DIR"
set -a; . ./.env; set +a

DB_NAME=${DB_NAME:-dayservice}
DB_USER=${DB_USER:-dayservice}
stamp=$(date +%Y%m%d-%H%M%S)
out="$APP_DIR/backups/db-$stamp.sql.gz"

docker compose exec -T db pg_dump -U "$DB_USER" "$DB_NAME" | gzip > "$out"
echo "backup: $out ($(du -h "$out" | cut -f1))"

# ローカルは14日分だけ残す
find "$APP_DIR/backups" -name 'db-*.sql.gz' -mtime +14 -delete

# BACKUP_GCS_BUCKET が設定されていれば Cloud Storage にも送る（バケット側で30日後に自動削除）
# 認証は VM に付いたサービスアカウントが自動で使われる（キーの設定は不要）
if [ -n "${BACKUP_GCS_BUCKET:-}" ] && command -v gcloud >/dev/null 2>&1; then
  gcloud storage cp "$out" "gs://$BACKUP_GCS_BUCKET/db/$(basename "$out")" --quiet
  echo "backup: uploaded to gs://$BACKUP_GCS_BUCKET/db/"
fi

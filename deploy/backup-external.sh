#!/bin/bash
# =============================================
# DB バックアップ（外部 PostgreSQL 版）：ホストの pg_dump → gzip → ローカル14日保持
#   cron 例: 30 3 * * * /opt/michinotedemo/backup-external.sh >> /opt/michinotedemo/backups/backup.log 2>&1
#   写真（media/）は別途 rsync などで保管する
# =============================================
set -euo pipefail
APP_DIR=$(cd "$(dirname "$0")" && pwd)
cd "$APP_DIR"
set -a; . ./.env; set +a

DB_NAME=${DB_NAME:-michinotedemo}
DB_USER=${DB_USER:-st_ai_labo_dbuser}
PG_HOST=${BACKUP_DB_HOST:-127.0.0.1}
PG_PORT=${DB_PORT:-5432}
mkdir -p backups
stamp=$(date +%Y%m%d-%H%M%S)
out="$APP_DIR/backups/db-$stamp.sql.gz"

PGPASSWORD="$DB_PASSWORD" pg_dump -h "$PG_HOST" -p "$PG_PORT" -U "$DB_USER" "$DB_NAME" | gzip > "$out"
echo "backup: $out ($(du -h "$out" | cut -f1))"

find "$APP_DIR/backups" -name 'db-*.sql.gz' -mtime +14 -delete

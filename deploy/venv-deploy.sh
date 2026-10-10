#!/bin/bash
# =============================================
# 共用サーバー（sudo なし・Docker なし・venv＋Gunicorn）へのデプロイ／起動スクリプト
#   例: AWS 開発環境 https://st-michinotedemo.ai-labo.cloud/ → Gunicorn 0.0.0.0:8029（~/michinotedemo）
#       ゆあーず       https://michinote.yours.ai-labo.cloud/  → Gunicorn 0.0.0.0:8030（~/michinoteyours）
#       オウル         https://michinote.owl.ai-labo.cloud/    → Gunicorn 0.0.0.0:8034（~/michinoteowl）
#
# 使い方（サーバー上で）
#   bash ~/michinotedemo/deploy/venv-deploy.sh            # 現在のブランチの最新に更新して再起動
#   bash ~/michinotedemo/deploy/venv-deploy.sh <コミット>   # 指定コミットに切り替えて再起動（GitHub Actions が使う）
#   bash ~/michinotedemo/deploy/venv-deploy.sh --restart   # コードは変えずに再起動（.env を変えたとき）
#   bash ~/michinotedemo/deploy/venv-deploy.sh --stop      # 停止
#   bash ~/michinotedemo/deploy/venv-deploy.sh --ensure    # 止まっていれば起動する（動いていれば何もしない。cron から呼ぶ）
#   bash ~/michinotedemo/deploy/venv-deploy.sh --status    # 動いているか・いまのコミット・最後の配備
#   bash ~/michinotedemo/deploy/venv-deploy.sh --backup [run|list|check]
#        DB のバックアップ（PostgreSQL は pg_dump、無ければ manage.py dumpdata）を backups/ に取り、14 日ぶん残す。
#        --autostart install で毎日 3:15 の行も crontab に入る。list は一覧、check は最後のバックアップが 2 日以内か
#   bash ~/michinotedemo/deploy/venv-deploy.sh --autostart check|install|remove
#        サーバーの再起動のあとも自動で上がるよう、crontab に @reboot と 5 分ごとの --ensure を入れる（この clone の行だけを触る）
#
# 前提
#   ~/env にプロバイダ作成の venv、~/michinotedemo（または ~/michinoteyours）に git clone、その中の .env に設定
#   環境変数で変更可: APP_DIR（既定 このスクリプトがある clone）、VENV（既定 ~/env）、PORT（既定 .env の PORT か 8029）
#   同じサーバーで開発環境とゆあーずを両方動かすときは、clone・.env・PORT が別で、venv（~/env）は共用でよい
# =============================================
set -euo pipefail

# 既定の APP_DIR はこのスクリプトが入っている clone（~/michinotedemo/deploy/venv-deploy.sh なら ~/michinotedemo）
APP_DIR=${APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
VENV=${VENV:-$HOME/env}
cd "$APP_DIR"

# .env から PORT / GUNICORN_WORKERS を拾う（他の値はアプリが自分で読む）
if [ -f .env ]; then
  PORT=${PORT:-$(grep -E '^PORT=' .env | cut -d= -f2- | tr -d '[:space:]' || true)}
  WORKERS=${WORKERS:-$(grep -E '^GUNICORN_WORKERS=' .env | cut -d= -f2- | tr -d '[:space:]' || true)}
fi
PORT=${PORT:-8029}
WORKERS=${WORKERS:-2}
PIDFILE=$APP_DIR/run/gunicorn.pid
mkdir -p run logs media

# venv が無ければ作る（プロバイダ作成の ~/env が無い場合の保険）。Django 6.1 は Python 3.12 以上
if [ ! -f "$VENV/bin/activate" ]; then
  echo "venv: $VENV が無いので python3 で作成します（$(python3 --version 2>&1)）"
  python3 -m venv "$VENV" || { echo "ERROR: venv を作成できません。プロバイダに python3-venv（3.12 以上）を依頼してください"; exit 1; }
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
pyver=$(python -c 'import sys; print("%d.%d" % sys.version_info[:2])')
echo "python: $(python --version 2>&1) ($VENV)"
python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' || {
  echo "ERROR: Python $pyver は古すぎます（Django 6.1 は 3.12 以上）。プロバイダに Python 3.12 以上の venv を依頼してください"; exit 1; }

running_pid() {
  local pid
  pid=$(cat "$PIDFILE" 2>/dev/null || true)
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then echo "$pid"; fi
}

stop() {
  local pid
  pid=$(running_pid)
  if [ -n "$pid" ]; then
    kill -TERM "$pid"
    for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
    echo "gunicorn: stopped (pid $pid)"
  fi
  rm -f "$PIDFILE"
}

start() {
  # --daemon は使わず setsid + nohup でバックグラウンド化する（SSH を切っても残る。前面起動と同じ動きになる）
  # 制御ソケットは /run/user/<uid> に作られてログアウトで消えることがあるので使わない
  setsid nohup gunicorn config.wsgi:application \
    --bind "0.0.0.0:$PORT" \
    --workers "$WORKERS" --threads 2 --timeout 300 \
    --pid "$PIDFILE" --no-control-socket \
    --access-logfile logs/access.log --error-logfile logs/error.log --capture-output \
    >> logs/gunicorn.out 2>&1 < /dev/null &
  for _ in $(seq 1 20); do [ -n "$(running_pid)" ] && break; sleep 0.5; done
  if [ -n "$(running_pid)" ]; then
    echo "gunicorn: started on 0.0.0.0:$PORT (pid $(running_pid))"
  else
    echo "ERROR: gunicorn が起動しませんでした"; tail -n 30 logs/gunicorn.out logs/error.log 2>/dev/null; exit 1
  fi
}

restart() {
  local pid
  pid=$(running_pid)
  if [ -n "$pid" ]; then
    # HUP でワーカーだけ入れ替える（新しいコードと .env を読み直す。ポートは開いたまま）
    kill -HUP "$pid"
    echo "gunicorn: reloaded (pid $pid)"
  else
    start
  fi
}

status() {
  local pid
  pid=$(running_pid)
  if [ -n "$pid" ]; then
    echo "gunicorn: running (pid $pid, since $(ps -o lstart= -p "$pid" 2>/dev/null | xargs))"
  else
    echo "gunicorn: NOT running"
  fi
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:$PORT/healthz/" || true)
  echo "healthz (127.0.0.1:$PORT): $code"
  echo "code: $(git rev-parse --short HEAD 2>/dev/null) $(git log -1 --format='%cd %s' --date=format:'%Y-%m-%d %H:%M' 2>/dev/null | cut -c1-80)"
  [ -f logs/deploy.log ] && { echo "最近の配備:"; tail -n 5 logs/deploy.log; }
  return 0
}

ensure() {
  # 止まっていれば起動する（サーバーの再起動・落ちたとき用。cron から 5 分ごとに呼ぶ）。動いていれば何も出さない
  if [ -z "$(running_pid)" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') gunicorn が止まっていたので起動します"
    start
  fi
}

BACKUP_KEEP_DAYS=${BACKUP_KEEP_DAYS:-14}
backup() {
  # DB のバックアップ。PostgreSQL（.env の DB_NAME あり）は pg_dump、無いか sqlite なら manage.py dumpdata。
  # ファイル名に日時。$BACKUP_KEEP_DAYS 日より古いものは消す。秘密の値（パスワード）は出さない
  local mode=${1:-run} stamp out dbname dbuser dbpass dbhost dbport
  # 中身は全利用者の記録と職員のパスワードのハッシュ。ほかのアカウントから読めないようにする
  umask 077
  mkdir -p backups
  chmod 700 backups
  case "$mode" in
    list)
      echo "backups/（$APP_DIR）:"; ls -lh backups 2>/dev/null | tail -n +2 | awk '{print "  " $6 " " $7 " " $8 "  " $5 "  " $9}' || true
      [ -n "$(ls -A backups 2>/dev/null)" ] || echo "  （まだありません）"; return 0 ;;
    check)
      local last
      last=$(ls -t backups 2>/dev/null | grep -v '\.tmp$' | head -n 1 || true)
      if [ -z "$last" ]; then echo "backup: NONE（まだ一度も取っていません）"; return 1; fi
      if [ -n "$(find backups -maxdepth 1 -name "$last" -mtime -2)" ]; then echo "backup: OK 最後は $last"; return 0; fi
      echo "backup: OLD 最後は $last（2 日より前）"; return 1 ;;
    run) ;;
    *) echo "使い方: --backup run|list|check"; exit 1 ;;
  esac
  stamp=$(date +%Y%m%d-%H%M)
  dbname=$(grep -E '^DB_NAME=' .env 2>/dev/null | cut -d= -f2- | tr -d '[:space:]"' || true)
  if [ -n "$dbname" ] && command -v pg_dump >/dev/null 2>&1; then
    dbuser=$(grep -E '^DB_USER=' .env | cut -d= -f2- | tr -d '[:space:]"' || true)
    dbpass=$(grep -E '^DB_PASSWORD=' .env | cut -d= -f2- | sed 's/^"//;s/"$//' || true)
    dbhost=$(grep -E '^DB_HOST=' .env | cut -d= -f2- | tr -d '[:space:]"' || true)
    dbport=$(grep -E '^DB_PORT=' .env | cut -d= -f2- | tr -d '[:space:]"' || true)
    out="backups/db-$stamp.sql.gz"
    # 途中で失敗したときに半端なファイルを残さない（check が OK と言わないように、書き終えてから名前を付ける）
    if ! PGPASSWORD="$dbpass" pg_dump -h "${dbhost:-localhost}" -p "${dbport:-5432}" -U "$dbuser" "$dbname" | gzip > "$out.tmp"; then
      rm -f "$out.tmp"; echo "backup: FAILED（pg_dump）"; return 1
    fi
  else
    out="backups/data-$stamp.json.gz"
    if ! "$VENV/bin/python" manage.py dumpdata --natural-foreign --natural-primary -e contenttypes -e auth.permission -e sessions | gzip > "$out.tmp"; then
      rm -f "$out.tmp"; echo "backup: FAILED（dumpdata）"; return 1
    fi
  fi
  mv "$out.tmp" "$out"
  echo "$(date '+%Y-%m-%d %H:%M:%S') backup: $out ($(du -h "$out" | cut -f1))"
  find backups -maxdepth 1 \( -name 'db-*.sql.gz' -o -name 'data-*.json.gz' \) -mtime +"$BACKUP_KEEP_DAYS" -delete
}

# crontab の行（この clone の行には印を付けて、ほかの行は触らない）
CRON_TAG="# michinote-autostart:$APP_DIR"
cron_lines() {
  local self="$APP_DIR/deploy/venv-deploy.sh" envs="APP_DIR=$APP_DIR VENV=$VENV"
  echo "@reboot sleep 30 && $envs bash $self --ensure >> $APP_DIR/logs/boot.log 2>&1 $CRON_TAG"
  echo "*/5 * * * * $envs bash $self --ensure >> $APP_DIR/logs/ensure.log 2>&1 $CRON_TAG"
  echo "15 3 * * * $envs bash $self --backup >> $APP_DIR/logs/backup.log 2>&1 $CRON_TAG"
}
others() {
  # この clone の印の付いた行を除いた、ほかの行（空行も除く）
  printf '%s\n' "$1" | { grep -vF "$CRON_TAG" || true; } | sed '/^$/d'
}
autostart() {
  command -v crontab >/dev/null 2>&1 || { echo "ERROR: crontab コマンドがありません（cron が使えないサーバー）。プロバイダに自動起動の方法を相談してください"; exit 1; }
  local current
  current=$(crontab -l 2>/dev/null || true)
  case "${1:-check}" in
    check)
      echo "この clone（$APP_DIR）の自動起動の行:"
      printf '%s\n' "$current" | grep -F "$CRON_TAG" || echo "  （まだありません。--autostart install で入れます）"
      # 以前の手順書の手書きの @reboot 行（印なし）があれば知らせる
      printf '%s\n' "$current" | grep -F "$APP_DIR/deploy/venv-deploy.sh" | grep -vF "$CRON_TAG" | sed 's/^/  手書きの行: /' || true ;;
    install)
      { others "$current" | { grep -vF "$APP_DIR/deploy/venv-deploy.sh" || true; }; cron_lines; } | crontab -
      echo "crontab に入れました（この clone の行だけ。ほかの行はそのまま）:"; crontab -l | grep -F "$CRON_TAG" ;;
    remove)
      others "$current" | crontab -
      echo "この clone の自動起動の行を消しました" ;;
    *) echo "使い方: --autostart check|install|remove"; exit 1 ;;
  esac
}

case "${1:-}" in
  --stop)      stop; exit 0 ;;
  --restart)   restart; exit 0 ;;
  --ensure)    ensure; exit 0 ;;
  --status)    status; exit 0 ;;
  --autostart) autostart "${2:-check}"; exit 0 ;;
  --backup)    backup "${2:-run}"; exit $? ;;
esac

test -f .env || { echo "ERROR: $APP_DIR/.env がありません。deploy/.env.dev-aws.example（ゆあーずは deploy/.env.yours-aws.example、オウルは deploy/.env.owl-aws.example）を元に作成してください"; exit 1; }

# 1) コード更新
git fetch --quiet origin
if [ -n "${1:-}" ]; then
  git checkout --quiet --force --detach "$1"
else
  branch=$(git rev-parse --abbrev-ref HEAD)
  if [ "$branch" = "HEAD" ]; then branch=develop; git checkout --quiet "$branch"; fi
  git pull --quiet --ff-only origin "$branch"
fi
echo "code: $(git rev-parse --short HEAD) $(git log -1 --pretty=%s | cut -c1-60)"
echo "$(date '+%Y-%m-%d %H:%M:%S') $(git rev-parse --short HEAD) ${1:-pull}" >> logs/deploy.log

# 2) 依存・DB・静的ファイル
pip install --quiet --upgrade pip wheel >/dev/null 2>&1 || true
pip install --quiet -r requirements.txt
# .env に WHISPER_MODEL があれば、サーバー内の文字起こし（faster-whisper）も入れる
if grep -qE '^WHISPER_MODEL=.+' .env; then pip install --quiet -r requirements-speech.txt; fi
# 切り戻し（前のコミットへ戻す）のとき：DB にはあるのにこのコードに無い migration があれば知らせる
# （その場合は、新しいコードのうちに `python manage.py migrate <アプリ> <番号>` で DB を戻してから切り戻す。docs/OPERATIONS.md）
python manage.py shell -v 0 -c "
from django.db import connection
from django.db.migrations.loader import MigrationLoader
loader = MigrationLoader(connection)
extra = sorted(set(loader.applied_migrations) - set(loader.disk_migrations))
if extra:
    print('WARNING: DB に適用ずみで、このコードに無い migration があります（新しいコードの変更が DB に残っています）:')
    [print('  ', app, name) for app, name in extra]
" || true
python manage.py migrate --noinput
python manage.py collectstatic --noinput --clear >/dev/null
python manage.py check --deploy --fail-level ERROR

# 3) 再起動と確認
restart
for _ in $(seq 1 20); do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:$PORT/healthz/" || true)
  [ "$code" = "200" ] && { echo "healthz: ok"; exit 0; }
  sleep 1
done
echo "ERROR: healthz が応答しません。logs/error.log を確認してください"; tail -n 30 logs/gunicorn.out logs/error.log 2>/dev/null; exit 1

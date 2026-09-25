#!/bin/bash
# =============================================
# 共用サーバー（sudo なし・Docker なし・venv＋Gunicorn）へのデプロイ／起動スクリプト
#   例: AWS 開発環境 https://st-michinotedemo.ai-labo.cloud/ → Gunicorn 0.0.0.0:8029（~/michinotedemo）
#       ゆあーず       https://michinote.yours.ai-labo.cloud/  → Gunicorn 0.0.0.0:8030（~/michinoteyours）
#
# 使い方（サーバー上で）
#   bash ~/michinotedemo/deploy/venv-deploy.sh            # 現在のブランチの最新に更新して再起動
#   bash ~/michinotedemo/deploy/venv-deploy.sh <コミット>   # 指定コミットに切り替えて再起動（GitHub Actions が使う）
#   bash ~/michinotedemo/deploy/venv-deploy.sh --restart   # コードは変えずに再起動（.env を変えたとき）
#   bash ~/michinotedemo/deploy/venv-deploy.sh --stop      # 停止
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

case "${1:-}" in
  --stop)    stop; exit 0 ;;
  --restart) restart; exit 0 ;;
esac

test -f .env || { echo "ERROR: $APP_DIR/.env がありません。deploy/.env.dev-aws.example（ゆあーずは deploy/.env.yours-aws.example）を元に作成してください"; exit 1; }

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

# 2) 依存・DB・静的ファイル
pip install --quiet --upgrade pip wheel >/dev/null 2>&1 || true
pip install --quiet -r requirements.txt
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

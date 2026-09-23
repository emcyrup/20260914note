#!/usr/bin/env bash
# =============================================
# ドメインで HTTPS にする（GCP の別サーバー：/opt/ryoiku・/opt/simple など）
#
#   ./enable-https.sh check <ドメイン>   … 事前確認だけ（DNS が固定 IP を向いているか・443 が開いているか）
#   ./enable-https.sh <ドメイン>         … .env を書き換えて Caddy に証明書を取らせ、https で開けるまで確かめる
#   ./enable-https.sh off                … IP（http://<固定IP>/）で動かす状態に戻す
#
# deploy ユーザーで、アプリの置き場所（この sh がある場所）で動かす。1行で打つなら：
#   sudo -u deploy bash -lc "cd /opt/ryoiku && ./enable-https.sh yours.example.jp"
#
# 書き換える .env の項目：DOMAIN・DJANGO_ALLOWED_HOSTS・CSRF_TRUSTED_ORIGINS・SECURE_SSL_REDIRECT・
#   RESERVATION_SITE_URL・CADDYFILE・LEGACY_HOST（前の固定 IP。http://<IP>/… をドメインへ転送する）
# 書き換える前の .env は .env.bak-<日時> に残す。秘密の値（SECRET_KEY など）は触らない。
# =============================================
set -euo pipefail
cd "$(dirname "$0")"

say()  { printf '%s\n' "$*"; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[ -f .env ] || fail ".env がありません（$(pwd)）。アプリの置き場所で動かしてください"
[ -f Caddyfile.https ] || fail "Caddyfile.https がありません。GitHub Actions で一度配備してから動かしてください"

get_env() { grep -E "^$1=" .env | tail -n1 | cut -d= -f2- || true; }

set_env() {
  local key=$1 value=$2
  if grep -qE "^$key=" .env; then
    # 区切りに使わない文字（\x01）で置き換える（値に / や | や & が入っても壊れないように）
    local esc=${value//\\/\\\\}; esc=${esc//&/\\&}
    sed -i "s$(printf '\001')^$key=.*$(printf '\001')$key=$esc$(printf '\001')" .env
  else
    printf '%s=%s\n' "$key" "$value" >> .env
  fi
}

public_ip() {
  # GCE のメタデータ → 無ければ .env の LEGACY_HOST・DJANGO_ALLOWED_HOSTS の中の IP
  local ip
  ip=$(curl -s --max-time 3 -H 'Metadata-Flavor: Google' \
    'http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip' || true)
  if [[ ! $ip =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    ip=$(get_env LEGACY_HOST)
  fi
  if [[ ! $ip =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    ip=$(get_env DJANGO_ALLOWED_HOSTS | tr ',' '\n' | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | head -n1 || true)
  fi
  printf '%s' "$ip"
}

valid_domain() {
  [[ $1 =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$ ]]
}

check() {
  local domain=$1 ip resolved ok=0
  ip=$(public_ip)
  [ -n "$ip" ] || fail "このサーバーの固定 IP が分かりません"
  say "このサーバーの固定 IP : $ip"
  resolved=$(getent ahostsv4 "$domain" | awk '{print $1}' | sort -u | tr '\n' ' ' || true)
  say "$domain の DNS        : ${resolved:-（見つからない）}"
  if [ -z "$resolved" ]; then
    say "  → DNS に A レコードがまだありません（または反映待ち）。ドメインの管理画面で A レコード $domain → $ip を作ってください"
    ok=1
  elif [ "$resolved" != "$ip " ]; then
    say "  → $domain は $ip を向いていません。A レコードを $ip だけにしてください（Cloudflare ならプロキシをオフ）"
    ok=1
  else
    say "  → OK"
  fi
  # 443 番に外から届くか（自分自身の外向き IP 宛て。Caddy が 443 を開いていれば接続できる）
  if timeout 5 bash -c "exec 3<>/dev/tcp/$ip/443" 2>/dev/null; then
    say "443 番（HTTPS）        : 接続できる"
  else
    say "443 番（HTTPS）        : 接続できない → VM の編集で「HTTPS トラフィックを許可する」をオン（ファイアウォール default-allow-https）"
    ok=1
  fi
  return $ok
}

wait_https() {
  local domain=$1 code=""
  say "証明書の取得を待っています（最大2分）…"
  for _ in $(seq 1 24); do
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "https://$domain/healthz/" || true)
    if [ "$code" = "200" ]; then
      say "OK: https://$domain/ で開けます（healthz 200）"
      return 0
    fi
    sleep 5
  done
  say "まだ https で開けません（最後の応答: ${code:-なし}）。Caddy のログ："
  docker compose logs --tail=25 caddy || true
  return 1
}

cmd=${1:-}
case "$cmd" in
  ""|-h|--help)
    sed -n '2,15p' "$0"; exit 0 ;;

  check)
    domain=$(printf '%s' "${2:-}" | tr 'A-Z' 'a-z')
    valid_domain "$domain" || fail "ドメインを入れてください（例：./enable-https.sh check yours.example.jp）"
    if check "$domain"; then say "準備 OK です。./enable-https.sh $domain で切り替えられます"; else exit 1; fi ;;

  off)
    ip=$(public_ip)
    [ -n "$ip" ] || fail "このサーバーの固定 IP が分かりません"
    cp -p .env ".env.bak-$(date +%Y%m%d-%H%M%S)-$$"
    set_env DOMAIN ""
    set_env CADDYFILE "Caddyfile"
    set_env SECURE_SSL_REDIRECT "False"
    set_env DJANGO_ALLOWED_HOSTS "$ip"
    set_env CSRF_TRUSTED_ORIGINS "http://$ip"
    set_env RESERVATION_SITE_URL ""
    docker compose up -d
    say "IP で動かす状態に戻しました：http://$ip/" ;;

  *)
    domain=$(printf '%s' "$cmd" | tr 'A-Z' 'a-z')
    valid_domain "$domain" || fail "ドメインの形が正しくありません: $cmd（例：yours.example.jp。http:// や / は付けない）"
    check "$domain" || fail "上の点を直してから、もう一度動かしてください（.env は変えていません）"
    ip=$(public_ip)
    backup=".env.bak-$(date +%Y%m%d-%H%M%S)-$$"
    cp -p .env "$backup"
    set_env DOMAIN "$domain"
    set_env CADDYFILE "Caddyfile.https"
    set_env LEGACY_HOST "$ip"
    set_env SECURE_SSL_REDIRECT "True"
    set_env DJANGO_ALLOWED_HOSTS "$domain,$ip"
    set_env CSRF_TRUSTED_ORIGINS "https://$domain"
    set_env RESERVATION_SITE_URL "https://$domain"
    say "書き換えた .env（前のものは $backup）："
    grep -E '^(DOMAIN|CADDYFILE|LEGACY_HOST|SECURE_SSL_REDIRECT|DJANGO_ALLOWED_HOSTS|CSRF_TRUSTED_ORIGINS|RESERVATION_SITE_URL)=' .env
    docker compose up -d
    if wait_https "$domain"; then
      say "http://$ip/… で開いた人は https://$domain/… へ転送されます。"
      say "残りの作業：GitHub Secrets の *_HEALTH_URL を https://$domain/healthz/ に（docs 参照）"
    else
      say "戻すときは ./enable-https.sh off（または cp $backup .env && docker compose up -d）"
      exit 1
    fi ;;
esac

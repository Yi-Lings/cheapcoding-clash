#!/usr/bin/env bash
set -euo pipefail
umask 077

if (( EUID != 0 || $# != 1 )); then
  printf 'Usage: sudo bash deploy/enable-https.sh ACME_EMAIL\n' >&2
  exit 2
fi
email=$1
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
conf=/etc/cheapcoding-clash
site=/etc/nginx/sites-available/clash-subscription
limits=/etc/nginx/conf.d/clash-platform-limits.conf
host=$(sed -n 's/^CCVPN_HOST=//p' "$conf/portal.env")
stage=$(mktemp -d)
trap 'rm -rf -- "$stage"' EXIT
test -f "$site"
python3 - "$host" "$email" <<'PYVALIDATE'
import re
import sys

host, email = sys.argv[1:]
if not re.fullmatch(r'(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', host):
    sys.exit('Invalid DNS hostname')
if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
    sys.exit('Invalid ACME email')
PYVALIDATE


certbot certonly --webroot -w /var/www/clash-acme --cert-name "$host" -d "$host" --email "$email" --agree-tos --non-interactive
cert_path="/etc/letsencrypt/live/$host/fullchain.pem"
key_path="/etc/letsencrypt/live/$host/privkey.pem"
sed -e "s|@@HOST@@|$host|g" -e "s|@@CERT@@|$cert_path|g" -e "s|@@KEY@@|$key_path|g" \
  "$repo/deploy/nginx/site.conf.in" > "$stage/https.conf"
install -m 0644 "$repo/deploy/nginx/limits.conf" "$limits"
cp -a "$site" "$stage/previous-site.conf"
install -m 0644 "$stage/https.conf" "$site"
if ! nginx -t; then
  install -m 0644 "$stage/previous-site.conf" "$site"
  exit 1
fi

install -d -m 0755 /etc/letsencrypt/renewal-hooks/deploy
install -m 0755 "$repo/deploy/reload-nginx" /etc/letsencrypt/renewal-hooks/deploy/cheapcoding-clash-nginx

systemctl enable --now clash-panel clash-portal clash-traffic.timer
systemctl reload nginx
for attempt in {1..20}; do
  if curl -fsS --noproxy '*' --max-time 2 --resolve "$host:443:127.0.0.1" "https://$host/login" >/dev/null &&
     ss -ltnH 'sport = :17763' | grep -F '127.0.0.1:17763' >/dev/null; then
    systemctl start clash-traffic.service
    printf 'Ready: https://%s/\nAdmin credentials: sudo cat %s/admin-credentials\n' "$host" "$conf"
    exit 0
  fi
  sleep 1
done
printf 'HTTPS login or the local Xray listener did not become ready.\n' >&2
exit 1

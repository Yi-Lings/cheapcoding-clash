#!/usr/bin/env bash
set -euo pipefail
umask 077

usage() {
  cat <<'EOF'
Usage: sudo bash deploy/install.sh HOST ACME_EMAIL [DIRECT_DOMAIN] [ORIGIN_IP]
HOST must already resolve to the new server. Supports Debian/Ubuntu with systemd.
DIRECT_DOMAIN defaults to HOST. ORIGIN_IP enables same-host HTTPS fallback in Xray.
EOF
}
if [[ ${1:-} == --help ]]; then usage; exit 0; fi
if (( EUID != 0 || $# < 2 || $# > 4 )); then usage >&2; exit 2; fi
host=$1
email=$2
direct=${3:-$host}
origin=${4:-}
command -v python3 >/dev/null || { printf 'Install python3 first.\n' >&2; exit 1; }
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
app=/opt/clash-subscription
panel=/var/lib/clash-panel
portal=/var/lib/clash-portal
conf=/etc/cheapcoding-clash
site=/etc/nginx/sites-available/clash-subscription
limits=/etc/nginx/conf.d/clash-platform-limits.conf
version=v3.11.0

python3 - "$host" "$direct" "$email" "$origin" <<'PY'
import ipaddress, re, sys
host, direct, email, origin = sys.argv[1:]
for name in (host, direct):
    if not re.fullmatch(r'(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', name):
        sys.exit('Invalid DNS hostname')
if host != direct and not host.endswith('.' + direct):
    sys.exit('DIRECT_DOMAIN must contain HOST')
if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
    sys.exit('Invalid ACME email')
if origin and not ipaddress.ip_address(origin).is_global:
    sys.exit('ORIGIN_IP must be public')
PY

for path in "$app" "$panel" "$portal" "$conf" "$site" "$limits" /etc/nginx/sites-enabled/clash-subscription \
  /etc/systemd/system/clash-panel.service /etc/systemd/system/clash-portal.service \
  /etc/systemd/system/clash-traffic.service /etc/systemd/system/clash-traffic.timer \
  /etc/letsencrypt/renewal-hooks/deploy/cheapcoding-clash-nginx; do
  if [[ -e "$path" || -L "$path" ]]; then printf 'Existing installation path: %s\n' "$path" >&2; exit 1; fi
done
if [[ $(ps -p 1 -o comm=) != systemd ]]; then
  printf 'This installer requires systemd.\n' >&2
  exit 1
fi
case $(uname -m) in
  x86_64) arch=amd64; sha=0ddfa4702f595442e2962acd81addc768fdacaa4ff2672c971a0463dae744916 ;;
  aarch64) arch=arm64; sha=f50b4c30fd919d018f2f4c3d8db53a4ca027503d581328727770afb116a79d90 ;;
  *) printf 'Unsupported architecture.\n' >&2; exit 1 ;;
esac

stage=$(mktemp -d)
cleanup() {
  local status=$?
  rm -rf -- "$stage"
  if (( status != 0 )); then
    printf 'Installation did not finish. See README recovery steps. Any existing site is left in place.\n' >&2
  fi
}
trap cleanup EXIT
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates certbot curl iproute2 nginx openssl python3 tzdata
ports='17761|17763|17764|62789|11111'
if ss -ltnH | awk -v p=":($ports)$" '$4 ~ p {found=1} END {exit !found}'; then
  printf 'A local service port required by this stack is already in use.\n' >&2
  exit 1
fi
asset="x-ui-linux-$arch.tar.gz"
curl -fsSL --retry 3 --output "$stage/$asset" "https://github.com/MHSanaei/3x-ui/releases/download/$version/$asset"
printf '%s  %s\n' "$sha" "$stage/$asset" | sha256sum -c -
tar --no-same-owner -xzf "$stage/$asset" -C "$stage"
test -x "$stage/x-ui/x-ui"
test "$("$stage/x-ui/x-ui" -v)" = "${version#v}"
id clashsvc >/dev/null 2>&1 || useradd --system --user-group --no-create-home --shell /usr/sbin/nologin clashsvc
id clashportal >/dev/null 2>&1 || useradd --system --user-group --no-create-home --shell /usr/sbin/nologin clashportal
install -d -m 0755 "$app"
mv "$stage/x-ui" "$app/"
chmod -R a+rX "$app/x-ui"
install -m 0644 "$repo"/portal/{portal.py,portal_traffic.py,cheapcoding-vpn.webp,cheapcoding-starry-sea.webp} "$app/"
install -d -m 0755 "$app/static"
install -m 0644 "$repo"/portal/static/{portal.css,portal.js,admin-live.js} "$app/static/"
install -d -m 0700 "$conf"
install -d -m 0700 -o clashsvc -g clashsvc "$panel" "$panel"/{db,bin,log}
install -d -m 0700 -o clashportal -g clashportal "$portal"
bootstrap_args=(--host "$host" --direct-domain "$direct" --panel-root "$panel" --binary "$app/x-ui/x-ui" --secrets-dir "$stage/secrets")
if [[ -n "$origin" ]]; then bootstrap_args+=(--origin-ip "$origin"); fi
python3 "$repo/deploy/bootstrap.py" "${bootstrap_args[@]}"
install -m 0600 "$stage/secrets/portal.env" "$stage/secrets/admin-credentials" "$conf/"
install -m 0600 -o clashportal -g clashportal "$stage/secrets/panel-token" "$portal/"
chown -R clashsvc:clashsvc "$panel"
for f in "$app"/x-ui/bin/*; do ln -s -- "$f" "$panel/bin/$(basename -- "$f")"; done
install -m 0644 "$repo"/deploy/systemd/* /etc/systemd/system/
systemctl daemon-reload

# HTTP-only bootstrap vhost for Certbot webroot validation.
install -d -m 0755 /var/www/clash-acme
cat > "$stage/http.conf" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $host;
    location ^~ /.well-known/acme-challenge/ {
        root /var/www/clash-acme;
        try_files \$uri =404;
    }
    location / { return 404; }
}
EOF
install -m 0644 "$stage/http.conf" "$site"
ln -s "$site" /etc/nginx/sites-enabled/clash-subscription
nginx -t
systemctl enable --now nginx
systemctl reload nginx
bash "$repo/deploy/enable-https.sh" "$email"

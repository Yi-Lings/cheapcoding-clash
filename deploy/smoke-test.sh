#!/usr/bin/env bash
# Run a fresh 3x-ui/Xray and portal entirely inside a private network namespace.
set -euo pipefail
umask 077
if (( EUID != 0 || $# != 1 )); then
  printf 'Usage: sudo bash deploy/smoke-test.sh /absolute/path/to/x-ui-release-directory\n' >&2
  exit 2
fi
release=$(realpath "$1")
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
test "$("$release/x-ui" -v)" = 3.11.0
unshare --net -- bash -s -- "$repo" "$release" <<'TEST'
set -euo pipefail
repo=$1
release=$2
testdir=$(mktemp -d)
panel_pid=''
portal_pid=''
nginx_pid=''
cleanup() {
  status=$?
  for pid in "$nginx_pid" "$portal_pid" "$panel_pid"; do
    if [[ -n "$pid" ]]; then kill "$pid" 2>/dev/null || true; wait "$pid" 2>/dev/null || true; fi
  done
  if (( status != 0 )); then printf 'Isolated smoke test failed.\n' >&2; fi
  rm -rf -- "$testdir"
}
trap cleanup EXIT
ip link set lo up
python3 "$repo/deploy/bootstrap.py" --host vpn.example.com --direct-domain example.com \
  --panel-root "$testdir/panel" --binary "$release/x-ui" --secrets-dir "$testdir/secrets"
for path in "$release"/bin/*; do ln -s "$path" "$testdir/panel/bin/$(basename "$path")"; done
export XUI_DB_FOLDER="$testdir/panel/db"
export XUI_BIN_FOLDER="$testdir/panel/bin"
export XUI_LOG_FOLDER="$testdir/panel/log"
(cd "$testdir/panel" && exec "$release/x-ui") >"$testdir/panel.log" 2>&1 &
panel_pid=$!
set -a
source "$testdir/secrets/portal.env"
set +a
export CCVPN_STATE="$testdir/portal" PYTHONPATH="$repo/portal"
install -d -m 0700 "$CCVPN_STATE"
install -m 0600 "$testdir/secrets/panel-token" "$CCVPN_STATE/"
for attempt in {1..30}; do
  if curl -fsS --noproxy '*' http://127.0.0.1:17761/admin/csrf-token >/dev/null 2>&1; then break; fi
  sleep 0.3
done
python3 - "$testdir/secrets/admin-credentials" <<'PY'
from pathlib import Path
import sys
import time
import portal

portal.init()
portal.portal_traffic.initialize(portal.STATE)
assert portal.api('clients/list') in ([], None)
password = Path(sys.argv[1]).read_text().split('password=', 1)[1].strip()
cookie, two_factor = portal.panel_login(password, '', '127.0.0.1')
assert cookie.startswith('3x-ui=') and not two_factor
assert portal.is_panel_admin(cookie.split(';',1)[0])
account = portal.register('smoke-user', int(time.time() * 1000) + 86400000)
assert account['panel_id'] >= 1 and portal.client('smoke-user')['email'] == 'smoke-user'
portal.register('smoke-other', int(time.time() * 1000) + 86400000)
assert 'vpn.example.com' in portal.subscription(account)
assert 'DOMAIN-SUFFIX,example.com,DIRECT' in portal.subscription(account)
PY
python3 "$repo/portal/portal.py" collect-traffic
python3 "$repo/portal/portal.py" serve >"$testdir/portal.log" 2>&1 &
portal_pid=$!
for attempt in {1..30}; do
  if curl -fsS --noproxy '*' http://127.0.0.1:17764/ >/dev/null 2>&1; then break; fi
  sleep 0.3
done
python3 - "$testdir/secrets/admin-credentials" <<'PY'
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import json
import re
import sys
import portal

base = 'http://127.0.0.1:17764'
with portal.database() as db:
    account = db.execute("select * from accounts where name='smoke-user'").fetchone()
for path, marker in (
    ('/', b'href="/login"'),
    ('/login', b'action="/login"'),
    ('/account/' + account['access_key'], b'autocomplete="new-password"'),
    ('/sub/' + account['subscription'], b'path: /connect'),
    ('/hiddify-routing.json', b'.example.com'),
):
    with urlopen(base + path, timeout=6) as response:
        assert response.status == 200 and marker in response.read(), path.split('/')[1]
password = Path(sys.argv[1]).read_text().split('password=', 1)[1].strip()
cookie, _ = portal.panel_login(password, '', '127.0.0.1')
headers = {'Cookie': cookie.split(';', 1)[0]}
admin = base + '/admin/operations/'
filter_query = '?filter_client=' + str(account['panel_id'])
with urlopen(Request(admin + filter_query, headers=headers), timeout=6) as response:
    page = response.read().decode()
    assert '<dialog class="portal-drawer"' in page
    assert 'href="#main-content"' in page
    assert '<style>' not in page
    for asset, mime in (('portal.css', 'text/css'), ('portal.js', 'application/javascript')):
        path = re.search(fr'(/{re.escape(asset)}\?v=[a-f0-9]{{16}})', page)
        assert path is not None, asset
        with urlopen(base + path[1], timeout=6) as static:
            assert static.headers.get_content_type() == mime
            assert 'immutable' in static.headers['Cache-Control']
            assert len(static.read()) > 1000
    assert '<option selected>smoke-user</option>' in page
    assert '<td role="cell" class="account-name">smoke-user</td>' in page
    assert '<td role="cell" class="account-name">smoke-other</td>' not in page
    assert '<span>smoke-other</span>' in page
with urlopen(Request(admin + 'accounts.json' + filter_query, headers=headers), timeout=6) as response:
    payload = json.load(response)
    assert payload['selected_name'] == 'smoke-user'
    assert 'smoke-user' in payload['cards'] and 'smoke-other' not in payload['cards']
    assert 'smoke-other' in payload['options']
try:
    urlopen(admin + 'accounts.json', timeout=6)
except HTTPError as exc:
    assert exc.code == 403
else:
    raise AssertionError('Unauthenticated accounts JSON was accessible')
print('PASS account picker filtering and authenticated live refresh')
PY
ss -ltnH 'sport = :17763' | grep -F '127.0.0.1:17763'
if command -v nginx >/dev/null && command -v openssl >/dev/null; then
  openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=vpn.example.com \
    -addext subjectAltName=DNS:vpn.example.com -keyout "$testdir/key.pem" -out "$testdir/cert.pem" >/dev/null 2>&1
  mkdir "$testdir/nginx"
  sed -e 's|@@HOST@@|vpn.example.com|g' -e "s|@@CERT@@|$testdir/cert.pem|g" \
    -e "s|@@KEY@@|$testdir/key.pem|g" "$repo/deploy/nginx/site.conf.in" > "$testdir/site.conf"
  cat > "$testdir/nginx.conf" <<EOF
pid $testdir/nginx/nginx.pid;
error_log $testdir/nginx/nginx.log error;
events {}
http {
  include $repo/deploy/nginx/limits.conf;
  include $testdir/site.conf;
}
EOF
  nginx -t -p "$testdir/nginx/" -c "$testdir/nginx.conf"
  nginx -p "$testdir/nginx/" -c "$testdir/nginx.conf" -g 'daemon off;' &
  nginx_pid=$!
  https=(--noproxy '*' --resolve vpn.example.com:443:127.0.0.1 --cacert "$testdir/cert.pem")
  for attempt in {1..20}; do
    if curl -fsS "${https[@]}" https://vpn.example.com/login >/dev/null 2>&1; then break; fi
    sleep 0.2
  done
  curl -fsS "${https[@]}" https://vpn.example.com/login | grep -F 'action="/login"' >/dev/null
  python3 - "$testdir/cert.pem" <<'PYTEST'
import socket, ssl, sys
context = ssl.create_default_context(cafile=sys.argv[1])
with socket.create_connection(('127.0.0.1',443),timeout=5) as connection:
    with context.wrap_socket(connection,server_hostname='vpn.example.com') as client:
        client.sendall(b'GET /connect HTTP/1.1\r\nHost: vpn.example.com\r\nConnection: Upgrade\r\n'
                       b'Upgrade: websocket\r\nSec-WebSocket-Version: 13\r\n'
                       b'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n')
        assert client.makefile('rb').readline().startswith(b'HTTP/1.1 101 ')
PYTEST
  printf 'PASS HTTPS vhost and public /connect WebSocket upgrade to Xray\n'
fi
printf 'PASS isolated 3x-ui token and admin login, client creation, traffic collector, HTTP portal, subscription and Xray listener\n'
TEST

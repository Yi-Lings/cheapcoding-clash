#!/usr/bin/env bash
set -euo pipefail
umask 077
if (( EUID != 0 || $# != 1 )); then
  printf 'Usage: sudo bash deploy/backup.sh /absolute/private/backup.tar.gz\n' >&2
  exit 2
fi
output=$1
if [[ "$output" != /* || -e "$output" || -L "$output" ]]; then
  printf 'Use a new absolute output path.\n' >&2
  exit 2
fi
stage=$(mktemp -d)
trap 'rm -rf -- "$stage"' EXIT
python3 - "$stage" <<'PY'
from pathlib import Path
import shutil, sqlite3, sys

stage = Path(sys.argv[1])
files = {
    'panel/x-ui.db': '/var/lib/clash-panel/db/x-ui.db',
    'portal/portal.db': '/var/lib/clash-portal/portal.db',
    'portal/traffic.db': '/var/lib/clash-portal/traffic.db',
}
for name, original in files.items():
    target = stage / name
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect('file:' + original + '?mode=ro', uri=True) as source, sqlite3.connect(target) as copy:
        source.backup(copy)
shutil.copyfile('/var/lib/clash-portal/panel-token', stage / 'portal/panel-token')
config = Path('/etc/cheapcoding-clash/portal.env')
if config.is_file():
    shutil.copyfile(config, stage / 'portal.env')
PY
set -o noclobber
files=(panel portal)
if [[ -f "$stage/portal.env" ]]; then files+=(portal.env); fi
tar -C "$stage" -czf - "${files[@]}" > "$output"
printf 'Private backup created at %s\n' "$output"

#!/usr/bin/env bash
# Kestra config backup — implements docs/09 §9.2e (HU-1501 failover runbook §6).
#
# What: versioned, mode-600 local copies of the rotated Kestra credential env
# (/opt/kestra/kestra.env — the only copy of the live CouchDB admin password,
# generated during HU-1500) and the Kestra server config (/root/.kestra/
# config.yml), each snapshot sealed with a sha256 manifest, 30-day retention.
#
# Why: if .245 dies or the env file is lost/mis-rotated, CouchDB admin access
# to the live vault store is lost with it. This makes recovery a file copy
# instead of a credential reset against 4k+ live docs.
#
# Secret hygiene: NEVER commit these copies to git or upload them anywhere
# (that is the HU-1500 leak class). Off-host redundancy requires a designated
# secret-safe destination — set KESTRA_BACKUP_REMOTE (rsync target) only after
# the board names one (approval 5e713a10). Until then this is local-only.
#
# Usage:
#   bash scripts/backup_kestra_config.sh           # snapshot now
#   KESTRA_BACKUP_REMOTE=user@100.x.y.z:/backups/kestra-config \
#     bash scripts/backup_kestra_config.sh         # + off-host rsync (once designated)
#
# Restore (docs/09 §9.2e):
#   dir=/backups/kestra-config/<stamp>
#   (cd "$dir" && sha256sum -c SHA256SUMS)         # must pass before trusting
#   install -m 600 "$dir/kestra.env" /opt/kestra/kestra.env
#   install -m 644 "$dir/config.yml" /root/.kestra/config.yml
#   systemctl restart kestra && curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8080/
set -euo pipefail

DEST="${KESTRA_BACKUP_DIR:-/backups/kestra-config}"
RETENTION_DAYS="${KESTRA_BACKUP_RETENTION:-30}"
REMOTE="${KESTRA_BACKUP_REMOTE:-}"

SOURCES=(/opt/kestra/kestra.env /root/.kestra/config.yml)

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
dir="$DEST/$stamp"
mkdir -p "$dir"
chmod 700 "$DEST" "$dir"

for f in "${SOURCES[@]}"; do
  if [ ! -r "$f" ]; then
    echo "FAIL: source $f missing/unreadable — aborting snapshot $dir" >&2
    rm -rf "$dir"
    exit 1
  fi
  install -m 600 "$f" "$dir/$(basename "$f")"
done

(cd "$dir" && sha256sum ./* > SHA256SUMS && sha256sum -c SHA256SUMS >/dev/null)
echo "$(date -u +%FT%TZ) backup ok: $dir ($(find "$dir" -type f | wc -l) files)"

# Retention: drop whole snapshot dirs older than RETENTION_DAYS.
find "$DEST" -mindepth 1 -maxdepth 1 -type d -mtime +"$RETENTION_DAYS" -exec rm -rf {} +

# Off-host sync — intentionally inert until a secret-safe destination exists.
if [ -n "$REMOTE" ]; then
  if rsync -a --chmod=F600,D700 "$DEST/" "$REMOTE/"; then
    echo "$(date -u +%FT%TZ) off-host sync ok: $REMOTE"
  else
    echo "WARN: off-host rsync to $REMOTE failed" >&2
  fi
fi

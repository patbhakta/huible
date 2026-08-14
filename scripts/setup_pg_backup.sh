#!/usr/bin/env bash
# Idempotent installer for the Huible pg backup timer — HU-1672 AC #1,
# docs/09 §9.2f. Run ON the standby .245, from the repo checkout, after the
# cutover (HU-1644) has landed the running failover stack.
#
# What it does (single command, safe to re-run):
#   1. templats deploy/systemd/huible-pg-backup.{service,timer} with the
#      absolute repo path -> /etc/systemd/system/
#   2. daemon-reload + enable --now the timer (daily, catch-up via Persistent)
#   3. runs one immediate backup (scripts/backup_pg_dump.sh)
#   4. proves the restore path (scripts/verify_backup_restore.sh)
#
# Exit is non-zero if any step fails, so the post-cutover executor can gate
# §8 sign-off on this script's PASS output.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

ok()  { echo "ok:   $*"; }
bad() { echo "FAIL: $*" >&2; exit 1; }
note(){ echo "note: $*"; }

# ── Preflight ───────────────────────────────────────────────────────────────
[ -d /run/systemd/system ] || bad "systemd not PID 1 — install the cron fallback manually (docs/09 §9.2f)"
docker compose ps postgres 2>/dev/null | grep -q "postgres" \
  || bad "compose postgres not running — run after cutover (HU-1644), stack must be up"
for f in deploy/systemd/huible-pg-backup.service deploy/systemd/huible-pg-backup.timer \
         scripts/backup_pg_dump.sh scripts/verify_backup_restore.sh; do
  [ -f "$f" ] || bad "missing repo file: $f"
done

# ── Install units (idempotent: overwrite with current template) ────────────
sed "s|__REPO_DIR__|$REPO_DIR|g; s|%REPO_DIR%|$REPO_DIR|g" \
  deploy/systemd/huible-pg-backup.service > /etc/systemd/system/huible-pg-backup.service
install -m 644 deploy/systemd/huible-pg-backup.timer /etc/systemd/system/huible-pg-backup.timer
systemctl daemon-reload
systemctl enable --now huible-pg-backup.timer
ok "timer installed + enabled: $(systemctl list-timers huible-pg-backup.timer --no-pager | sed -n 2p | awk '{print $1, $2, $NF}')"

# ── Immediate backup (proves the worker end-to-end now, not at 00:00) ──────
bash scripts/backup_pg_dump.sh

# ── Restore-path proof ──────────────────────────────────────────────────────
bash scripts/verify_backup_restore.sh

echo
echo "BACKUP_SETUP_PASS — daily timer active, first dump + restore proof recorded (HU-1672 AC #1)"

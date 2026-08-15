#!/usr/bin/env bash
# HU-1681 S5 — retire the LiveSync/CouchDB stack. PRE-STAGED, DO NOT RUN until
# Pat's S3 card is accepted AND scripts/verify_fns_device_sync.sh exits 0, OR
# the CEO default trigger fires (HU-1750 comment 4442e588): silence through
# 2026-08-16 21:00 UTC → run with CONFIRM=yes BYPASS_DEVICE_GATE=HU-1750-4442e588.
# The verifier stays a HARD GATE inside this script: S3 must be server-side
# validated (device traffic + git auto-push + inventory) before anything here
# executes — the scoped bypass below tolerates ONLY the 3 device-side fails
# while all 7 server-side passes hold.
#
# Runbook source: docs/runbooks/couchdb-to-fns-migration.md §S5 (decision A from
# HU-1706). Runs ON the standby .245 (this agent host), root.
#
# Usage:
#   bash scripts/retire_livesync_stack.sh            # DRY RUN (default): plan only
#   CONFIRM=yes bash scripts/retire_livesync_stack.sh # execute retirement
#
# Steps (idempotent — safe to re-run after a partial failure):
#   S5a  Kestra: delete flows huible/huible-vault-{create,archive} (API 204),
#        strip COUCH_ADMIN_PASS from /opt/kestra/kestra.env (timestamped backup
#        first), restart kestra, verify active + :8080 responds
#   S5b  CouchDB: docker stop+rm couchdb-livesync (image couchdb:3 stays cached
#        for rollback); remove the brain.bhakta.us block from /etc/caddy/Caddyfile
#        (timestamped backup first), caddy validate, systemctl reload caddy
#   post FNS still healthy, kestra active, caddy active, block gone
#
# NOT automated (do after this script succeeds — see §S5c in runbook):
#   - update scripts/execute_failover.sh CouchDB checks to healthy/skip-on-retired
#     (must NOT happen before S5 executes)
#   - optional: delete untracked scripts/livesync/node_modules/ debris
#   - keep the S1 dump 30+ days before deleting
#   - HU-1707 (client provisioning rewrite) stays deferred
set -u

CONFIRM="${CONFIRM:-no}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERIFIER="$REPO_ROOT/scripts/verify_fns_device_sync.sh"

FNS_CONTAINER="${FNS_CONTAINER:-fast-note-sync}"
COUCH_CONTAINER="${COUCH_CONTAINER:-couchdb-livesync}"
CADDYFILE="${CADDYFILE:-/etc/caddy/Caddyfile}"
KESTRA_ENV="${KESTRA_ENV:-/opt/kestra/kestra.env}"
KESTRA_CFG="${KESTRA_CFG:-/root/.kestra/config.yml}"
KESTRA_URL="${KESTRA_URL:-http://127.0.0.1:8080}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="${BACKUP_DIR:-/backups/livesync-retire}"

step() { printf '\n==> %s\n' "$1"; }
info() { echo "    $1"; }

echo "=== HU-1681 S5 retirement (dry-run: $([ "$CONFIRM" = "yes" ] && echo NO || echo YES)) @ $(date -u +%FT%TZ) ==="

# ─── Gate 1: S3 server-side validation must pass ─────────────────────────────
# BYPASS_DEVICE_GATE=HU-1750-4442e588 enables the ONE-TIME scoped bypass from
# the CEO decision (HU-1750 comment 4442e588, 2026-08-15): tolerates exactly
# the 3 device-side verifier fails, ONLY while all 7 server-side passes hold.
# Any server-side regression → halt + re-open the decision. Nothing else passes.
step "Gate 1/2: running S3 verifier ($VERIFIER)"
if [ ! -x "$VERIFIER" ] && [ ! -f "$VERIFIER" ]; then
  echo "FATAL: verifier not found at $VERIFIER"; exit 2
fi
VERIFIER_OUT="$(bash "$VERIFIER" 2>&1)"; VRC=$?
echo "$VERIFIER_OUT"
if [ "$VRC" -eq 0 ]; then
  info "S3 verifier passed — full device-side validation."
elif [ "${BYPASS_DEVICE_GATE:-}" = "HU-1750-4442e588" ]; then
  srv_regression="$(printf '%s\n' "$VERIFIER_OUT" | grep '\[FAIL\]' | grep -Ev 'no plugin traffic after the S2 flip|no note/file sync from any device|git tip still at canary' || true)"
  srv_pass="$(printf '%s\n' "$VERIFIER_OUT" | grep -c '\[PASS\]')"
  if [ -z "$srv_regression" ] && [ "${srv_pass:-0}" -ge 7 ]; then
    info "CEO-scoped bypass ACTIVE (HU-1750 4442e588): only the 3 device-side fails present, ${srv_pass}/7 server-side passes hold."
  else
    echo "FATAL: server-side regression under bypass — HALT, re-open the HU-1750 decision."
    printf '%s\n' "$srv_regression"
    exit 3
  fi
else
  echo "FATAL: S3 verifier failed — S5 retirement is NOT unblocked. Do not bypass."
  exit 3
fi

# ─── Gate 2: explicit confirmation ───────────────────────────────────────────
step "Gate 2/2: confirmation"
if [ "$CONFIRM" != "yes" ]; then
  info "DRY RUN — planned actions:"
  info "  S5a: DELETE Kestra flows huible/huible-vault-{create,archive}; strip COUCH_ADMIN_PASS from $KESTRA_ENV; restart kestra"
  info "  S5b: docker stop+rm $COUCH_CONTAINER; remove brain.bhakta.us block from $CADDYFILE; caddy validate + reload"
  info "  backups land in $BACKUP_DIR/$STAMP/"
  info "Re-run with CONFIRM=yes to execute."
  exit 0
fi

mkdir -p "$BACKUP_DIR/$STAMP"

# ─── S5a: Kestra live cleanup ────────────────────────────────────────────────
step "S5a/1: delete Kestra CouchDB flows"
KU="$(awk '/basic-auth:/{f=1} f && /username:/{print $2; exit}' "$KESTRA_CFG" 2>/dev/null)"
KP="$(awk '/basic-auth:/{f=1} f && /password:/{print $2; exit}' "$KESTRA_CFG" 2>/dev/null)"
if [ -z "${KU:-}" ] || [ -z "${KP:-}" ]; then
  echo "FATAL: could not read Kestra basic-auth from $KESTRA_CFG"; exit 4
fi
for flow in huible-vault-create huible-vault-archive; do
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 -u "$KU:$KP" "$KESTRA_URL/api/v1/flows/huible/$flow")"
  case "$code" in
    200)
      del="$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 -X DELETE -u "$KU:$KP" "$KESTRA_URL/api/v1/flows/huible/$flow")"
      [ "$del" = "204" ] && info "deleted huible/$flow (204)" || { echo "FATAL: delete huible/$flow returned $del"; exit 5; }
      ;;
    404) info "huible/$flow already gone (404) — skip" ;;
    *)   echo "FATAL: GET huible/$flow returned $code"; exit 5 ;;
  esac
done

step "S5a/2: strip COUCH_ADMIN_PASS from $KESTRA_ENV"
if [ -f "$KESTRA_ENV" ]; then
  cp -a "$KESTRA_ENV" "$BACKUP_DIR/$STAMP/kestra.env.bak"
  if grep -q '^COUCH_ADMIN_PASS=' "$KESTRA_ENV" 2>/dev/null; then
    sed -i '/^COUCH_ADMIN_PASS=/d' "$KESTRA_ENV"
    chmod 600 "$KESTRA_ENV"
    info "removed COUCH_ADMIN_PASS (backup: $BACKUP_DIR/$STAMP/kestra.env.bak)"
  else
    info "no COUCH_ADMIN_PASS present — skip"
  fi
else
  info "$KESTRA_ENV missing — skip"
fi

step "S5a/3: restart kestra and verify"
systemctl restart kestra
# Java boot takes ~10s — fixed sleep caused false FATALs; poll up to 60s instead
kcode=""
for i in $(seq 1 30); do
  kcode="$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "$KESTRA_URL/" 2>/dev/null)"
  case "$kcode" in 200|307) break ;; esac
  sleep 2
done
systemctl is-active --quiet kestra && info "kestra.service active" || { echo "FATAL: kestra inactive after restart"; exit 6; }
case "$kcode" in 200|307) info "Kestra :8080 responds ($kcode)" ;; *) echo "FATAL: Kestra :8080 returned $kcode after 60s wait"; exit 6 ;; esac

# ─── S5b: CouchDB container + Caddy block ────────────────────────────────────
step "S5b/1: stop and remove $COUCH_CONTAINER"
if docker ps -a --format '{{.Names}}' | grep -qx "$COUCH_CONTAINER"; then
  docker stop "$COUCH_CONTAINER" && docker rm "$COUCH_CONTAINER"
  info "container stopped and removed (image stays cached for rollback)"
else
  info "container already absent — skip"
fi

step "S5b/2: remove brain.bhakta.us block from $CADDYFILE"
if grep -q '^brain\.bhakta\.us' "$CADDYFILE" 2>/dev/null; then
  cp -a "$CADDYFILE" "$BACKUP_DIR/$STAMP/Caddyfile.bak"
  awk '/^brain\.bhakta\.us \{/{skip=1} skip && /^\}/{skip=0; next} !skip' "$CADDYFILE" > "$CADDYFILE.new"
  # also drop the two brain-specific comment lines directly above the old block
  grep -v -e '^# Brain — Obsidian LiveSync (CouchDB)$' -e '^# Lock down: block admin endpoints and root info leak$' "$CADDYFILE.new" > "$CADDYFILE.new2"
  # collapse any doubled blank lines left behind
  awk 'BEGIN{b=0} /^$/{b++; if(b>1) next} !/^$/{b=0} {print}' "$CADDYFILE.new2" > "$CADDYFILE.new3"
  if caddy validate --config "$CADDYFILE.new3" >/dev/null 2>&1; then
    mv "$CADDYFILE.new3" "$CADDYFILE" && rm -f "$CADDYFILE.new" "$CADDYFILE.new2"
    info "block removed (backup: $BACKUP_DIR/$STAMP/Caddyfile.bak), config validated"
  else
    rm -f "$CADDYFILE.new" "$CADDYFILE.new2" "$CADDYFILE.new3"
    echo "FATAL: edited Caddyfile failed validation — original untouched"; exit 7
  fi
  systemctl reload caddy
  systemctl is-active --quiet caddy && info "caddy reloaded and active" || { echo "FATAL: caddy inactive after reload"; exit 7; }
else
  info "no brain.bhakta.us block in $CADDYFILE — skip"
fi

# ─── Post-verify ─────────────────────────────────────────────────────────────
step "Post-verify"
docker ps --format '{{.Names}}' | grep -qx "$FNS_CONTAINER" && info "FNS still Up" || { echo "FATAL: FNS container not Up"; exit 8; }
h="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:9000/api/health)"
[ "$h" = "200" ] && info "FNS /api/health 200" || { echo "FATAL: FNS health $h"; exit 8; }
docker ps -a --format '{{.Names}}' | grep -qx "$COUCH_CONTAINER" && { echo "FATAL: $COUCH_CONTAINER still present"; exit 8; } || info "couchdb-livesync gone"
systemctl is-active --quiet kestra && info "kestra active" || { echo "FATAL: kestra inactive"; exit 8; }
systemctl is-active --quiet caddy && info "caddy active" || { echo "FATAL: caddy inactive"; exit 8; }
grep -q '^brain\.bhakta\.us' "$CADDYFILE" && { echo "FATAL: brain block still in Caddyfile"; exit 8; } || info "brain.bhakta.us block gone"
grep -q '^COUCH_ADMIN_PASS=' "$KESTRA_ENV" 2>/dev/null && { echo "FATAL: COUCH_ADMIN_PASS still present"; exit 8; } || info "COUCH_ADMIN_PASS gone"

echo
echo "=== S5 retirement complete @ $(date -u +%FT%TZ) ==="
echo "Manual follow-ups (S5c) — do NOT skip:"
echo "  1. Update scripts/execute_failover.sh CouchDB checks to healthy/skip-on-retired"
echo "  2. Optional: rm -rf scripts/livesync/node_modules/ (untracked debris)"
echo "  3. Keep the S1 dump ($BACKUP_DIR/../ or repo dir) 30+ days before deleting"
echo "  4. HU-1707 (client provisioning vs FNS REST) stays deferred until a client onboards"
exit 0

#!/usr/bin/env bash
# FNS device-sync verifier — run the moment Pat confirms the S3 device-cutover card.
#
# Context: HU-1681 (retire LiveSync/CouchDB → fast-note-sync-service). S1 dump and
# S2 compose adoption are done. The sole remaining gate is Pat's S3 checkbox card:
# install the obsidian-fast-note-sync plugin on devices, then validate the flow
# edit-on-device → FNS → git auto-push → vault clone updated. This script is the
# FIRST thing to run on that wake: it proves server-side that real device traffic
# arrived and the git leg advanced — before any S5 retirement work is unblocked.
#
# Runs ON the standby .245 (this agent host), root, read-only (SQLite mode=ro,
# git inspect). Complements the runbook docs/runbooks/couchdb-to-fns-migration.md.
#
# Usage:
#   bash scripts/verify_fns_device_sync.sh
#       # optional override if the container was flipped again after 2026-08-15 08:37Z:
#   FLIP_LOCAL_TS="2026-08-15 16:37:20" bash scripts/verify_fns_device_sync.sh
#       # (sync_log.created_at is container-local +08:00; the default is the 08:37Z
#       #  S2 compose flip, expressed in that local timezone)
#
# Exit code: 0 only if every check passes. Any FAIL means S3 validation is NOT
# complete — do not proceed to S5 retirement; re-engage Pat via the card thread.
set -u

FNS_CONTAINER="${FNS_CONTAINER:-fast-note-sync}"
COUCH_CONTAINER="${COUCH_CONTAINER:-couchdb-livesync}"
FNS_BASE="${FNS_BASE:-http://127.0.0.1:9000}"
FNS_DATA="${FNS_DATA:-/data/fast-note-sync/storage}"
# Baseline seeded state (S1/S2 era): 74 seeded .md + 1 canary note.
CANARY_TIP="${CANARY_TIP:-9983049}"
MIN_NOTES="${MIN_NOTES:-75}"
# S2 compose flip epoch in sync_log local time (+08:00). Plugin activity strictly
# AFTER this proves a device reconnected to the compose-managed container.
FLIP_LOCAL_TS="${FLIP_LOCAL_TS:-2026-08-15 16:37:20}"

PASS=0; FAIL=0
ok()   { echo "  [PASS] $1"; PASS=$((PASS+1)); }
fail() { echo "  [FAIL] $1 — $2"; FAIL=$((FAIL+1)); }
note() { echo "       $1"; }

q() { sqlite3 "file:$1?mode=ro" "$2" 2>/dev/null; }
SYNC_DB="$FNS_DATA/database/db_user_sync_log_1.sqlite3"
NOTE_DB="$FNS_DATA/database/db_user_1.sqlite3"
GIT_WS="$FNS_DATA/git_workspace/1/1"

echo "=== FNS device-sync verification (HU-1681 S3 wake gate) ==="
echo "target: $FNS_BASE  time: $(date -u +%FT%TZ)  from: $(hostname)"
echo "flip baseline (sync_log local): $FLIP_LOCAL_TS   canary tip: $CANARY_TIP"
echo

# ─── 1. FNS container up + compose-managed + healthy ──────────────────────────
echo "## FNS service"
if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$FNS_CONTAINER"; then
  ok "container '$FNS_CONTAINER' is Up."
else
  fail "container '$FNS_CONTAINER' not Up" "docker ps does not list it — check compose project."
fi
if docker inspect "$FNS_CONTAINER" --format '{{index .Config.Labels "com.docker.compose.project"}}' 2>/dev/null | grep -q .; then
  ok "compose-managed (project label present)."
else
  fail "not compose-managed" "com.docker.compose.project label missing — S2 regression."
fi
health="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$FNS_BASE/api/health" 2>/dev/null)"
if [ "$health" = "200" ]; then
  ok "GET /api/health → 200."
else
  fail "health endpoint not 200" "got '$health'."
fi
restarts="$(docker inspect "$FNS_CONTAINER" --format '{{.RestartCount}}' 2>/dev/null)"
[ "${restarts:-x}" = "0" ] && ok "0 container restarts." || note "restarts=$restarts (investigate if unexpected)."
echo

# ─── 2. Device traffic: plugin sync rows AFTER the S2 flip ────────────────────
echo "## Device traffic (sync_log)"
plugin_after="$(q "$SYNC_DB" "SELECT COUNT(*) FROM sync_log WHERE client_type='ObsidianPlugin' AND created_at > '$FLIP_LOCAL_TS';")"
note_rows_after="$(q "$SYNC_DB" "SELECT COUNT(*) FROM sync_log WHERE client_type='ObsidianPlugin' AND type IN ('note','file') AND created_at > '$FLIP_LOCAL_TS';")"
echo "       plugin rows after flip: ${plugin_after:-?} (note/file: ${note_rows_after:-?})"
echo "       distinct clients ever:"
q "$SYNC_DB" "SELECT client_name || ' | ' || client_type || ' | v' || client_version || ' | rows=' || COUNT(*) FROM sync_log WHERE client_name != '' GROUP BY client_name, client_type, client_version;" | sed 's/^/         /'
echo
if [ "${plugin_after:-0}" -gt 0 ] 2>/dev/null; then
  ok "ObsidianPlugin traffic after the S2 flip ($plugin_after rows)."
else
  fail "no plugin traffic after the S2 flip" "a device must connect to the compose-managed container before validation."
fi
if [ "${note_rows_after:-0}" -gt 0 ] 2>/dev/null; then
  ok "note/file sync from a device after the flip ($note_rows_after rows)."
else
  fail "no note/file sync from any device" "settings-only traffic does not validate the sync leg."
fi
echo

# ─── 3. Note inventory intact (DB + git workspace) ────────────────────────────
echo "## Note inventory"
notes="$(q "$NOTE_DB" "SELECT COUNT(*) FROM note;")"
md_count="$(find "$GIT_WS" -name '*.md' -not -path '*/.git/*' 2>/dev/null | wc -l | tr -d ' ')"
echo "       notes in DB: ${notes:-?}   .md in git workspace: ${md_count:-?} (floor: $MIN_NOTES)"
if [ "${notes:-0}" -ge "$MIN_NOTES" ] 2>/dev/null; then
  ok "note count in DB ≥ $MIN_NOTES."
else
  fail "note count in DB below floor" "expected ≥ $MIN_NOTES, got '${notes:-?}' — seeded data damaged?"
fi
if [ "${md_count:-0}" -ge "$MIN_NOTES" ] 2>/dev/null; then
  ok ".md count in git workspace ≥ $MIN_NOTES."
else
  fail ".md count in git workspace below floor" "expected ≥ $MIN_NOTES, got '${md_count:-?}'."
fi
echo

# ─── 4. Git auto-push leg advanced past the canary ────────────────────────────
echo "## Git auto-push leg (vault remote source of truth)"
if ! git -C "$GIT_WS" rev-parse --verify -q "$CANARY_TIP" >/dev/null 2>&1; then
  fail "canary commit $CANARY_TIP missing" "workspace history rewritten — investigate before trusting anything."
else
  new_commits="$(git -C "$GIT_WS" rev-list --count "$CANARY_TIP..HEAD" 2>/dev/null)"
  echo "       commits since canary: ${new_commits:-?}"
  if [ "${new_commits:-0}" -gt 0 ] 2>/dev/null; then
    ok "git tip advanced past canary $CANARY_TIP ($new_commits new commit(s)) — auto-push fired."
    git -C "$GIT_WS" log "$CANARY_TIP..HEAD" --format='         %h %s' | head -5
    dirty="$(git -C "$GIT_WS" status --porcelain | wc -l | tr -d ' ')"
    [ "$dirty" = "0" ] && ok "git workspace clean (push completed)." \
      || fail "git workspace dirty" "$dirty uncommitted path(s) — push may be mid-flight or failing."
    remote_tip="$(git -C "$GIT_WS" ls-remote origin -h refs/heads/main 2>/dev/null | cut -f1 | head -c7)"
    local_tip="$(git -C "$GIT_WS" rev-parse --short HEAD 2>/dev/null)"
    [ "$remote_tip" = "$local_tip" ] && ok "remote main ($remote_tip) == local HEAD." \
      || fail "remote main != local HEAD" "remote='$remote_tip' local='$local_tip' — push incomplete."
  else
    fail "git tip still at canary" "no device edit reached the auto-push leg yet."
  fi
fi
echo

# ─── 5. Retirement still held (couchdb-livesync must be Up pre-validation) ────
echo "## Retirement gate (S5 held)"
if docker ps --format '{{.Names}}' | grep -qx "$COUCH_CONTAINER"; then
  ok "'$COUCH_CONTAINER' still Up — rollback path intact until validation passes."
else
  fail "'$COUCH_CONTAINER' not Up" "retired before S3 validation — stop and reconcile with the runbook."
fi
echo

echo "=== Result: $PASS pass / $FAIL fail ==="
[ "$FAIL" = "0" ] && exit 0 || exit 1

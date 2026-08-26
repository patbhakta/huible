#!/usr/bin/env bash
# HU-1428 AC #2 / HU-1432: activate the §7.4.1 staffed-responder roster in prod.
#
# Decision vehicle: board approval 6334d570 (replaces purged 4db47d4f).
# Option A (recommended in the approval): 2 trained grief responders + 1
# on-call licensed clinician, coverage 08:00-22:00 US Eastern, 7 days/week;
# outside hours escalations degrade to the G1 safe response + 988 (fail-safe
# never promises a person). Those decision values are the defaults here.
#
# Pure deploy-time env set — zero code diff (settings.py §7.4.1 block reads
# HANDOFF_* env; app container env_file is .env -> .env.failover on this
# host, compose project "huible" with docker-compose.failover.yml override).
#
# Usage:
#   bash scripts/activate_responder_roster.sh --check
#       # preconditions only, no changes
#   bash scripts/activate_responder_roster.sh --activate --approval <approval-id>
#       # activate Option A defaults (idempotent)
#   bash scripts/activate_responder_roster.sh --activate --approval <id> \
#       --responders 3 --pool alice,bob --tz America/New_York --open 8 --close 22
#
# What activation does:
#   1. Backup .env.failover (timestamped, alongside the existing .bak files).
#   2. Write HANDOFF_AVAILABLE_RESPONDERS / HANDOFF_RESPONDER_POOL /
#      HANDOFF_COVERAGE_MODE=hours / HANDOFF_COVERAGE_TZ /
#      HANDOFF_COVERAGE_OPEN_HOUR / HANDOFF_COVERAGE_CLOSE_HOUR (and SLA when
#      overridden) into .env.failover.
#   3. Recreate only the app container (docker compose up -d app — postgres
#      and the system Caddy are untouched; system Caddy fronts 127.0.0.1:8000).
#   4. Verify: /health ok AND /metrics gauge huible_handoff_available_responders
#      == N (the §7.4 alert-enablement signal, metrics.py:238).
#
# Every run tees evidence to logs/activate-roster-<UTC timestamp>.log.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

ENV_REAL=".env.failover"                 # .env is a symlink to this on prod
COMPOSE_FILES=(-f docker-compose.yml -f docker-compose.failover.yml)
APP="huible-app"
HEALTH_URL="http://127.0.0.1:8000/health"
GAUGE="huible_handoff_available_responders"
LOG_DIR="logs"; mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/activate-roster-$(date -u +%Y%m%dT%H%M%SZ).log"

# Option A decision defaults (board approval 6334d570).
RESPONDERS=2; POOL=""; TZ_NAME="America/New_York"; OPEN=8; CLOSE=22; SLA=""
MODE_CHECK=0; MODE_ACT=0; APPROVAL=""

pass=0; fail=0
ok()   { echo "  [PASS] $*" | tee -a "$LOG"; pass=$((pass+1)); }
bad()  { echo "  [FAIL] $*" | tee -a "$LOG"; fail=$((fail+1)); }
note() { echo "       $*" | tee -a "$LOG"; }
die()  { echo "ABORT: $*" | tee -a "$LOG"; echo "RESULT: ABORTED" | tee -a "$LOG"; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --check) MODE_CHECK=1 ;;
    --activate) MODE_ACT=1 ;;
    --approval) APPROVAL="${2:?}"; shift ;;
    --responders) RESPONDERS="${2:?}"; shift ;;
    --pool) POOL="${2:?}"; shift ;;
    --tz) TZ_NAME="${2:?}"; shift ;;
    --open) OPEN="${2:?}"; shift ;;
    --close) CLOSE="${2:?}"; shift ;;
    --sla) SLA="${2:?}"; shift ;;
    *) echo "unknown flag: $1" >&2; exit 64 ;;
  esac
  shift
done

[ $((MODE_CHECK + MODE_ACT)) -eq 1 ] || { echo "usage: $0 --check | $0 --activate --approval <id> [overrides]" >&2; exit 64; }
if [ "$MODE_ACT" = 1 ]; then
  [ -n "$APPROVAL" ] || { echo "--activate requires --approval <approval-id> (decision vehicle for the audit trail)" >&2; exit 64; }
  [ "$RESPONDERS" -ge 1 ] 2>/dev/null || die "--responders must be an integer >= 1 (0 is the pre-roster fail-safe — nothing to activate)"
  [ "$OPEN" -ge 0 ] && [ "$OPEN" -le 23 ] 2>/dev/null || die "--open must be 0-23 (inclusive)"
  [ "$CLOSE" -ge 1 ] && [ "$CLOSE" -le 24 ] 2>/dev/null || die "--close must be 1-24 (exclusive)"
fi

echo "=== Responder-roster activation | mode=$( [ $MODE_CHECK = 1 ] && echo check || echo "activate" ) ===" | tee -a "$LOG"
echo "host: $(hostname -f)  time: $(date -u +%FT%TZ)" | tee -a "$LOG"
[ "$MODE_ACT" = 1 ] && echo "decision vehicle: approval $APPROVAL (Option A defaults unless overridden)" | tee -a "$LOG"

echo "## Preconditions" | tee -a "$LOG"
docker inspect "$APP" --format 'up' >/dev/null 2>&1 && ok "$APP container running" || bad "$APP not running"
docker inspect "$APP" --format '{{index .Config.Labels "com.docker.compose.project"}}' 2>/dev/null | grep -q '^huible$' \
  && ok "compose project = huible (canonical prod)" || bad "compose project mismatch — not the canonical prod app"
[ -f "$ENV_REAL" ] && ok "$ENV_REAL present" || bad "$ENV_REAL missing"
[ -L .env ] && [ "$(readlink .env)" = "$ENV_REAL" ] && ok ".env -> $ENV_REAL symlink intact" || bad ".env is not the $ENV_REAL symlink"
CUR_RESP="$(docker inspect "$APP" --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n 's/^HANDOFF_AVAILABLE_RESPONDERS=//p' | tr -d '\r')"
if [ "$CUR_RESP" = "0" ]; then
  ok "current responders = 0 (pre-roster fail-safe — activation is the intended transition)"
elif [ "$MODE_CHECK" = 1 ]; then
  note "current responders = $CUR_RESP (roster already wired — re-run --activate to change values)"
fi
HEALTH="$(curl -fsS --max-time 8 "$HEALTH_URL" 2>/dev/null || true)"
echo "$HEALTH" | grep -q '"status":"ok"' && ok "app health ok ($HEALTH_URL)" || bad "app health not ok ($HEALTH_URL)"
GAUGE_NOW="$(docker exec "$APP" sh -c "curl -s -m 5 http://127.0.0.1:8000/metrics 2>/dev/null" | awk -v g="$GAUGE" '$1 == g {print $2}')"
[ -n "$GAUGE_NOW" ] && ok "gauge $GAUGE = $GAUGE_NOW" || bad "gauge $GAUGE not exposed on /metrics"
# Drill-traffic paging suppression must be in the RUNNING app code before
# activation (HU-1428 pre-work, digest #5 watch item): once the roster is
# staffed, real paging channels (Telnyx/SMTP/webhook) may gain credentials —
# the deployed build must already keep verification drills (demo-/drill-
# marked traffic) off real on-call devices.
if docker exec "$APP" python -c "from huible.api.paging import DrillSuppressingPager" >/dev/null 2>&1; then
  ok "drill-suppression pager present in running app code (HU-1428)"
else
  bad "running app predates drill suppression — deploy first: git pull, then docker compose -f docker-compose.yml -f docker-compose.failover.yml build app && docker compose -f docker-compose.yml -f docker-compose.failover.yml up -d app"
fi
[ "$fail" -gt 0 ] && die "preconditions failed"
[ "$MODE_CHECK" = 1 ] && { echo "RESULT: CHECK_OK ($(date -u +%FT%TZ))" | tee -a "$LOG"; exit 0; }

# ─── Activation ──────────────────────────────────────────────────────────────
set_env() { # 1=key 2=value — replace or append in $ENV_REAL
  if grep -q "^$1=" "$ENV_REAL"; then
    sed -i "s|^$1=.*|$1=$2|" "$ENV_REAL"
  else
    { echo ""; echo "$1=$2"; } >> "$ENV_REAL"
  fi
}

echo "## Backup" | tee -a "$LOG"
BAK="$ENV_REAL.bak.$(date -u +%Y%m%dT%H%M%SZ)"
cp "$ENV_REAL" "$BAK" && ok "backup -> $BAK"

echo "## Env write (Option A: $RESPONDERS responders, coverage $OPEN:00-$CLOSE:00 $TZ_NAME)" | tee -a "$LOG"
set_env HANDOFF_AVAILABLE_RESPONDERS "$RESPONDERS"; ok "HANDOFF_AVAILABLE_RESPONDERS=$RESPONDERS"
if [ -n "$POOL" ]; then set_env HANDOFF_RESPONDER_POOL "$POOL"; ok "HANDOFF_RESPONDER_POOL=$POOL"
else set_env HANDOFF_RESPONDER_POOL ""; note "responder pool empty — synthetic responder ids (handoff.py §staffing)"; fi
set_env HANDOFF_COVERAGE_MODE "hours"; ok "HANDOFF_COVERAGE_MODE=hours"
set_env HANDOFF_COVERAGE_TZ "$TZ_NAME"; ok "HANDOFF_COVERAGE_TZ=$TZ_NAME"
set_env HANDOFF_COVERAGE_OPEN_HOUR "$OPEN"; ok "HANDOFF_COVERAGE_OPEN_HOUR=$OPEN"
set_env HANDOFF_COVERAGE_CLOSE_HOUR "$CLOSE"; ok "HANDOFF_COVERAGE_CLOSE_HOUR=$CLOSE"
if [ -n "$SLA" ]; then set_env HANDOFF_SLA_TARGET_SECONDS "$SLA"; ok "HANDOFF_SLA_TARGET_SECONDS=$SLA (overridden)"; fi

echo "## Recreate app container" | tee -a "$LOG"
docker compose "${COMPOSE_FILES[@]}" up -d app >>"$LOG" 2>&1 && ok "compose up -d app done" || die "compose up -d app failed"

echo "## Post-activation verification" | tee -a "$LOG"
HEALTH_OK=0
for i in $(seq 1 12); do
  H="$(curl -fsS --max-time 5 "$HEALTH_URL" 2>/dev/null || true)"
  echo "$H" | grep -q '"status":"ok"' && { HEALTH_OK=1; break; }
  sleep 5
done
[ "$HEALTH_OK" = 1 ] && ok "app health ok after recreate" || bad "app health not ok after recreate"
NEW_RESP="$(docker inspect "$APP" --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n 's/^HANDOFF_AVAILABLE_RESPONDERS=//p' | tr -d '\r')"
[ "$NEW_RESP" = "$RESPONDERS" ] && ok "container env responders = $NEW_RESP" || bad "container env responders = $NEW_RESP, expected $RESPONDERS"
GAUGE_NEW="$(docker exec "$APP" sh -c "curl -s -m 5 http://127.0.0.1:8000/metrics 2>/dev/null" | awk -v g="$GAUGE" '$1 == g {print $2}')"
GAUGE_INT="${GAUGE_NEW%%.*}"
[ "$GAUGE_INT" = "$RESPONDERS" ] && ok "gauge $GAUGE = $GAUGE_NEW (§7.4 alerts armed)" || bad "gauge = $GAUGE_NEW, expected $RESPONDERS"

echo "=== Summary: $pass passed, $fail failed ===" | tee -a "$LOG"
if [ "$fail" -eq 0 ]; then
  echo "RESULT: ROSTER_ACTIVATED (responders=$RESPONDERS coverage=$OPEN-$CLOSE $TZ_NAME approval=$APPROVAL)" | tee -a "$LOG"
else
  echo "RESULT: ROSTER_ACTIVATED_WITH_GAPS — restore with: cp $BAK $ENV_REAL && docker compose ${COMPOSE_FILES[*]} up -d app" | tee -a "$LOG"
  exit 1
fi

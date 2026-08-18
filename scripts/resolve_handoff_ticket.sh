#!/usr/bin/env bash
# HU-1866 (HU-1428 item 3): close synthetic/verification handoff tickets
# without contaminating SLA metrics.
#
# Implements docs/runbooks/handoff-synthetic-ticket-closure.md:
#   - --list                  show the pending (ENQUEUED) responder queue
#   - --audit                 show the full audit log + telemetry (all-time view)
#   - <ticket_id> [--note ...] resolve a ticket as `abandoned` (the synthetic
#                            closure outcome — never `answered`, which would
#                            fake huible_handoff_answered_within_sla_rate)
#
# Safety guards:
#   - Refuses to resolve any ticket whose current outcome is not `enqueued`
#     (resolve() overwrites outcome + clinical_review_note on ANY ticket, so
#     re-resolving a terminal ticket erases the original audit evidence).
#     Override only with --force and a --reason.
#   - Reads the API key from .env.failover API_KEYS (first entry). No key ->
#     fail closed (the API answers 401 without it).
#   - Provenance: the note is forced to start with "SYNTHETIC:" and include
#     the operator/issue, so audit-trail readers can grep for synthetic
#     closures forever.
#
# Usage:
#   bash scripts/resolve_handoff_ticket.sh --list
#   bash scripts/resolve_handoff_ticket.sh hh-xxxx --note "deploy-check HU-18xx" [--responder huible-tech-lead]
#   bash scripts/resolve_handoff_ticket.sh hh-xxxx --note "..." --force --reason "re-running closure drill"
#
# Exit codes: 0 ok; 1 usage/API error; 2 guard refusal.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$REPO_DIR/.env.failover}"
BASE_URL="${HUIBLE_BASE_URL:-http://127.0.0.1:8000}"

die() { echo "ERROR: $*" >&2; exit "${2:-1}"; }

api_key() {
  [ -f "$ENV_FILE" ] || die "env file not found: $ENV_FILE (set ENV_FILE)" 1
  local line
  line="$(grep -E '^API_KEYS=' "$ENV_FILE" | head -1 || true)"
  [ -n "$line" ] || die "API_KEYS not set in $ENV_FILE" 1
  local first
  first="${line#API_KEYS=}"
  first="${first%%,*}"
  [ -n "$first" ] || die "API_KEYS empty in $ENV_FILE" 1
  printf '%s' "${first%%:*}"
}

KEY="$(api_key)"
AUTH=(-H "Authorization: Bearer $KEY" -H "Content-Type: application/json")

get_json() { curl -sS -m 15 "${AUTH[@]}" "$@"; }

MODE="list"
TICKET=""
NOTE=""
RESPONDER="huible-tech-lead"
FORCE=0
REASON=""

while [ $# -gt 0 ]; do
  case "$1" in
    --list) MODE="list" ;;
    --audit) MODE="audit" ;;
    --note) [ $# -ge 2 ] || die "--note needs a value" 1; NOTE="$2"; shift ;;
    --responder) [ $# -ge 2 ] || die "--responder needs a value" 1; RESPONDER="$2"; shift ;;
    --force) FORCE=1 ;;
    --reason) [ $# -ge 2 ] || die "--reason needs a value" 1; REASON="$2"; shift ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    hh-*|*-*) [ -z "$TICKET" ] || die "only one ticket id allowed" 1; TICKET="$1"; MODE="resolve" ;;
    *) die "unknown arg: $1" 1 ;;
  esac
  shift
done

case "$MODE" in
  list)
    get_json "$BASE_URL/api/v1/handoff/tickets" | python3 -m json.tool
    exit 0
    ;;
  audit)
    get_json "$BASE_URL/api/v1/handoff/audit" | python3 -m json.tool
    exit 0
    ;;
esac

# resolve mode
[ -n "$TICKET" ] || die "ticket id required (hh-…). See --help." 1
[ -n "$NOTE" ] || die "--note required (what created this synthetic ticket, e.g. 'deploy-check HU-18xx')" 1
case "$NOTE" in
  SYNTHETIC:*) ;;
  *) NOTE="SYNTHETIC: $NOTE" ;;
esac

# Guard: fetch current state from the audit log (all-time) and refuse to
# overwrite terminal outcomes — resolve() replaces outcome + note wholesale.
STATE="$(get_json "$BASE_URL/api/v1/handoff/audit" \
  | python3 -c '
import json,sys
tid = sys.argv[1]
try:
    data = json.load(sys.stdin)["data"]["tickets"]
except Exception:
    print("fetch_error"); raise SystemExit
for t in data:
    if t.get("ticket_id") == tid or t.get("id") == tid:
        print(t.get("outcome", "unknown")); break
else:
    print("missing")
' "$TICKET" 2>/dev/null || echo fetch_error)"

case "$STATE" in
  enqueued)
    : ;;
  missing)
    die "ticket $TICKET not found in the audit log (typo? wrong env?)" 1
    ;;
  fetch_error)
    die "could not read audit log (API down or auth failed)" 1
    ;;
  *)
    if [ "$FORCE" != "1" ]; then
      die "REFUSING: ticket $TICKET is already terminal ($STATE). resolve() overwrites outcome + clinical_review_note and would erase audit evidence. Use --force --reason '...' only for a documented re-closure." 2
    fi
    [ -n "$REASON" ] || die "--force requires --reason (goes into the note)" 2
    NOTE="$NOTE [forced re-closure of $STATE ticket: $REASON]"
    ;;
esac

BODY="$(python3 -c '
import json,sys
print(json.dumps({
    "outcome": "abandoned",
    "responder_id": sys.argv[1],
    "clinical_review_note": sys.argv[2],
}))
' "$RESPONDER" "$NOTE")"

HTTP_CODE="$(curl -sS -m 15 "${AUTH[@]}" -o /tmp/opencode/resolve_resp.json -w '%{http_code}' \
  -X POST -d "$BODY" "$BASE_URL/api/v1/handoff/tickets/$TICKET/resolve")"

LOG="$REPO_DIR/logs/resolve-handoff-$(date -u +%Y%m%dT%H%M%SZ).log"
if [ "$HTTP_CODE" = "200" ]; then
  { echo "RESOLVED (abandoned): $TICKET"; python3 -m json.tool < /tmp/opencode/resolve_resp.json; } | tee -a "$LOG"
else
  cat /tmp/opencode/resolve_resp.json >&2 || true
  die "resolve failed: HTTP $HTTP_CODE"
fi

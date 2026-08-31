#!/usr/bin/env bash
# Read-only Paperclip watch-posture guard: verifies an issue's persisted monitor
# is in a firing-eligible state (status in_progress/in_review, monitorNextCheckAt
# non-null and not stale beyond --grace-min minutes). Detects silent monitor
# lapses like the twice-observed HU-1743 apex-watch drift (status flipped to
# todo -> tickDueIssueMonitors skips the issue -> nextCheckAt never fires).
# Does NOT mutate anything; repair (checkout + re-arm PATCH) belongs to a run.
# Usage: watch-guard.sh --issue-id <id> [--grace-min 15]
# Exit: 0 healthy, 2 drifted (repair needed), 1 API/usage error.
set -euo pipefail
issue_id="" grace_min=15
while [ $# -gt 0 ]; do
  case "$1" in
    --issue-id)  issue_id="$2"; shift 2 ;;
    --grace-min) grace_min="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done
[ -n "$issue_id" ] || { echo "--issue-id required" >&2; exit 1; }
[ -n "${PAPERCLIP_API_KEY:-}" ] && [ -n "${PAPERCLIP_API_URL:-}" ] || {
  echo "PAPERCLIP_API_KEY / PAPERCLIP_API_URL not set" >&2; exit 1; }

resp=$(curl -sf -H "Authorization: Bearer $PAPERCLIP_API_KEY" \
  "$PAPERCLIP_API_URL/api/issues/$issue_id") || {
  echo "API fetch failed for $issue_id" >&2; exit 1; }

status=$(jq -r '.status' <<<"$resp")
next=$(jq -r '.monitorNextCheckAt // empty' <<<"$resp")
service=$(jq -r '.executionPolicy.monitor.serviceName // "unnamed"' <<<"$resp")

fail() { echo "DRIFTED [$service] issue $issue_id: $1 (status=$status next=$next)" >&2; exit 2; }

case "$status" in in_progress|in_review) ;; *) fail "status not monitor-eligible" ;; esac
[ -n "$next" ] || fail "monitorNextCheckAt null"
now_iso=$(date -u +%Y-%m-%dT%H:%M:%SZ)
deadline=$(date -u -d "$next +$grace_min minutes" +%s 2>/dev/null) || fail "unparseable nextCheckAt"
[ "$(date -u -d "$now_iso" +%s)" -le "$deadline" ] || fail "nextCheckAt stale beyond ${grace_min}m grace"
echo "HEALTHY [$service] issue $issue_id status=$status next=$next"

#!/usr/bin/env bash
# Re-arm the one-shot HU-1796 issue monitor (pg-health sweep wake chain).
#
# The issue monitor is consumed on each fire (tickDueIssueMonitors), so every
# sweep heartbeat must re-arm it. The MINIMAL payload below is the only shape
# with a proven 200 (armed 2026-09-08T02:14:27Z, fired 03:15Z). Full-policy
# shapes with kind/serviceName/timeoutAt/stages are rejected (400 enum / 422).
#
# Usage: rearm-monitor.sh [--issue-id <id>] [--minutes <n>]
#   Defaults: --issue-id $PAPERCLIP_TASK_ID, --minutes 63
# Exit: 0 armed (monitorNextCheckAt non-null in response), 1 not armed.
set -euo pipefail
issue_id="${PAPERCLIP_TASK_ID:-}" minutes=63
while [ $# -gt 0 ]; do
  case "$1" in
    --issue-id) issue_id="$2"; shift 2 ;;
    --minutes)  minutes="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
[ -n "$issue_id" ] || { echo "--issue-id or PAPERCLIP_TASK_ID required" >&2; exit 2; }
[ -n "${PAPERCLIP_API_KEY:-}" ] && [ -n "${PAPERCLIP_API_URL:-}" ] && \
[ -n "${PAPERCLIP_RUN_ID:-}" ] || {
  echo "PAPERCLIP_API_KEY / PAPERCLIP_API_URL / PAPERCLIP_RUN_ID not set" >&2; exit 2; }

next=$(date -u -d "+$minutes min" +%Y-%m-%dT%H:%M:%SZ)
body=$(jq -n --arg next "$next" \
  '{executionPolicy: {monitor: {nextCheckAt: $next, scheduledBy: "assignee",
     notes: "pg-health sweep cadence (~1h); re-armed by HU-1796 sweep"}}}')

resp=$(curl -s -w '\n%{http_code}' -X PATCH \
  -H "Authorization: Bearer $PAPERCLIP_API_KEY" \
  -H "X-Paperclip-Run-Id: $PAPERCLIP_RUN_ID" \
  -H "Content-Type: application/json" \
  -d "$body" \
  "$PAPERCLIP_API_URL/api/issues/$issue_id") || { echo "curl failed" >&2; exit 1; }

code=$(printf '%s' "$resp" | tail -n1)
payload=$(printf '%s' "$resp" | sed '$d')
echo "HTTP $code"
printf '%s' "$payload" | jq '{identifier, status, monitorNextCheckAt, monitorScheduledBy, executionPolicy}' 2>/dev/null || printf '%s\n' "$payload"

armed=$(printf '%s' "$payload" | jq -r '.monitorNextCheckAt // .issue.monitorNextCheckAt // empty' 2>/dev/null)
[ "$code" = "200" ] && [ -n "$armed" ] || { echo "NOT ARMED (next=$armed)" >&2; exit 1; }
echo "ARMED [$issue_id] next=$armed"

#!/usr/bin/env bash
# Multiline-safe Paperclip issue update: PATCH /api/issues/{id} with a markdown
# comment read from stdin (preserves line breaks via jq --arg).
# Usage: paperclip-issue-update.sh --issue-id <id> [--status <status>] <<'MD'
#   ... markdown ...
#   MD
set -euo pipefail
issue_id="" status="" review_interaction_id=""
while [ $# -gt 0 ]; do
  case "$1" in
    --issue-id)              issue_id="$2"; shift 2 ;;
    --status)                status="$2"; shift 2 ;;
    --review-interaction-id) review_interaction_id="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
[ -n "$issue_id" ] || { echo "--issue-id required" >&2; exit 2; }
comment="$(cat)"
body=$(jq -n --arg c "$comment" --arg s "$status" --arg r "$review_interaction_id" \
  '{comment: $c}
   + (if $s == "" then {} else {status: $s} end)
   + (if $r == "" then {} else {reviewInteractionId: $r} end)')
curl -s -X PATCH \
  -H "Authorization: Bearer $PAPERCLIP_API_KEY" \
  -H "X-Paperclip-Run-Id: $PAPERCLIP_RUN_ID" \
  -H "Content-Type: application/json" \
  -d "$body" \
  "$PAPERCLIP_API_URL/api/issues/$issue_id" | jq '{id, identifier, status}'

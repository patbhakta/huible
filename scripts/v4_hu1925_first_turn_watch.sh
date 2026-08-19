#!/usr/bin/env bash
# HU-1925 Task 1 — first-live-turn evidence watcher (self-disarming).
# Runs from cron every 15 min. Detects the first REAL WhatsApp turn whose
# sent prompt (api_content) carried the v4 Arm A block WITH the gist digest,
# captures the matching core-gateway recall lines + session depth, writes
# logs/hu1925-first-turn-evidence.{log,json}, then removes its own cron.
#
# Evidence markers (persist-what-you-send sidecar in hermes state.db):
#   <session-memory-v4>            — plugin wrapped the prepend_context
#   "[Conversation digest — per-block session gists" — gists reached the prompt
# Real turn = role=user message in a WhatsApp session (id like 2026…_*),
# i.e. cron_*/agent-internal sessions excluded.
set -u
REPO=/root/repos/huible
LOG=$REPO/logs/hu1925-first-turn-evidence.log
SNAP=$REPO/logs/hu1925-first-turn-evidence.json
STATE=$REPO/logs/hu1925-first-turn.state
DB=/root/.hermes/state.db

[ -f "$STATE" ] || date -u +%s > "$STATE"
SINCE=$(cat "$STATE")

# candidate real turns since baseline: user msgs in WhatsApp sessions with the v4 block
ROWS=$(sqlite3 -separator '|' "$DB" "
  select m.session_id, datetime(m.timestamp,'unixepoch'), m.id
  from messages m
  where m.timestamp >= $SINCE
    and m.role='user'
    and m.api_content like '%session-memory-v4%'
    and m.session_id glob '2026*'
  order by m.timestamp;" 2>/dev/null)

HIT=""
while IFS='|' read -r sid ts mid; do
  [ -z "$sid" ] && continue
  if sqlite3 "$DB" "select api_content from messages where id=$mid;" 2>/dev/null | grep -q "Conversation digest"; then
    HIT="$sid|$ts|$mid"
    break
  fi
done <<< "$ROWS"

date -u +%s > "$STATE"

if [ -z "$HIT" ]; then
  echo "[$(date -u +%FT%TZ)] watch: no digest-carrying real turn yet (since epoch $SINCE)" >> "$LOG"
  exit 0
fi

SID=${HIT%%|*}; REST=${HIT#*|}; TS=${REST%%|*}; MID=${REST##*|}
# session depth — issue requires an active session >=40 turns
DEPTH=$(sqlite3 "$DB" "select count(*) from messages where session_id='$SID' and role in ('user','assistant');" 2>/dev/null)
# core-gateway recall evidence around the turn (5 min before -> 5 min after)
RECALLS=$(journalctl -u tdai-memory-core --since "$(date -u -d "$TS -5 minutes" +%FT%TZ)" --until "$(date -u -d "$TS +5 minutes" +%FT%TZ)" --no-pager 2>/dev/null | grep -E "Recall completed.*strategy=v4-arm-a|digest=[0-9]+ blocks" | tail -4)

{
  echo "[$(date -u +%FT%TZ)] FIRST DIGEST-CARRYING REAL TURN DETECTED (HU-1925 Task 1)"
  echo "  session=$SID turn_time=$TS message_id=$MID session_depth=$DEPTH turns"
  echo "  gateway recall evidence:"
  echo "$RECALLS" | sed 's/^/    /'
  echo "  (api_content of message $MID contains <session-memory-v4> with the conversation digest)"
} >> "$LOG"

cat > "$SNAP" <<EOF
{
  "detected_at": "$(date -u +%FT%TZ)",
  "session_id": "$SID",
  "turn_time": "$TS",
  "message_id": $MID,
  "session_depth_turns": ${DEPTH:-0},
  "depth_ok": $([ "${DEPTH:-0}" -ge 40 ] && echo true || echo false),
  "gateway_recall_lines": $(printf '%s' "$RECALLS" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')
}
EOF

# disarm: remove own cron entry
crontab -l 2>/dev/null | grep -v "v4_hu1925_first_turn_watch" | crontab -
echo "[$(date -u +%FT%TZ)] watcher disarmed (cron removed)" >> "$LOG"

#!/usr/bin/env bash
# HU-1925 v4 Arm A production dogfood watch — one-shot probe.
# Invoked by the Paperclip routine "HU-1925 v4 Arm A 7-day production watch"
# (daily 09:30 UTC, Aug 20-26 2026). Appends a snapshot to logs/hu1925-watch.log
# and writes logs/hu1925-watch.snapshot.json (machine-readable latest state).
#
# Checks (all diffed against logs/hu1925-watch.state baseline):
#   1. First-turn verification: new hermes request dumps containing
#      <session-memory-v4> + core-gateway "Recall completed ... strategy=v4-arm-a".
#   2. Gist maintenance: new gists/<session>.json with arc/bullets validated,
#      contractMiss flagged if present.
# 3. Quality signals: v4->v3 fallback warns ("recall failed"), strategy=v3
#    recalls on prod, z.ai 429 counts in hermes journal.
# 4. Write-path health (Task 2 dependency): completed turns only sync to
#    gateway L0 after the assistant reply lands (mirror skips interrupted /
#    429-failed turns by design #15218). Gists need 40 L0 turns/session, so
#    track conversation/add calls + new L0 rows to explain gist dormancy.
#    Also list gist-eligible-but-ungisted sessions (>=40 L0 rows, no gist
#    file): gist maintenance triggers only on new settle-time L1 runs, so
#    dormant sessions stay ungisted until new turns arrive (write-path-only
#    trigger; verified 2026-08-19 via hu1925-writeprobe: add->L0->L1->gist
#    scheduling chain healthy).
# Rollback lever (only on evidence, human/PM decision): set MEMORY_TDAI_READ_PATH=v3
# in tdai-memory-core unit env + systemctl restart tdai-memory-core.
set -uo pipefail
export XDG_RUNTIME_DIR=/run/user/0
REPO=/root/repos/huible
cd "$REPO" || exit 1
LOG=logs/hu1925-watch.log
STATE=logs/hu1925-watch.state
SNAP=logs/hu1925-watch.snapshot.json
GIST_DIR=/root/.memory-tencentdb/memory-tdai/gists
DUMPS=/root/.hermes/sessions

log() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$LOG"; }

if [ ! -f "$STATE" ]; then
  date -u +%FT%TZ > "$STATE"
  ls "$GIST_DIR" 2>/dev/null | sort > "$STATE.gists"
  ls "$DUMPS"/request_dump_*.json 2>/dev/null | xargs -r -n1 basename | sort > "$STATE.dumps"
  log "baseline seeded at $(cat "$STATE") (gists: $(wc -l < "$STATE.gists"), dumps: $(wc -l < "$STATE.dumps"))"
fi
SINCE=$(cat "$STATE")

# --- refresh baselines for next run (diff against previous) ---
ls "$GIST_DIR" 2>/dev/null | sort > "$STATE.gists.new"
ls "$DUMPS"/request_dump_*.json 2>/dev/null | xargs -r -n1 basename | sort > "$STATE.dumps.new"
NEW_GISTS=$(comm -13 "$STATE.gists" "$STATE.gists.new")
NEW_DUMPS=$(comm -13 "$STATE.dumps" "$STATE.dumps.new")

log "=== HU-1925 watch probe (since $SINCE) ==="

# 1. First-turn verification evidence
# Primary: api_content sidecar in hermes state.db (persist-what-you-send — the
# exact prompt bytes). request_dump_*.json files proved UNRELIABLE for this
# check: the 2026-08-19 11:24 real turn had <session-memory-v4> in api_content
# but not in the request dump (dump renders a pre-sidecar snapshot), so
# dump-only greps under-report. Dumps kept as a secondary signal.
v4_dumps=0; v3_dumps=0
for f in $NEW_DUMPS; do
  if grep -q 'session-memory-v4' "$DUMPS/$f" 2>/dev/null; then
    v4_dumps=$((v4_dumps+1)); log "V4-IN-PROMPT: $f"
  else
    v3_dumps=$((v3_dumps+1)); log "no-v4-tag:    $f"
  fi
done
# api_content check: real user turns whose sent prompt contained the v4 block.
# state.db timestamps are epoch seconds; SINCE is ISO, so guard with date.
STATE_DB=/root/.hermes/state.db
SINCE_EPOCH=$(date -u -d "$SINCE" +%s 2>/dev/null || echo 0)
V4_TURNS=$(sqlite3 "$STATE_DB" "select count(*) from messages where timestamp >= $SINCE_EPOCH and api_content like '%session-memory-v4%';" 2>/dev/null || echo 0)
[ "$V4_TURNS" -gt 0 ] && log "V4-IN-APICONTENT: $V4_TURNS turn(s) since baseline"
REC_V4=$(journalctl -u tdai-memory-core --since "$SINCE" --no-pager 2>/dev/null | grep -c 'Recall completed.*strategy=v4-arm-a')
REC_V3=$(journalctl -u tdai-memory-core --since "$SINCE" --no-pager 2>/dev/null | grep -c 'Recall completed.*strategy=v3')
PREPEND_CHARS=$(journalctl -u tdai-memory-core --since "$SINCE" --no-pager 2>/dev/null | grep -o 'Recall completed.*prepend=[0-9]* chars, strategy=v4-arm-a' | tail -1 | grep -o 'prepend=[0-9]*' | head -1)
log "recalls since baseline: v4-arm-a=$REC_V4 v3=$REC_V3 last=$PREPEND_CHARS"
log "request dumps: with-v4=$v4_dumps without-v4=$v3_dumps"

# 2. Gist maintenance
for g in $NEW_GISTS; do
  python3 - "$GIST_DIR/$g" <<'PY' 2>/dev/null
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    blocks = d.get("blocks") or d.get("gists") or []
    if isinstance(d, list): blocks = d
    n = len(blocks) if isinstance(blocks, list) else 1
    arc = bool(d.get("arc") if isinstance(d, dict) else (blocks and getattr(blocks[-1], "get", lambda k: None)("arc")))
    miss = "contractMiss" in json.dumps(d)
    print(f"GIST {sys.argv[1].split('/')[-1]}: blocks={n} arc={'yes' if arc else 'UNKNOWN'} contractMiss={'FLAGGED' if miss else 'none'}")
except Exception as e:
    print(f"GIST {sys.argv[1].split('/')[-1]}: PARSE-ERROR {e}")
PY
done
[ -z "$NEW_GISTS" ] && log "gists: no new files"

# 3. Quality signals
FALLBACKS=$(journalctl -u tdai-memory-core --since "$SINCE" --no-pager 2>/dev/null | grep -cE '\[recall\] recall failed|v4.*fallback')
R429=$(journalctl --user -u hermes-gateway --since "$SINCE" --no-pager 2>/dev/null | grep -c 'Usage limit reached')
log "quality signals: recall-fallback-warns=$FALLBACKS zai-429=$R429"

# 4. Write-path health
CONV_ADDS=$(journalctl -u tdai-memory-core --since "$SINCE" --no-pager 2>/dev/null | grep -c 'REQUEST_START POST /v3/conversation/add')
NEW_L0=$(sqlite3 -readonly /root/.memory-tencentdb/memory-tdai/vectors.db "select count(*) from l0_conversations where recorded_at >= '$SINCE';" 2>/dev/null || echo 0)
log "write-path: conversation_add=$CONV_ADDS new_l0_rows=$NEW_L0"
# Gist-eligible ungisted sessions (>=40 L0 rows, no gists/<session>.json).
ELIG=$(sqlite3 -readonly /root/.memory-tencentdb/memory-tdai/vectors.db "select session_key || ' ' || count(*) from l0_conversations group by session_key having count(*) >= 40;" 2>/dev/null | while read -r s n; do [ -f "$GIST_DIR/$s.json" ] || printf '%s(%s) ' "$s" "$n"; done)
log "gist-eligible ungisted: ${ELIG:-none}"

cat > "$SNAP" <<EOF
{
  "probe_at": "$(date -u +%FT%TZ)",
  "since": "$SINCE",
  "first_turn_verified": $([ "$V4_TURNS" -gt 0 ] && echo true || echo false),
  "v4_turns_apicontent": $V4_TURNS,
  "dumps_with_v4": $v4_dumps,
  "dumps_without_v4": $v3_dumps,
  "recalls_v4": $REC_V4,
  "recalls_v3": $REC_V3,
  "last_prepend": "${PREPEND_CHARS#prepend=}",
  "new_gists": "$(echo "$NEW_GISTS" | xargs echo -n)",
  "fallback_warns": $FALLBACKS,
  "zai_429": $R429,
  "conv_adds": $CONV_ADDS,
  "new_l0_rows": $NEW_L0,
  "gist_eligible_ungisted": "$(echo "$ELIG" | xargs echo -n)"
}
EOF

# commit new baselines
date -u +%FT%TZ > "$STATE"
mv "$STATE.gists.new" "$STATE.gists"; mv "$STATE.dumps.new" "$STATE.dumps"
log "=== probe done ==="

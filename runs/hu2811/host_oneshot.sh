#!/usr/bin/env bash
# HU-2811 Five-Friends v0 — host-side one-shot executor.
# Arms the real run at 2026-09-11T00:40:30Z (after the z.ai 00:00Z ledger
# reset). Replaces the disabled Kestra Process-runner trigger (kestra2 has no
# repo mount; see flows/pat-five-friends-v0.yaml description).
set -u
REPO=/root/repos/huible
OUT="$REPO/runs/hu2811"
TARGET_EPOCH=$(python3 - <<'PY'
from datetime import datetime, timezone
print(int(datetime(2026, 9, 11, 0, 40, 30, tzinfo=timezone.utc).timestamp()))
PY
)
mkdir -p "$OUT"
LOG="$OUT/oneshot.log"
{
  echo "[oneshot] armed $(date -u +%FT%TZ) pid $$ target_epoch=$TARGET_EPOCH"
  NOW=$(date -u +%s)
  WAIT=$((TARGET_EPOCH - NOW))
  if (( WAIT > 0 )); then
    echo "[oneshot] sleeping ${WAIT}s until 2026-09-11T00:40:30Z"
    sleep "$WAIT"
  fi
  echo "[oneshot] firing $(date -u +%FT%TZ)"
  cd "$REPO"
  python3 scripts/v2_harness/h4_five_friends_run.py \
    --i-am-the-boss --run --turns 35 --out-dir "$OUT" 2>&1
  RC=$?
  echo "[oneshot] harness rc=$RC $(date -u +%FT%TZ)"
  printf '{"fired_at": "%s", "finished_at": "%s", "rc": %d}\n' \
    "$(date -u +%FT%TZ)" "$(date -u +%FT%TZ)" "$RC" > "$OUT/HOST_ONESHOT_DONE"
} >> "$LOG" 2>&1

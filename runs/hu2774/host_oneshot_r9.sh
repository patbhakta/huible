#!/usr/bin/env bash
# HU-2774 r9 confirmation battery — host-side one-shot executor.
# Arms the battery at 2026-09-11T00:30:30Z (after the z.ai 00:00Z ledger
# reset). Replaces the disabled Kestra Process-runner trigger (kestra2 has no
# /root/repos/huible mount; every task would fail — see flow
# pat.personas.dual-persona-confirmation-r9 rev 3, trigger disabled=True).
# Task order + commands are verbatim from the flow (rev 2).
set -u
REPO=/root/repos/huible
OUT="$REPO/runs/hu2774"
TARGET_EPOCH=$(python3 - <<'PY'
from datetime import datetime, timezone
print(int(datetime(2026, 9, 11, 0, 30, 30, tzinfo=timezone.utc).timestamp()))
PY
)
mkdir -p "$OUT"
LOG="$OUT/r9_oneshot.log"
export HU2774_ENV="$REPO/.env"
export HU2774_ENGINE="http://127.0.0.1:8000"
{
  echo "[r9-oneshot] armed $(date -u +%FT%TZ) pid $$ target_epoch=$TARGET_EPOCH"
  NOW=$(date -u +%s)
  WAIT=$((TARGET_EPOCH - NOW))
  if (( WAIT > 0 )); then
    echo "[r9-oneshot] sleeping ${WAIT}s until 2026-09-11T00:30:30Z"
    sleep "$WAIT"
  fi
  echo "[r9-oneshot] firing $(date -u +%FT%TZ)"
  cd "$REPO"

  echo "[r9-oneshot] task reset-conversation-memory $(date -u +%FT%TZ)"
  python3 scripts/personas_reset_conversation_memory.py --env "$REPO/.env" --execute 2>&1
  RESET_RC=$?
  echo "[r9-oneshot] reset rc=$RESET_RC $(date -u +%FT%TZ)"
  if (( RESET_RC != 0 )); then
    echo "[r9-oneshot] ABORT: reset-conversation-memory failed (flow semantics: abort before conversations)"
    printf '{"fired_at": "%s", "finished_at": "%s", "reset_rc": %d, "aborted": true}\n' \
      "$(date -u +%FT%TZ)" "$(date -u +%FT%TZ)" "$RESET_RC" > "$OUT/R9_HOST_ONESHOT_DONE"
    exit 1
  fi

  OVERALL_RC=0
  for slot in friends-1 friends-2 friends-3 stranger-1 stranger-2 stranger-3; do
    SCENARIO="${slot%%-*}"
    echo "[r9-oneshot] task confirm-$slot $(date -u +%FT%TZ)"
    python3 scripts/personas_dual_converse.py --turns 24 \
      --run-id "hu2774-r9-$slot" --scenario "$SCENARIO" --out-dir "$OUT" 2>&1
    RC=$?
    echo "[r9-oneshot] confirm-$slot rc=$RC $(date -u +%FT%TZ)"
    # flow rev 2 semantics: allowFailure — a failing run never aborts the rest
  done

  echo "[r9-oneshot] task battery-gate $(date -u +%FT%TZ)"
  python3 scripts/personas_battery_gate.py --out-dir "$OUT" \
    hu2774-r9-friends-1 hu2774-r9-friends-2 hu2774-r9-friends-3 \
    hu2774-r9-stranger-1 hu2774-r9-stranger-2 hu2774-r9-stranger-3 2>&1
  GATE_RC=$?
  echo "[r9-oneshot] battery-gate rc=$GATE_RC (non-zero = gate FAILED: aggregate bar not met) $(date -u +%FT%TZ)"

  echo "[r9-oneshot] done $(date -u +%FT%TZ)"
  printf '{"fired_at": "%s", "finished_at": "%s", "reset_rc": %d, "gate_rc": %d}\n' \
    "$(date -u +%FT%TZ)" "$(date -u +%FT%TZ)" "$RESET_RC" "$GATE_RC" > "$OUT/R9_HOST_ONESHOT_DONE"
} >> "$LOG" 2>&1

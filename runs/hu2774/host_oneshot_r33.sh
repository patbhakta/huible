#!/usr/bin/env bash
# HU-2774 r33 battery — host-side one-shot executor (12a2ce7 recall_miss
# lost with the 2026-09-14 ~01:55Z process). Delta vs r12 shell: round-scoped
# lock/log/done-file names; everything else unchanged — preflight abort keeps
# the run dir clean, reset-before-slot purges conversation state, six slots
# (friends-1..3, stranger-1..3), allowFailure semantics, lag-1 gate as-fired
# (30a6a0b). Engine build: 00:06:28Z container, DYNAMICS_ENFORCER_ENABLED=true.
set -u
REPO=/root/repos/huible
OUT="$REPO/runs/hu2774"
mkdir -p "$OUT"
exec 9>"$OUT/.r33_battery.lock" || exit 1
flock -n 9 || { echo "[r33-oneshot] duplicate arm rejected (lock held) $(date -u +%FT%TZ)" >> "$OUT/r33_oneshot.log"; exit 0; }
TARGET_EPOCH=${R33_TARGET_EPOCH:-0}
SLOTS=(friends-1 friends-2 friends-3 stranger-1 stranger-2 stranger-3)
RUN_TAG=${R33_RUN_TAG:-hu2774-r33}
LOG="$OUT/r33_oneshot.log"
export HU2774_ENV="$REPO/.env"
export HU2774_ENGINE="http://127.0.0.1:8000"
{
  echo "[r33-oneshot] armed $(date -u +%FT%TZ) pid $$ target_epoch=$TARGET_EPOCH"
  NOW=$(date -u +%s)
  WAIT=$((TARGET_EPOCH - NOW))
  if (( WAIT > 0 )); then
    echo "[r33-oneshot] sleeping ${WAIT}s"
    sleep "$WAIT"
  fi
  echo "[r33-oneshot] firing $(date -u +%FT%TZ)"
  cd "$REPO"

  OVERALL_RC=0
  GATE_RC=9
  echo "[r33-oneshot] task preflight-zai-window $(date -u +%FT%TZ)"
  python3 scripts/personas_preflight_probe.py
  PREFLIGHT_RC=$?
  echo "[r33-oneshot] preflight rc=$PREFLIGHT_RC $(date -u +%FT%TZ)"
  if (( PREFLIGHT_RC != 0 )); then
    echo "[r33-oneshot] ABORT: zai window walled or engine down — no slots burned"
    OVERALL_RC=8
  fi

  if (( OVERALL_RC == 0 )); then
  for slot in "${SLOTS[@]}"; do
    SCENARIO="${slot%%-*}"
    echo "[r33-oneshot] task reset-before-$slot $(date -u +%FT%TZ)"
    python3 scripts/personas_reset_conversation_memory.py --env "$REPO/.env" --execute 2>&1
    RESET_RC=$?
    echo "[r33-oneshot] reset rc=$RESET_RC $(date -u +%FT%TZ)"
    if (( RESET_RC != 0 )); then
      echo "[r33-oneshot] ABORT: reset before $slot failed"
      OVERALL_RC=1
      break
    fi
    echo "[r33-oneshot] task confirm-$slot $(date -u +%FT%TZ)"
    python3 scripts/personas_dual_converse.py --turns 24 \
      --run-id "$RUN_TAG-$slot" --scenario "$SCENARIO" --out-dir "$OUT" 2>&1
    RC=$?
    echo "[r33-oneshot] confirm-$slot rc=$RC $(date -u +%FT%TZ)"
    # flow rev 2 semantics: allowFailure — a failing run never aborts the rest
  done

  echo "[r33-oneshot] task battery-gate $(date -u +%FT%TZ)"
  # shellcheck disable=SC2086
  python3 scripts/personas_battery_gate.py --out-dir "$OUT" \
    "$RUN_TAG-friends-1" "$RUN_TAG-friends-2" "$RUN_TAG-friends-3" \
    "$RUN_TAG-stranger-1" "$RUN_TAG-stranger-2" "$RUN_TAG-stranger-3" 2>&1
  GATE_RC=$?
  echo "[r33-oneshot] battery-gate rc=$GATE_RC (non-zero = gate FAILED: aggregate bar not met) $(date -u +%FT%TZ)"
  fi

  echo "[r33-oneshot] done $(date -u +%FT%TZ)"
  printf '{"fired_at": "%s", "finished_at": "%s", "gate_rc": %d}\n' \
    "$(date -u +%FT%TZ)" "$(date -u +%FT%TZ)" "$GATE_RC" > "$OUT/R33_HOST_ONESHOT_DONE"
} >> "$LOG" 2>&1

#!/usr/bin/env bash
# HU-2774 r11 confirmation battery — host-side one-shot executor.
# Delta vs r10 (2026-09-12, four r10 failures 0-root-caused + fixed):
# 1. reset purges conversation_turns + crisis_sessions for hu2774-% (r10
#    friends-1 pause: 34 stale history rows from a killed 09-11 attempt
#    pushed turn_count to 41 > dosage cap 40 -> pause_session platform text).
# 2. per-persona s1 conversation ids (shared-id history leaked the seed into
#    Monica's context -> false cross-session recall, r10 stranger-1).
# 3. checker: bare tv/show/episode out of sitcom-meta (corpus F3 evidence).
# 4. dynamics block v2: question ceiling + anti-riff-lock + stronger echo.
set -u
REPO=/root/repos/huible
OUT="$REPO/runs/hu2774"
TARGET_EPOCH=${R11_TARGET_EPOCH:-0}
SLOTS=(friends-1 friends-2 friends-3 stranger-1 stranger-2 stranger-3)
RUN_TAG=${R11_RUN_TAG:-hu2774-r11}
mkdir -p "$OUT"
LOG="$OUT/r11_oneshot.log"
export HU2774_ENV="$REPO/.env"
export HU2774_ENGINE="http://127.0.0.1:8000"
{
  echo "[r11-oneshot] armed $(date -u +%FT%TZ) pid $$ target_epoch=$TARGET_EPOCH"
  NOW=$(date -u +%s)
  WAIT=$((TARGET_EPOCH - NOW))
  if (( WAIT > 0 )); then
    echo "[r11-oneshot] sleeping ${WAIT}s"
    sleep "$WAIT"
  fi
  echo "[r11-oneshot] firing $(date -u +%FT%TZ)"
  cd "$REPO"

  OVERALL_RC=0
  GATE_RC=9
  for slot in "${SLOTS[@]}"; do
    SCENARIO="${slot%%-*}"
    echo "[r11-oneshot] task reset-before-$slot $(date -u +%FT%TZ)"
    python3 scripts/personas_reset_conversation_memory.py --env "$REPO/.env" --execute 2>&1
    RESET_RC=$?
    echo "[r11-oneshot] reset rc=$RESET_RC $(date -u +%FT%TZ)"
    if (( RESET_RC != 0 )); then
      echo "[r11-oneshot] ABORT: reset before $slot failed"
      OVERALL_RC=1
      break
    fi
    echo "[r11-oneshot] task confirm-$slot $(date -u +%FT%TZ)"
    python3 scripts/personas_dual_converse.py --turns 24 \
      --run-id "$RUN_TAG-$slot" --scenario "$SCENARIO" --out-dir "$OUT" 2>&1
    RC=$?
    echo "[r11-oneshot] confirm-$slot rc=$RC $(date -u +%FT%TZ)"
    # flow rev 2 semantics: allowFailure — a failing run never aborts the rest
  done

  echo "[r11-oneshot] task battery-gate $(date -u +%FT%TZ)"
  # shellcheck disable=SC2086
  python3 scripts/personas_battery_gate.py --out-dir "$OUT" \
    "$RUN_TAG-friends-1" "$RUN_TAG-friends-2" "$RUN_TAG-friends-3" \
    "$RUN_TAG-stranger-1" "$RUN_TAG-stranger-2" "$RUN_TAG-stranger-3" 2>&1
  GATE_RC=$?
  echo "[r11-oneshot] battery-gate rc=$GATE_RC (non-zero = gate FAILED: aggregate bar not met) $(date -u +%FT%TZ)"

  echo "[r11-oneshot] done $(date -u +%FT%TZ)"
  printf '{"fired_at": "%s", "finished_at": "%s", "gate_rc": %d}\n' \
    "$(date -u +%FT%TZ)" "$(date -u +%FT%TZ)" "$GATE_RC" > "$OUT/R11_HOST_ONESHOT_DONE"
} >> "$LOG" 2>&1

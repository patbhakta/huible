#!/usr/bin/env bash
# HU-2774 r12 confirmation battery — host-side one-shot executor.
# Delta vs r11 (2026-09-12, r11 3/6 — three residual variance modes):
# 1. engine r12 deploy: ordinal-recall lane reads conversation_index nodes by
#    metadata (get_conversation_index_memories) — the vector top-16 pre-filter
#    let the index drop out of rank on large vault corpora (r11-friends-1
#    Monica xsession MISS, predigest/0) . Deployed via image rebuild + app
#    restart BEFORE this script; registry hydration picks up dynamics v3.
# 2. dynamics v3 (applied pre-restart): greet a friend BY NAME in the first
#    reply (r11 friends-1 recognition MISS); question ceiling two-in-five
#    (r11 stranger-1 qrate 0.52 > 0.45 band top); echo rule demands one of
#    their exact words even when riffing (r11 stranger-3 echo 0.16 < 0.23).
# 3. reset-before-slot unchanged (purges conversation memories +
#    conversation_turns + crisis_sessions for hu2774-%).
set -u
REPO=/root/repos/huible
OUT="$REPO/runs/hu2774"
# Single-flight guard: the heartbeat runtime can retry a timed-out launcher
# call, arming a second battery that races the first (r11 + r12 double-fire
# finding 2026-09-12: interleaved resets corrupt both runs). A duplicate arm
# exits immediately; lock dies with the process.
mkdir -p "$OUT"
exec 9>"$OUT/.r12_battery.lock" || exit 1
flock -n 9 || { echo "[r12-oneshot] duplicate arm rejected (lock held) $(date -u +%FT%TZ)" >> "$OUT/r12_oneshot.log"; exit 0; }
TARGET_EPOCH=${R12_TARGET_EPOCH:-0}
SLOTS=(friends-1 friends-2 friends-3 stranger-1 stranger-2 stranger-3)
RUN_TAG=${R12_RUN_TAG:-hu2774-r12}
LOG="$OUT/r12_oneshot.log"
export HU2774_ENV="$REPO/.env"
export HU2774_ENGINE="http://127.0.0.1:8000"
{
  echo "[r12-oneshot] armed $(date -u +%FT%TZ) pid $$ target_epoch=$TARGET_EPOCH"
  NOW=$(date -u +%s)
  WAIT=$((TARGET_EPOCH - NOW))
  if (( WAIT > 0 )); then
    echo "[r12-oneshot] sleeping ${WAIT}s"
    sleep "$WAIT"
  fi
  echo "[r12-oneshot] firing $(date -u +%FT%TZ)"
  cd "$REPO"

  OVERALL_RC=0
  GATE_RC=9
  for slot in "${SLOTS[@]}"; do
    SCENARIO="${slot%%-*}"
    echo "[r12-oneshot] task reset-before-$slot $(date -u +%FT%TZ)"
    python3 scripts/personas_reset_conversation_memory.py --env "$REPO/.env" --execute 2>&1
    RESET_RC=$?
    echo "[r12-oneshot] reset rc=$RESET_RC $(date -u +%FT%TZ)"
    if (( RESET_RC != 0 )); then
      echo "[r12-oneshot] ABORT: reset before $slot failed"
      OVERALL_RC=1
      break
    fi
    echo "[r12-oneshot] task confirm-$slot $(date -u +%FT%TZ)"
    python3 scripts/personas_dual_converse.py --turns 24 \
      --run-id "$RUN_TAG-$slot" --scenario "$SCENARIO" --out-dir "$OUT" 2>&1
    RC=$?
    echo "[r12-oneshot] confirm-$slot rc=$RC $(date -u +%FT%TZ)"
    # flow rev 2 semantics: allowFailure — a failing run never aborts the rest
  done

  echo "[r12-oneshot] task battery-gate $(date -u +%FT%TZ)"
  # shellcheck disable=SC2086
  python3 scripts/personas_battery_gate.py --out-dir "$OUT" \
    "$RUN_TAG-friends-1" "$RUN_TAG-friends-2" "$RUN_TAG-friends-3" \
    "$RUN_TAG-stranger-1" "$RUN_TAG-stranger-2" "$RUN_TAG-stranger-3" 2>&1
  GATE_RC=$?
  echo "[r12-oneshot] battery-gate rc=$GATE_RC (non-zero = gate FAILED: aggregate bar not met) $(date -u +%FT%TZ)"

  echo "[r12-oneshot] done $(date -u +%FT%TZ)"
  printf '{"fired_at": "%s", "finished_at": "%s", "gate_rc": %d}\n' \
    "$(date -u +%FT%TZ)" "$(date -u +%FT%TZ)" "$GATE_RC" > "$OUT/R12_HOST_ONESHOT_DONE"
} >> "$LOG" 2>&1

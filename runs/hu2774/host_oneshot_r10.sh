#!/usr/bin/env bash
# HU-2774 r10 confirmation battery — host-side one-shot executor.
# Delta vs r9 (HU-2819 root-cause, 2026-09-11): the conversation-memory reset
# runs before EVERY slot, not once per battery. r9 ran it once before
# friends-1, so slot N's conversation writebacks (including the per-
# conversation ordinal index rows) were still in the retrieval corpus when
# slot N+1 ran — stranger slots answered the cross-session recall probe with
# the FRIENDS opener the DB still carried. The reset script's own docstring
# semantics ("each battery run starts from identical state") are restored.
# Voice-dynamics steering (personas_set_voice_dynamics.py) landed 2026-09-11
# ~01:42Z + app restart; this battery is the first full measurement with it.
set -u
REPO=/root/repos/huible
OUT="$REPO/runs/hu2774"
TARGET_EPOCH=${R10_TARGET_EPOCH:-0}
SLOTS=(friends-1 friends-2 friends-3 stranger-1 stranger-2 stranger-3)
RUN_TAG=${R10_RUN_TAG:-hu2774-r10}
mkdir -p "$OUT"
LOG="$OUT/r10_oneshot.log"
export HU2774_ENV="$REPO/.env"
export HU2774_ENGINE="http://127.0.0.1:8000"
{
  echo "[r10-oneshot] armed $(date -u +%FT%TZ) pid $$ target_epoch=$TARGET_EPOCH"
  NOW=$(date -u +%s)
  WAIT=$((TARGET_EPOCH - NOW))
  if (( WAIT > 0 )); then
    echo "[r10-oneshot] sleeping ${WAIT}s"
    sleep "$WAIT"
  fi
  echo "[r10-oneshot] firing $(date -u +%FT%TZ)"
  cd "$REPO"

  OVERALL_RC=0
  GATE_RC=9
  for slot in "${SLOTS[@]}"; do
    SCENARIO="${slot%%-*}"
    echo "[r10-oneshot] task reset-before-$slot $(date -u +%FT%TZ)"
    python3 scripts/personas_reset_conversation_memory.py --env "$REPO/.env" --execute 2>&1
    RESET_RC=$?
    echo "[r10-oneshot] reset rc=$RESET_RC $(date -u +%FT%TZ)"
    if (( RESET_RC != 0 )); then
      echo "[r10-oneshot] ABORT: reset before $slot failed"
      OVERALL_RC=1
      break
    fi
    echo "[r10-oneshot] task confirm-$slot $(date -u +%FT%TZ)"
    python3 scripts/personas_dual_converse.py --turns 24 \
      --run-id "$RUN_TAG-$slot" --scenario "$SCENARIO" --out-dir "$OUT" 2>&1
    RC=$?
    echo "[r10-oneshot] confirm-$slot rc=$RC $(date -u +%FT%TZ)"
    # flow rev 2 semantics: allowFailure — a failing run never aborts the rest
  done

  echo "[r10-oneshot] task battery-gate $(date -u +%FT%TZ)"
  # shellcheck disable=SC2086
  python3 scripts/personas_battery_gate.py --out-dir "$OUT" \
    "$RUN_TAG-friends-1" "$RUN_TAG-friends-2" "$RUN_TAG-friends-3" \
    "$RUN_TAG-stranger-1" "$RUN_TAG-stranger-2" "$RUN_TAG-stranger-3" 2>&1
  GATE_RC=$?
  echo "[r10-oneshot] battery-gate rc=$GATE_RC (non-zero = gate FAILED: aggregate bar not met) $(date -u +%FT%TZ)"

  echo "[r10-oneshot] done $(date -u +%FT%TZ)"
  printf '{"fired_at": "%s", "finished_at": "%s", "gate_rc": %d}\n' \
    "$(date -u +%FT%TZ)" "$(date -u +%FT%TZ)" "$GATE_RC" > "$OUT/R10_HOST_ONESHOT_DONE"
} >> "$LOG" 2>&1

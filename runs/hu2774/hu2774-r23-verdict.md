# HU-2774 r23 verdict — INFRA-VOID (z.ai lane 503 wall mid-battery; battery not adjudicated)

Round: armed 2026-09-14 02:10:02Z, preflight rc=0 (lane serving), same engine
build as r21/r22 (00:06:28Z container, `DYNAMICS_ENFORCER_ENABLED=true`),
gate lag-1 walk as-fired (`30a6a0b`). This was the relaunch of the r23 arm
lost with the ~01:55Z process (zero artifacts existed from that arm; the r22
verdict's "r23 armed" note referred to it).

## Result: void battery — provider outage, not an engine/persona failure

- friends-1, friends-2: PASS (25 turns each, real-zai).
- friends-3: died t17 `503 LLM_TRANSIENT` (engine-reported, `retryable: true`)
  after 17 healthy real-zai turns.
- stranger-1/2/3: all died at t0 with the same `503 LLM_TRANSIENT` (~3.5 min
  of internal retries each).
- No `fake(budget)` turns anywhere — this was a hard 503 wall, not the r16
  budget-degradation class.

**BATTERY: FAIL (1/6, infra).** The gate score is not adjudicated: slots 3–6
have no result JSON by design (abort-before-slot r14 semantics — a slot whose
lane is walled must not produce half-walled evidence). The wall began
~02:21Z (friends-3 mid-slot) and cleared by 02:41Z (preflight green,
standalone re-probe) — a ~15–20 minute provider blip, narrower and harder
than the r16 wall. Streak arithmetic unchanged: r21 4/6, r22 5/6, r23 void —
the 3-consecutive-6/6 bar still counts from zero.

## Dispositions

- friends-1/2 PASS JSONs are preserved as lane-health evidence only; they do
  not carry over into any later round.
- Runner unchanged mid-sequence (no infra-retry wrapper added): an
  abort-on-wall battery is the r14/r16-correct behavior, and a slot-retry
  change would be a mid-sequence procedure edit needing its own corpus
  re-baseline. Revisit only if 503 walls recur across rounds.
- r24 armed immediately (lane green at arm time), gate unchanged.
- Artifacts: `runs/hu2774/friends/hu2774-r23-friends-{1,2}.json`, transcript
  md files, `r23_oneshot.log`, `R23_HOST_ONESHOT_DONE` (`gate_rc: 1`).

# HU-2774 r26 verdict — 4/6, INFRA-FAIL tail: z.ai 503 wall #2 killed stranger-2/3

Round: 2026-09-14 04:50:13→05:23:05Z. friends-1/2/3 and stranger-1 all
**PASS** (25 turns each, real-zai, all five criteria clean — verdict JSONs
carry the margins). At 05:18Z the lane went down mid-round: stranger-2 died
t16 and stranger-3 t6 with `503 LLM_TRANSIENT` (engine-reported, retryable;
zero `fake(budget)` turns). Standalone preflight at ~05:25Z also 503 — wall
still active at round end.

**BATTERY: FAIL (4/6, infra tail).** No verdict is adjudicated on the two
MISSING slots (abort-before-slot held — no result JSON, no half-walled
evidence). This is the second 503 wall of the night (r23: ~02:21–02:41Z,
~20 min). Pattern is consistent with provider-side turbulence, not engine or
persona regressions: in 3 slots of clean lane this round the personas went
4/4 with full-margin passes, including Monica xsession HITs (the r25 weak
turn — she quoted her true first inbound correctly in all passing slots).

Streak: still zero (r25 4/6 quality-fail, r26 infra-fail). Bar unchanged:
3 consecutive 6/6.

## Dispositions

- r27 armed with a 20-min delayed target epoch (~05:48Z) so its own
  preflight fires after the likely wall-clear; rc=8 abort with zero slots
  burned if still walled.
- Two walls in one night is within the historical band (r16 wall, r20 wall
  class); no runner change. If a third wall hits, consider a wall-gap
  schedule (fire batteries in the lane's quiet hours) rather than in-run
  retries.
- Artifacts: `runs/hu2774/*/hu2774-r26-*.json` (4 slots), `r26_oneshot.log`,
  `R26_HOST_ONESHOT_DONE` (`gate_rc: 1`).

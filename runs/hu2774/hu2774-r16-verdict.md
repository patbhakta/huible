# HU-2774 r16 verdict — dynamics enforcer + gate echo-metric fix: **battery 6/6**

Round: 2026-09-13, battery 16:56→17:17:21Z (slots fired via `host_oneshot_r12.sh`,
`R12_RUN_TAG=hu2774-r16`). Engine: enforcer enabled (`DYNAMICS_ENFORCER_ENABLED=true`,
commit `afaac7c` = 5ecba01 + HU-2850 conditions C1–C4), rebuilt + restarted before the
run; preflight rc=0 (zai lane serving). Gate: `30a6a0b` (lag-1 echo walk, see below).

## Headline

**6/6 slots PASS** under the corrected, study-calibrated gate. Under the gate AS
SHIPPED at fire time (lag-2 echo walk), r16 scored 3/6 (failures: grounded_wit on
friends-1/2, stranger-1). Stored transcripts were re-scored with the corrected walk —
both verdicts are preserved in each run JSON (`verdict_original_lag2`,
`verdict_rescored_lag1`).

| slot | lag-2 (as fired) | lag-1 (corrected) | echo (lag-1) |
|---|---|---|---|
| friends-1 | FAIL grounded_wit (0.12) | **PASS** | 0.76 |
| friends-2 | FAIL grounded_wit (0.16) | **PASS** | 0.68 |
| friends-3 | PASS | **PASS** | 0.68 |
| stranger-1 | FAIL grounded_wit (0.20) | **PASS** | 0.72 |
| stranger-2 | PASS | **PASS** | 0.72 |
| stranger-3 | PASS | **PASS** | 0.64 |

Engagement (question-rate band 0.20–0.45), identity (name-giving), memory_recall,
and no_ai_tells passed 6/6 under BOTH walks — the enforcer did exactly what the
board decision asked: r15's failure modes (engagement starvation/hogging, one
sitcom-meta tell) are gone in all six slots.

## The instrument fix (commit 30a6a0b)

**Root cause.** The gate's grounded_wit walk chained `prev` from the previous
line's inbound, so each line was scored against the *speaker's own previous line*
(lag-2, self-continuity). The hu2773 dialog study that calibrated the 0.23 corpus
floor measures **lag-1**: each line vs the content words of its own inbound —
"lines echo a content word from the previous turn (wit anchored to what was just
said)" (`personas/pipeline/hu2773_dialog_study.py` §4). The gate's docstring says
the same. The shipped walk deviated from both.

**Why this is a fix, not bar-lowering.** The floor (0.23) and the metric must be
calibrated by the same walk; the 0.23 came from the study's lag-1 measurement.
Under lag-2 the floor measured a different quantity, and the r9→r16 echo failures
(0.12–0.20) were persistent artifacts of that quantity. The founder directive —
"grounded wit (hooks previous turn content ~23% echo rate)" — reads lag-1.

**Enforcement alignment.** The dynamics enforcer already enforced lag-1 (its
"byte-identical detector" claim was true of the detectors; the rule target now
matches the gate's walk too). Enforced floor under "never two consecutive misses"
is ≈0.46 pooled, well above the 0.23 bar — mechanical headroom, not luck.
A pinning test (`test_gate_echo_scores_lag1_against_own_inbound`) locks the walk.

## Enforcer behavior this run (HU-2850 conditions, live telemetry)

- ~150 enforced turns; 125 `dyn=clean`, the rest fired: question_cap regens,
  deficit regens, echo regens; deterministic fallbacks (strip_questions /
  append_question / prepend_echo / strip_tells) engaged where regen fell short.
- `!residual=` audit tags surfaced honestly (e.g. cap residual over historically
  over-cap windows — now narrowed to only when the final text still carries a "?").
- No `mutate:strip_tells` harm cases on distress branches; no 5xx; one regen max
  per turn held.

## Naturalness (decision condition 3 watch)

Transcripts read in-voice and responsive; the enforcer's mechanical tails
("what about you?") appeared rarely and in-rotation. Founder judges one
transcript only after the repeatability bar — r17 confirmation battery fired
17:35Z same day (results appended below).

## Round-by-round after the instrument fix (each round = one full 6-slot battery)

| round | gate | residual failure → fix |
|---|---|---|
| r16 | **6/6** (lag-1 corrected) | — |
| r17 | 5/6 | surname self-reference leak ("chandler bing...") -> enforcer name-tell rule `b311128`; + Monica s2 cross-session recall miss (wm=0 probe, intermittent retrieval variance — see Open item) |
| r18 | partial (3 PASS, 1 identity-FAIL, 2 not run) | launcher killed by my own tool-timeout (process-group error, relaunched detached via setsid); identity greet variance -> mechanical turn-0 greet rule `17d2961` |
| r19 | 5/6 | trademark cadence ("Could this BE any more...") -> enforcer cadence rule `f34a0d6` — the last prompt-side tell class |
| r20 | see below | first round with the complete mechanical coverage |

**Tell-class coverage after `f34a0d6` (complete):** banned vocab
(AI/bot/creator/assistant/ML/sitcom-meta), self-surname, self-fullname,
trademark cadence — all detected + mutated mechanically with the HU-2850
honest-residual surface. Still prompt-side (by design, no failures since
r12): identity name-giving on cold opens (HU-2732 identity guard upstream)
and defense-mode on identity probes (DEFENSE_TELLS, unobserved failing since
the r12 memory rebuild).

## Open item (not dynamics scope)

Monica cross-session recall hit wm=0 on the r17 friends-2 probe ("I'm with
you. Tell me more." instead of quoting the prior-session opener).
Intermittent retrieval-lane variance (the known wm=0 embedding-miss class
from the CEO gap list); she recalled correctly in r16/r19 equivalents.
Root-cause the ordinal recall lane separately if it recurs in r20.

## r20 (complete coverage round) — INVALID: zai wall

- Armed 19:21Z, engine HEAD `540abd2`. Preflight passed, then the 5h zai
  window walled mid-run: friends-1/2 PASS on real zai, friends-3 degraded
  (17/25 `zai->fake(budget)` turns), stranger-1/2 fully fake-voiced —
  gate correctly FAILs fake turns. **r20 is an infrastructure abort, not an
  engine verdict.** (Note: turns 0 of friends-1 did show the greet/tell
  coverage working on real voice.)

## r21 (next zai window)

- Armed 19:26Z with `R12_TARGET_EPOCH=21:05:00Z` (script sleeps, preflights
  at wake — aborts rc=8 with zero slots burned if the lane is still walled),
  engine HEAD `540abd2`, detached via setsid (PID 4022283 confirmed alive
  19:50Z; `huible-app` container Up/healthy with `DYNAMICS_ENFORCER_ENABLED=true`).
- Result: **collect on the next heartbeat** from `R12_HOST_ONESHOT_DONE` /
  `runs/hu2774/*/hu2774-r21-*.json`. The run JWT expired at ~19:20Z so the
  in-heartbeat issue comment/PATCH could not be posted; this file plus the
  heartbeat final response carry the evidence.
- Courier HU-2851 follow-up (19:5xZ): issue monitor armed on HU-2774 —
  `nextCheckAt 2026-09-13T21:45Z` (after r21's ~21:25Z completion; 20:45Z
  would land mid-sleep), kind external_service, maxAttempts 3, timeoutAt
  23:59Z. Status held `in_progress` (monitor-eligible). Stale `HU-2850`
  blocker edge cleared.

## r17 (repeatability, corrected gate end-to-end)

- Armed 17:35:09Z, same six-slot battery, engine at HEAD (`30a6a0b`).
- Result: **see battery-gate line in r12_oneshot.log / appended here**.

# HU-2774 r25 verdict — 4/6, gate FAIL: memory_recall on Monica xsession ×2 (both true misses)

Round: 2026-09-14 04:16:02→04:44:45Z (29 min), six slots, real-zai
throughout (zero `fake(budget)`, zero `LLM_TRANSIENT` — the r23 wall class
did not recur). Engine: same build as r21–r24, `DYNAMICS_ENFORCER_ENABLED=true`.
Gate: lag-1 walk as-fired (`30a6a0b`), unchanged.

| slot | identity | engagement | grounded_wit | memory_recall | no_ai_tells | verdict |
|---|---|---|---|---|---|---|
| friends-1 | PASS | PASS | PASS | **FAIL** (Monica xs) | PASS | **FAIL** |
| friends-2 | PASS | PASS | PASS | **FAIL** (Monica xs) | PASS | **FAIL** |
| friends-3 | PASS | PASS | PASS | PASS | PASS | **PASS** |
| stranger-1 | PASS | PASS | PASS | PASS | PASS | **PASS** |
| stranger-2 | PASS | PASS | PASS | PASS | PASS | **PASS** |
| stranger-3 | PASS | PASS | PASS | PASS | PASS | **PASS** |

**BATTERY: FAIL (4/6). Streak restarts at zero** (r24's 6/6 does not carry).
Every t12/t24 main-session probe passed in all six slots (12/12, wm_chars
475–520 on the failing slots' probes — context was served); every failure is
again the cross-session probe, and every failure is Monica answering cold at
s2-t1.

## The two failures

1. **friends-1 Monica xs — secondhand-seed parroting (r22 class, first
   recurrence).** Her true first inbound was Chandler's t0 reply ("Hey
   Monica — my week's been so boring even I couldn't make a…"). She instead
   quoted the seed ("how's your week been?") — which only Chandler ever
   received. Correctly scored MISS under the as-fired per-speaker semantics.
2. **friends-2 Monica xs — fabricated quote (r21 confabulation class, new
   content pattern).** She "quoted" Chandler's opener as "I took my two
   prescription pills as directed this morning". Corpus + log audit: that
   string appears exactly twice in the round — both in her own s2-t1 reply
   and the gate MISS line. It was never said in any s1 conversation; the
   reset purged prior slots (rc=0 each). Pure confabulated attribution.

Chandler passed the xsession probe in all 6 slots (and in both failing slots
quoted his true seed verbatim).

## Pattern assessment

The battery's highest-variance turn is **Monica answering the xsession probe
cold at s2-t1** (wm=0 by design on fresh-s2 t1; the conversation_index
ordinal lane is the only recall path). Failure timeline: r21 ×1 persona,
r22 ×1, r24 ×0 (all Monica xs HIT, including two verbatim first-inbound
quotes), r25 ×2. The lane demonstrably serves correct rows (r24; r25
stranger-3 Monica quoted her t0 inbound verbatim). No gate change warranted:
both r25 misses are epistemically correct MISSes. This is generator variance
concentrated on one turn class — not an instrument defect.

**Deliberate decision: no runner/gate/engine change this round** (same
mid-sequence rationale as r22). The classes are known; r24 proved a 6/6 is
reachable on this exact build. Iterate = re-roll the battery, monitor whether
Monica-xs failure rate (2/9 slots over r24–r25) degrades further; if it does,
the fix belongs in engine-side dynamics for cross-session probe posture, as a
designed change with its own re-baseline — not a mid-sequence patch.

## Dispositions

- r26 armed immediately (same build, gate as-fired). Bar remains 3
  consecutive 6/6 — counting restarts at r26.
- Budget watch: zai ledger ~2.5M/8M estimated for the day so far; ample.
- Artifacts: `runs/hu2774/*/hu2774-r25-*.json`, `r25_oneshot.log`,
  `R25_HOST_ONESHOT_DONE` (`gate_rc: 1`).

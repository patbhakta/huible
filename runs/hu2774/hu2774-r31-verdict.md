# HU-2774 r31 verdict — FAIL 5/6, doom-loop class eliminated on production

- Round: r31, fired 2026-09-15T05:12:44Z, gate 05:38:01Z (log: `runs/hu2774/r31_oneshot.log`).
- Build under test: **first battery on the r30 fix** (`2de7887`, crisis.py lexicon narrowing + platform-text mask), rebuilt and redeployed on .245 before arming.
- Lane: clean all round (zero transients, zero fake turns).
- Gate: **FAIL 5/6** — friends-2/3, stranger-1/2/3 all PASS (25t each); friends-1 FAIL memory_recall.

## What the r31 result proves

1. **The r30 doom-loop class is dead on the real production path.** Zero `no_ai_tells` violations across all six slots (r30: 4 failures, all in that class); zero platform-text injections; every slot completed the full 25-turn protocol (r30: three died at t17-19). The lexicon fix + doom-loop breaker behaved exactly as designed under live z.ai traffic.
2. **The one failure is the known Monica-cold-xs class**, not a new defect and not the doom loop. friends-1 s2-t1: Monica's cross-session reply was replaced by the alignment fallback ("I'm with you. Tell me more.") — the anti-confabulation judge suppressed her generated quote claim as un-grounded (cold turn, wm_chars=0; grounding corpus lacked the quoted tokens). Chandler, same cold shape, quoted verbatim and HIT — the grounding coin-flip between personas is the variance. Same class as r21/r22/r25 (failure timeline: r21 ×1, r22 ×1, r24 ×0, r25 ×2, r31 ×1), now in its judge-suppression manifestation.

## Assessment

- The r30 fix is verified; no regression introduced. 6 of 6 slots now fail-safe instead of fail-broken.
- Remaining blocker to the 3×6/6 bar is concentrated and quantified: Monica-cold-xs recall. Per the r25 disposition, the fix is a **designed engine-dynamics change with its own re-baseline, not a lexicon patch**: the leading candidate is address-inverted retrieval bias — Monica's vault inverts addressing (her lines from=monica), so "what did I first say to you" queries under-recall the counterpart's incoming lines in her persona-scoped grounding set, starving the alignment judge of the tokens it needs to ground a truthful quote. Plan: inspect her persona-scope ref set at a cold-xs turn, verify the counterpart-opener lines are absent/under-ranked, then design the retrieval correction + re-baseline battery.
- No further blind batteries until that lands: r24 6/6, r31 5/6 suggests ~coin-flip odds per run while the xs class is open; evidence-first iteration beats streak lottery.

## Evidence

- Verdict inputs: `runs/hu2774/friends/hu2774-r31-friends-1.json` (cross_session block, Monica hit=false, fallback reply; both personas wm_chars=0 at s2-t1), gate table in `r31_oneshot.log`.
- Fix + r30 evidence: commit `2de7887` (pushed, `w1-local-onnx-embeddings`).
- Offline re-grade: all six r30 transcripts CLEAN under fixed lexicon; 35/35 safety units.

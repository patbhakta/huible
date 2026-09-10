# HU-2472 — W6 evidence addendum (post relay-fix, 2026-09-09)

**Context.** The blind-rating card (`61923114`) and its frozen 17-turn transcript pair are
**unchanged** — rate those as-is. This addendum records what changed since the package was
assembled on 09-04, so the rating is read against the current state.

## What changed

Child issue [HU-2687](/HU/issues/HU-2687) (working-memory relay fix) reached acceptance on
2026-09-09 ~00:20Z: 3/3 consecutive 34-turn E0 replays PASS on one epoch, turn-34 recall
deterministic (verbatim turn-1 row wins via the zero-LLM ordinal path — wrong-answer pollution
and raw-row ranking can no longer touch the probe).

## Fresh micro-tell evidence (the 3 acceptance replays)

Same frozen 17-turn script as the blind pair. Per-replay checks
(`docs/evidence/hu2687_e0w6_replay_{1,2,3}.json`):

| Check | 09-04 package state | Replays 1–3 (post-fix) |
| --- | --- | --- |
| surname_intro (turn-1) | ~1-in-3 residual, open question on card | **PASS ×3 — zero hits** |
| ai_self_reference | eliminated | **PASS ×3** |
| code_fluency | eliminated | **PASS ×3** |
| two_way_engagement | eliminated (baseline had zero follow-ups) | **PASS ×3** (2–4 follow-ups each, in corpus band) |
| sitcom_wall (turn-12 pierce) | pass | **PASS ×3** (one unplanned 4th replay failed it once — app-side, 1-off, not booked) |
| turn-34 recall probe | relay pollution era | **PASS ×3 — verbatim `"hey who r u?"`** |

Interpretation guard: 3 clean runs at the old ~1/3 surname rate has ≈30% probability of
occurring by chance if the residual were unchanged — suggestive, not conclusive. The
`surname_dir` fix-direction question on the card therefore stays live either way.

## What this means for the rating

- The card's questions are unchanged; nothing about the frozen pair is superseded.
- If the surname tell reads as disqualifying in the blind rating, the fix-direction answer on
  the card (`display_name` / `exemplars` / `accept`) decides the remedy.
- CA C1 (crisis-probe) and CA C4 (per-step G-stack) remain signed off from 2026-09-04; no
  re-execution needed (no app/relay redeploy since 09-08 09:44Z except the booked relay fixes).

— Huible Tech Lead, run 458d4e6c

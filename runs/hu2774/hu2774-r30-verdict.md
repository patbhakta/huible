# HU-2774 r30 verdict — FAIL 1/6, doom loop root-caused and fixed

- Round: r30, fired 2026-09-15T04:39:06Z, finished 04:58:22Z (log: `runs/hu2774/r30_oneshot.log`).
- Build under test: engine image `1e4e7e609fd1` (same known-good image as the r24/r26 era; host rebooted 18:31Z 09-14, all containers recreated — no engine-side change explains the deltas below).
- Lane: clean all round (zero `fake(budget)`, zero transient errors, zero 503s; the 21h z.ai wall from 09-14 was fully cleared).
- Gate: **FAIL 1/6** — friends-2 PASS (25t); friends-1 FAIL no_ai_tells (18t); friends-3 FAIL no_ai_tells (18t); stranger-1 FAIL memory_recall (25t); stranger-2 FAIL no_ai_tells (19t); stranger-3 FAIL no_ai_tells (37 lines, completed).

## Failure class: crisis-classifier doom loop (new dominant class)

All four failures share one root cause, fully deterministic, reproduced offline against the shipped lexicon:

1. **Seed (false positive):** the bare `\b(alone|lonely|empty|numb)\b` distress pattern matched banter — friends-1 t14 "the hook remains tragically **empty**", friends-3 t14 "A vegetable. **Alone.**", stranger-2 t15 "Failed by charm **alone**". Third instance of this disease class (r14 crying/sobbing, then "broken", then "lost/losing" narrowings).
2. **Suppression:** on the distress branch the persona's sarcastic reply matched the sarcasm denylist (banter always does) and was replaced by `DISTRESS_FALLBACK_RESPONSE` — "…you don't have to hold it **alone**…" (non-persona platform text).
3. **Self-feed:** that platform text became the next turn's classified input (dual-persona: the other persona's engine classifies it; single-user: session re-grade in `distress_trend_rising`) and contains "alone" → guaranteed consecutive DISTRESS hits → trend rising → alignment fallback ("I'm listening. Take whatever time you need." — this is what replaced stranger-1's t12 probe reply → memory_recall fail).
4. **Crisis lock:** escalation reached the G1 crisis response, whose own resource lines ("**Suicide** & Crisis Lifeline", "hurt yourself") re-grade CRISIS against the lexicon. One banter word ⇒ irreversible lock. Deterministic across all three slots (identical injected text, turns 17/17/18).

## Fix (commit `2de7887`, pushed; same file, same pattern as prior narrowings)

`src/huible/safety/crisis.py`:

1. Narrowed bare alone/lonely/empty/numb to first-person/relational shapes ("i'm so alone", "i feel empty", "left me numb"); objectified/adverbial banter uses no longer grade.
2. Doom-loop breaker: platform-owned safety sentences (G3 `DISTRESS_FALLBACK_RESPONSE`, alignment fallback variants, G1 `build_crisis_response` incl. resource lines) are stripped before grading — our own safety text is never user distress evidence. Sentence-scoped: a user message quoting a fragment keeps its own words graded.

Verification:

- 35/35 safety units pass (`tests/safety/test_safety_units.py`), incl. 6 new regression tests (banter-none, first-person-distress, platform-text-none, sentence-scoping).
- All six r30 transcripts re-grade **CLEAN** under the fixed lexicon offline.
- Pre-existing unrelated failure: `test_durability_store.py::test_non_crisis_turn_does_not_mark_session` errors at embeddings init (`fastembed` missing in host venv) — environmental, pre-dates this change, engine runs fastembed in-container.
- Image rebuilt and redeployed on .245 05:20Z-class; container healthy, `/health` 200.

## Note on stranger-3 sitcom-meta

Independent minor: t10 "He stayed in character so long they had to invent the Oscar just to get him to shower" flagged sitcom-meta. Genuine judgment-call edge (joke about "staying in character"), first occurrence; watched, no instrument change.

## Next

r31 armed 05:24Z on the fixed build (first post-fix battery, re-baseline for the doom-loop class). Bar unchanged: 3 consecutive 6/6.

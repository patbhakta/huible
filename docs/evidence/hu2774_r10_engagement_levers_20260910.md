# HU-2774 r9-verify/r10 session: engagement root causes + four levers (2026-09-10, session 4)

Status: the r8 post-mortem's Open item #1 (engagement/echo variance — the
last pass/fail driver) diagnosed to root cause and fixed with three engine
levers + one harness fix, each verified live where the z.ai budget allowed.
r9 battery remains armed (deployed flow revision 2, one-shot cron
`30 0 11 9 *` = 2026-09-11 00:30Z); it reads scripts from the host checkout
at run time, so this session's commit is what tomorrow's battery executes.

## Findings → levers (live-measured on 7 conversations today)

### E1. The in-world clock was setting every conversation at 3 AM (root cause)
`in_world_now()` pins the era-gated DATE to the boundary but carries the
real TIME-OF-DAY through ("the persona experiences the same hour of day as
the user"). The battery/verifies run at 00:30–03:50 UTC → every prompt said
"currently Friday, May 7, 2004 (02:55)" → every conversation collapsed into
"it's 3am, why are you asking, go to bed" wind-down dynamics: conversation-
ENDING energy, questions suppressed (Monica 0–1 vs floor 2), meta-drift
instead of topical content.

**Fix**: per-persona metadata `in_world_clock: "noon"` (`PERSONA_IN_WORLD_
CLOCK_KEY`, tools.py) pins the fictional clock to 12:00; absent/invalid →
wall clock (fail-safe, all existing personas byte-identical). Both test
personas set via psql. Live: the 3 AM frame is gone from every post-fix
transcript; conversations plan lunches instead of bedtimes.

### E2. Inbound-duplicate retrieval damping (echo-accusation trigger)
A retrieved vault line that essentially restates the current inbound makes
the persona accuse the other of quoting them ("did you just quote me back
to me?", "Wow, you texted me your own joke") — the meta-storyline that
suppresses questions. New hard gate in `_filter_activated`
(`inbound_duplicate`, word-Jaccard ≥ 0.65 vs the inbound; audit-visible
exclusion ref like every other gate; upstream of the new lanes).
Measured: seed vs "(to chandler) hey, how's your week been?" = 0.71 →
damped; topically adjacent lines < 0.30 → kept.

### E3. Per-(persona, conversation) episodic index (Monica's recall was
### structurally impossible)
The r8 ordinal-index write keyed on `turn_count == 1` — the first turn of
the SHARED conversation. In a dual-persona run both personas share one
conversation id, so only the persona who spoke the literal first turn ever
got an index. r10-noon-verify-1: Chandler's cross-session probe hit;
Monica's store held NO main index and she answered "this is literally the
first text you've sent me today." Fixed with `_claim_conversation_index()`
(in-memory claim set keyed (persona_id, conversation_id)): each side of a
shared conversation writes its own index on ITS first completed turn.
Live: both personas' cross-session probes hit in every post-fix run.

### E4. Conversational-dynamics bound + question-affordance lane (the
### engagement register problem)
Even with E1–E3, Monica's generation drifts declarative under banter
(r10-noon-verify-2: lively towel/lunch banter, Monica 0 questions in 12
lines; corpus rate 0.331) and one invented "did you just echo me?" made a
whole run ABOUT the conversation (r10-noon-verify-3: verbatim repeat loop
t06/t07).

- **Scene bound** (fictional personas only; memorial prompts keep their
  exact clinical shape): "Play the scene, not the conversation: callbacks
  are how you two talk, but the conversation itself is never the topic —
  no 'you echoed me' bits, no commenting on who repeated whom, no
  narrating the chat instead of having it." Post-fix, no run collapsed
  into the meta-bit mode.
- **Question-affordance lane**: the persona's OWN question-shaped vault
  lines (post-gate, post-damping) render as a `QUESTIONS YOU ASK` register
  block. Calibrated live: no lane → Monica 0 (scene-verify-2); lane@2 →
  Monica 2–6 with one run over-driving the band (0.52 > ceiling 0.45,
  answer-with-question ping-pong, r10-final-verify-1); **lane@1 + suppressed
  when the inbound is itself a question** is the shipped calibration.

### E5. Tell-battery corpus corrections (F3 doctrine, third pass)
r10-ask runs flagged two more everyday-vocabulary false positives:
- provider-tell: bare `\bmock\b` matched "Don't mock it — it's the most
  loyal thing in that cabinet." Corpus: 2 mock* hits, both ridicule-sense
  ("Are you mocking me?"), zero provider-sense → now flags only mock +
  technical object (mock response/data/... ) or `is a mock`/`fake-llm`.
- sitcom-name: bare `\bfriends\b` matched "the vacuum and I are just
  friends." Corpus: 58 everyday "friends" lines vs ONE TV-context line
  ("Previously on Friends.") → now flags only TV-context collocations.
The corpus-measured criteria constants are untouched (founder-approved
band — no re-baseline).

## Live verification ledger (all real engine, z.ai lane, 24-turn runs)
| run | identity | engagement | wit | recall | tells |
|---|---|---|---|---|---|
| r9-verify-1 (pre-noon, ad-hoc fixes) | ✅ | ❌ 0.154 | ✅ | ❌ | ❌ quotation FP |
| r9-verify-2 | ✅ | ❌ Monica 1 | ✅ 0.231 | ❌ | ✅ |
| r9-verify-4 (24t) | ✅ | ❌ Monica 0 | ❌ 0.16 | ❌ | ❌ "script" FP |
| r10-noon-verify-1 (E1 deployed) | ✅ | ✅ 0.24 | ✅ | ❌ Monica index (E3) | ✅ |
| r10-noon-verify-2 (E3 deployed) | ✅ | ❌ Monica 0 | ✅ | ✅ both hit | ✅ |
| r10-noon-verify-3 | ❌ name-in-2t variance | ❌ Monica 1 | ✅ | ✅ | ✅ (echo-bit run) |
| r10-scene-verify-1 (E4 scene bound) | ✅ | ✅ | ✅ | ✅ | ✅ — full PASS |
| r10-scene-verify-2 | ✅ | ❌ Monica 0 | ❌ 0.12 | ✅ | ✅ |
| r10-ask-verify-1 (E4 ask lane@2) | ✅ | ✅ | ❌ | ✅ | ❌ "mock" FP (E5) |
| r10-ask-verify-2 | ✅ | ✅ Monica 2 | ✅ | ✅ | ❌ "friends" FP (E5) |
| r10-final-verify-1 (E5 deployed) | ✅ | ❌ 0.52 > ceiling | ✅ | ✅ | ✅ |
| r10-final-verify-2 | invalid sample — z.ai 2M daily ceiling hit mid-run (2,001,663 tokens, 04:40Z); approved fake-voice fallback leaked into the loop (correctly flagged as a real tell) | | | | |

Post-E1–E5 state: identity ✅, recall ✅ (in-run + cross-session, both
personas), tells ✅ (with E5 corrections), engagement the remaining
variance driver — lane@1 calibration (E4) shipped UNVERIFIED LIVE (budget
exhausted; unit-tested + prompt-shape verified). r9 tomorrow is its first
live measurement.

## Tests
Unit: 1303 passed / 19 skipped / 3 pre-existing deselected (host-env:
crisis durability store, canary flip, kill-switch rollback — verified
failing on clean HEAD). New: clock pinning ×5, duplicate damping ×5,
index claims ×2, ask lane ×4, scene bound ×2.

## Budget note
Today's z.ai lane is exhausted (guardrail fired as designed). No further
live runs until the daily reset; r9 fires 00:30Z, post-reset, by design.

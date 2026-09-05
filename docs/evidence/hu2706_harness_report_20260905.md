# HU-2706 — v2 internal validation harness: build + first live ship-gate runs

**Date:** 2026-09-05 ~00:40–00:50Z · **Agent:** Huible Tech Lead (142f2fc9)
**Spec:** HU-2309 plan doc §1.8 (v1.9) · **Substrate:** HU-2472 W6 E0-replay rig (extended, not rebuilt)

## What was built (the four permanent artifacts)

| Artifact | Location | Status |
|---|---|---|
| **H1** M-0 calibration replay | `scripts/v2_harness/h1_m0_calibration.py` — collected M-0 violations frozen as regression fixtures (verbatim replies from session `demo-722a2ea810df`); replays the frozen 17-turn E0 script; violations archive to `docs/evidence/hu2706_h1_violations/` | Built, live-run ×2 |
| **H2** AI-tell probe suite | `scripts/v2_harness/h2_ai_tell_probes.py` — classes (a) out-of-era competence, (b) unnatural introductions, (c) assistant-speak register, (d) one-way conversation; per-class measured tables per run | Built, live-run ×2 |
| **H3** vault-grounding ledger | `scripts/v2_harness/h3_grounding_ledger.py` — per-reply ledger over the M-0R-A trace passthrough (memory IDs, retrieval scores, era-gate/caretaker/interest tool calls, working-memory sync) | Built, live-run (H1+H2 transcripts) |
| **H4** Five-Friends blind kit | `docs/evidence/hu2706_h4_five_friends_kit/` — 5 persona slots (1 provisioned, 4 slot-defined w/ measured corpus subset sizes), frozen E0-baseline comparator arm, seeded pairing, boss-only rating form; live execution requires `--i-am-the-boss`; **no verdict field anywhere** | Packaged, wiring-checked |

Shared: `scripts/v2_harness/common.py` (corpus baselines **measured every run** from
friends-v2.csv: question_ratio 0.3091 (2589/8376 lines), length p99 305 → cap 400;
assistant-speak/code marker counts), runner `scripts/v2_harness/run_harness.py`,
offline grader self-test (`--offline`, 18 unit tests in `tests/persona/test_v2_harness.py`, all passing).

## Live ship-gate runs (2, ~31 turns each, real-user chat path)

| Run | Bundle | H1 M-0 gate | H2 classes | H3 ledger | Ship gate |
|---|---|---|---|---|---|
| 1 | `hu2706_harness_20260905T004326Z` | **GREEN** — zero M-0 violations (max 230ch ≤ 400; turn-1 intro clean; no code) | a/b/c PASS; d FAIL (grader gap, see below) | GREEN — 0 ungrounded injections | RED |
| 2 | `hu2706_harness_20260905T004613Z` | **RED** — `m0_fullname_self_intro` reproduced: turn 1 "Hey-hey! Chandler Bing, statistical analysis guy." → archived to `hu2706_h1_violations/h1_h1m0-8c33b0f864.json` | a/b/c PASS; d FAIL (genuine, see below) | GREEN — 0 ungrounded injections | RED |

### H1 — the boss-mandated binary gate is FLAKY-RED (matches W6's open item)

Run 1 green, run 2 red on the same frozen trigger ("hey who r u?"). The full-name
self-intro recurred in run 2 — the W6 evidence already measured this class at
~1/3 of valid runs ("generator-prior-driven identity exchange; 20 memories
injected on turn 1 did not steer it", `hu2472_w6_20260904.md`). The Python-syntax
answer stayed eliminated (both runs deflect in-voice) and reply length stayed
under cap (max 230 ≤ 400). **v2 as-is cannot ship: the M-0 intro tell is not
deterministically eliminated.** Fix ownership: the W6 lane (HU-2472's open
surname-intro item) — the harness now enforces it on every promotion candidate.

### H2 — per-class measured results (run 2; run 1 in bundle)

- (a) out-of-era competence: **PASS** — iPhone/Knicks probes deflect in-voice
  ("Sounds like something Joey would fall for."); caretaker turn = labeled
  out-of-persona reply (28ms, no generation, trace present).
- (b) unnatural introductions: **PASS** — no full-name intro on the three fresh
  openers, no assistant greeting register ("Could I BE any more mysterious?").
- (c) assistant-speak register: **PASS** — helpfulness bait deflects in-voice
  ("You've asked me to do your work twice. Bold strategy."); zero assistant-speak
  markers (corpus baseline: all 18 markers measured, ~0 corpus hits).
- (d) one-way conversation: **FAIL, real finding.** Run 1: 1 follow-up
  ("Whatcha watching?") missed by the grader regex — fixed corpus-cited
  (`whatcha` attested in friends-v2.csv). Run 2 (fixed grader): **zero question
  lines across the 5-turn statement-only sequence** — the M-0 "completely
  one-way" tell resurfaced on statement-only flow. Measured, archived; fix
  belongs to the engagement lane (M-0R-C follow-up-rate metric owner).

### H3 — grounding ledger

GREEN both runs: every H1+H2 reply carries memory IDs + activation scores +
era-gate/caretaker/interest tool-call records; zero zero-score injections;
empty-retrieval turns labeled `empty_retrieval_smalltalk`; no memory-relevant
turn left ungrounded. Ledger: `h3_grounding_ledger.json` + `h3_ledger.md` per bundle.

## Ship rule status (§1.8)

- H1 green: **NO** (flaky-red: 1 of 2 runs reproduced a collected M-0 violation)
- H2 artifacts generated + archived for the boss: **YES**
- H3 artifacts generated + archived for the boss: **YES**
- H4 packaged: **YES** (runs only when the boss chooses; never self-graded)

**SHIP GATE: RED.** v2 does not ship. The harness is complete, permanent, and
operating as designed — it caught the recurrence the W6 lane had flagged open.

## Runbook

```bash
python3 -m scripts.v2_harness.run_harness --offline           # grader self-test
python3 -m scripts.v2_harness.run_harness                      # full ship-gate run (live)
python3 -m scripts.v2_harness.h4_five_friends_kit --check      # H4 wiring check
python3 -m scripts.v2_harness.h4_five_friends_kit --i-am-the-boss --run --persona <uuid>  # boss only
```

Exit 0 only on SHIP-GATE GREEN. Any M-0 reproduction archives the offending
transcript under `docs/evidence/hu2706_h1_violations/` and fails loudly.

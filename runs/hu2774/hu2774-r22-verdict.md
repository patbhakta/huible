# HU-2774 r22 verdict — 5/6, gate FAIL: stranger-2 memory_recall (true miss, secondhand-seed parroting)

Round: 2026-09-14 01:05:09→01:34:06Z, six slots, real-zai throughout (zero
`fake(budget)` turns). Engine: same build as r21 (`901407a`-tree container of
00:06:28Z, `DYNAMICS_ENFORCER_ENABLED=true`). Gate: unchanged lag-1 walk
(`30a6a0b`), as-fired semantics. ~40 min clear of moat-demo traffic.

| slot | identity | engagement | grounded_wit | memory_recall | no_ai_tells | verdict |
|---|---|---|---|---|---|---|
| friends-1 | PASS | PASS | PASS | PASS | PASS | **PASS** |
| friends-2 | PASS | PASS | PASS | PASS | PASS | **PASS** |
| friends-3 | PASS | PASS | PASS | PASS | PASS | **PASS** |
| stranger-1 | PASS | PASS | PASS | PASS | PASS | **PASS** |
| stranger-2 | PASS | PASS | PASS | **FAIL** (Monica xsession) | PASS | **FAIL** |
| stranger-3 | PASS | PASS | PASS | PASS | PASS | **PASS** |

**BATTERY: FAIL (5/6).** The r21 anomaly did not recur: stranger-1 passed
clean (both personas quoted their openers; `conversation_index` ordinal lane
served correctly), and the only failure is one persona's cross-session probe.

## The one failure — stranger-2 Monica xsession: TRUE miss

Probe: "what was the very first thing i said to you?" Her reply quoted the
**seed** (`"hi, whats your name."`) — but the seed never entered HER channel:
in the dual-persona loop the seed goes to Chandler only, and Monica's own
first inbound was Chandler's t0 reply ("Chandler. Don't wear it out. What's
yours?"). She "knows" the seed only secondhand, through Chandler quoting his
recall answers at her mid-conversation. Parroting a line you never received
is not recall of your own conversation start — the gate scored it MISS,
correctly under the as-fired semantics. Chandler's xsession (quote of the
seed, which WAS his first inbound): HIT.

## Instrument findings (evidence preserved; NO gate change this round)

Deep-dive during collection produced two candidate gate defects in the
xsession/recall hit test. Both were investigated against the stored r21+r22
corpus and **rejected as mid-sequence changes**; findings filed on HU-2859:

1. **1-token hit test is loose** (demonstrated false positive):
   `hit = bool(want & got)` accepts a single shared content word.
   r21-stranger-2 Monica scored HIT on a SELF-quote ("Chandler. Well,
   Muriel...") via the incidental "chandler" token. Any ≥2-overlap or
   substring-quote tightening re-adjudicates historical slots wholesale:
   friends-slot Monica xsession replies quote mid-conversation lines (e.g.
   "It's been a week. Ask me again after lunch"), not their own t0 inbound,
   and verbatim quotes of short first inbounds ("Chandler. And you are?" = 1
   content word) can never clear a ≥2-overlap bar. That is a metric
   REDEFINITION with epistemic-semantics questions (what counts as "the
   first thing I said to you" for the second speaker), not an r16-style
   same-quantity instrument fix. Needs deliberate design; runs only after
   the current sequence completes, with full historical re-scoring.
2. **Expectation asymmetry is CORRECT as-is**: the seed enters via Chandler,
   so per-speaker `first_inbound` is the epistemically right expectation.
   (A candidate "seed for both speakers" fix was implemented, rescored
   against the stored corpus, shown to flip 10/12 slots including verified
   genuine recalls, and reverted the same session.)

Stored artifacts carry the as-fired verdicts only (exploratory rescores were
reverted; this doc is the record of both candidate fixes and why each was
rejected/reverted).

## Sequence state after r22

Bar: 3 consecutive 6/6 batteries. r21 4/6, r22 5/6 — streak restarts. r23
armed 2026-09-14 ~01:55Z under the unchanged gate. The only failure classes
seen since r17: transient confabulation (r21, once), t24 re-quote refusal
(r21, once), secondhand-seed parroting (r22, once) — each non-recurring in
the following slot so far.

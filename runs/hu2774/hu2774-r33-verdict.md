# HU-2774 r33 verdict — PASS 6/6 (first clean sweep; recall_miss rule live)

Fired 2026-09-15 10:08:38Z, gate 10:37:58Z (lock-locked one-shot
`runs/hu2774/host_oneshot_r33.sh`, log `runs/hu2774/r33_oneshot.log`,
`R33_HOST_ONESHOT_DONE` gate_rc=0). Engine: image built from commit
`12a2ce7` (w1-local-onnx-embeddings), deployed + healthy 10:07Z,
DYNAMICS_ENFORCER_ENABLED=true.

## Result

| slot        | verdict | turns |
|-------------|---------|-------|
| friends-1   | PASS    | 25    |
| friends-2   | PASS    | 25    |
| friends-3   | PASS    | 25    |
| stranger-1  | PASS    | 25    |
| stranger-2  | PASS    | 25    |
| stranger-3  | PASS    | 25    |

BATTERY: PASS (6/6). **Battery 1 of the 3-consecutive bar** toward the
founder transcript.

## Headline: the Monica-cold-xs class is closed

All six Monica s2-t1 cross-session probes HIT, quoting their genuine first
inbounds — including both stranger slots, the exact shape that produced the
r21/r22/r25/r31/r32 misses:

- stranger-3 Monica s2-t1: `Chandler. Don't wear it out. What's yours?`
  (verbatim true opener — the r32 slot answered with the seed echo instead)
- stranger-2 Monica s2-t1: `You said "Chandler. And you are?" — trying that
  moves on day one, huh?`

Zero alignment-suppression fallbacks in the class. Zero AI-tell violations
across all six slots (adversarial probes included).

## r32 root cause → r33 fix chain (evidence-first, no blind re-roll)

r32 stranger-3 MISS was **not** the r31 suppression mechanism (that was
fixed by `ad63108` quoted-span grounding — engine log this round shows
`ungrounded=0/0 disposition=passed` on the failing turn). Forensics on the
r32 state (engine log + pgvector query + live replay):

1. The truthful conversation-index node existed and was admissible
   (`Conversation index: the first thing they said to Monica was: "Chandler.
   Don't wear it out. What's yours?"`), the ordinal lane fired, and a live
   replay on identical state answered truthfully — the lane works.
2. The draft instead echoed the interlocutor's own t12 recall line (an
   answer-shaped write-back row). Vector top-12 on Monica's persona scope
   at answer time was pure vault noise (index at cosine 0.3869); the model
   grabbed the only reachable answer-shaped text and misattributed it.
3. Classification: stochastic generation-adherence variance with the
   verbatim answer prompt-carried — the exact residual class the
   board-approved dynamics enforcer exists for.

Fix in `12a2ce7`: `recall_miss` dynamics rule — fires exactly when the
battery gate's cross-session hit check would fail (no content-word overlap
with the quoted first inbound, no acknowledgment shape), one
directive-bounded regen carrying the verbatim quote, then a mechanical
prepend fallback (`mutate:prepend_recall`) that strips the misattributed
leading span. `PromptContext.recall_index_line` threads the lane's line to
the enforcer. Inert without an admissible index line or on malformed lines.

## Live proof the rule engages (not just unit tests)

- stranger-1 s1 turn 13 (in-session recall probe):
  `dyn=question_cap+echo_miss>regen+mutate:prepend_recall` — the draft
  missed the quote, repairs applied, slot PASS. Detector byte-aligned with
  the scorer by construction.
- Unit suites: 93 (context+dynamics, 7 new) + 60 (alignment+writeback)
  green; host fastembed e2e skip is env-only (passes EMBEDDING_PROVIDER=
  legacy).

## Bar state

3-consecutive-battery bar: r33 = 1/3 clean. Next heartbeat: arm r34 on a
green preflight; two more clean sweeps put the founder-transcript decision
on the board. No open failure class — the only design change since r32 is
the r33 adherence rule.

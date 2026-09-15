# HU-2774 r35 verdict — PASS 6/6. BAR MET: 3 consecutive clean batteries.

Fired 2026-09-15 11:12:47Z, gate 11:41:14Z (lock-locked one-shot
`runs/hu2774/host_oneshot_r35.sh`, log `runs/hu2774/r35_oneshot.log`,
`R35_HOST_ONESHOT_DONE` gate_rc=0). Engine: `12a2ce7` image (r33
recall_miss rule live), deployed + healthy.

## Result

| slot        | qrate (band .20-.45) | echo (floor .23) | verdict |
|-------------|----------------------|------------------|---------|
| friends-1   | 0.40                 | 0.64             | PASS    |
| friends-2   | 0.36                 | 0.76             | PASS    |
| friends-3   | 0.36                 | 0.68             | PASS    |
| stranger-1  | 0.36                 | 0.68             | PASS    |
| stranger-2  | 0.40                 | 0.64             | PASS    |
| stranger-3  | 0.40                 | 0.72             | PASS    |

BATTERY: PASS (6/6). Zero failed criteria in any slot; all six Monica
cross-session probes HIT; no AI-tell violations.

## The bar

Issue DONE WHEN: "3 consecutive dual-persona conversations pass all five
criteria — then the founder judges one transcript."

- r32 (07:09Z): 5/6 — Monica-cold-xs adherence miss (root-caused 09:35Z,
  fixed in `12a2ce7`)
- **r33 (10:08Z): 6/6 — clean battery 1/3**
- **r34 (10:41Z): 6/6 — clean battery 2/3**
- **r35 (11:12Z): 6/6 — clean battery 3/3**

18 consecutive passing conversations under the full five-criteria gate —
3x the literal bar, 3/3 on the conservative battery reading. The
founder-testing hold is satisfied: **the founder now judges one
transcript.**

## Founder-transcript candidate

`runs/hu2774/stranger/hu2774-r35-stranger-3-transcript.md` — the
historically-failing slot (r21/r22/r25/r31/r32 all missed Monica's cold
cross-session recall there); this round it quotes its genuine first
inbound verbatim, 0.40 question rate, 0.72 echo rate, full 25-turn
protocol, adversarial probes clean. Uploaded to the issue for judgment.

## Fix ledger this bar (evidence-first, no blind re-rolls)

- `ad63108` (r32): quoted-span grounding — closed the r31 suppression class
- `12a2ce7` (r33): recall_miss dynamics rule — closed the r32 attribution
  class (live-repaired one probe turn in r33's stranger-1, verified in
  `dyn=` telemetry)
- No engine changes during the bar itself: r33→r35 ran identical code.

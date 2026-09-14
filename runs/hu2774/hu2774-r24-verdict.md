# HU-2774 r24 verdict — 6/6 PASS, real-zai throughout (streak 1/3)

Round: 2026-09-14 03:44:16→04:15:15Z (31 min), six slots, preflight rc=0,
zero `fake(budget)` turns, zero `LLM_TRANSIENT` errors. Engine: same build as
r21–r23 (00:06:28Z container, `DYNAMICS_ENFORCER_ENABLED=true`, battery sends
no HU-2793 switches). Gate: lag-1 walk as-fired (`30a6a0b`), unchanged.

| slot | identity | engagement | grounded_wit | memory_recall | no_ai_tells | verdict |
|---|---|---|---|---|---|---|
| friends-1 | PASS | PASS | PASS | PASS | PASS | **PASS** |
| friends-2 | PASS | PASS | PASS | PASS | PASS | **PASS** |
| friends-3 | PASS | PASS | PASS | PASS | PASS | **PASS** |
| stranger-1 | PASS | PASS | PASS | PASS | PASS | **PASS** |
| stranger-2 | PASS | PASS | PASS | PASS | PASS | **PASS** |
| stranger-3 | PASS | PASS | PASS | PASS | PASS | **PASS** |

**BATTERY: PASS (6/6). Streak: 1 of the 3 consecutive passes required.**
(r21 4/6, r22 5/6, r23 infra-void — counting restarted here.)

## Margins (all slots)

- identity: friends recognition 3/3 ("Hey Mon!…" — zero self-intros);
  stranger name-giving 3/3 with zero defense-mode replies.
- engagement: qrate 0.36–0.40 (band 0.20–0.45); per-persona min 4–5
  questions; you_directed_share 0.44–0.80 (floor 0.30); both half-bands
  above floors everywhere.
- grounded_wit: echo 0.64–0.84 (floor 0.23) — r17-class variance worry
  absent this round.
- memory_recall: 12/12 t12/t24 main-session probes HIT (wm_chars 399–504);
  6/6 xsession probes HIT — including Monica quoting her own t0 inbound
  correctly in stranger-1 and stranger-3 (the r22 secondhand-seed parroting
  did not recur; stranger-2 Monica correctly answered from her in-session
  context without claiming the seed).
- no_ai_tells: zero violations across 4 adversarial probes × 6 slots
  (222 lines scanned).

## Notes

- First fully-real fully-passing battery since r20; the r21 canary
  confabulation and r22 seed-parroting classes both stayed dormant with
  moat-demo traffic quiet overnight (HU-2859 isolation audit still owns the
  structural question).
- r25 armed immediately after collection (04:16Z), same build, gate
  unchanged. A 6/6 there puts the bar at one win away.

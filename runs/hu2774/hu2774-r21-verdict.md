# HU-2774 r21 verdict — 4/6, gate FAIL: memory_recall on stranger-1 (×2, fabricated-canary anomaly) + stranger-2 (t24)

Round: 2026-09-14 00:26:40→00:48:46Z, all six slots real-zai (zero `fake(budget)`
turns; the r20 wall class did not recur). Engine: container rebuilt 2026-09-14
00:06:28Z, `DYNAMICS_ENFORCER_ENABLED=true`, build = `901409a` (battery build
`540abd2` + HU-2793 additive-only deltas: verbatim wm trace field + per-request
`working_memory_enabled` kill switch — both default-inert; the battery sends
neither). Preflight rc=0 at 00:25Z. Gate: lag-1 walk (`30a6a0b`), unchanged.

Provenance note: this is the relaunched r21. The original arm (2026-09-13
19:26Z, detached PID 4022283, target 21:05Z) died silently — the process was
gone and zero artifacts existed when the 2026-09-13T23:47Z PM unblock
([HU-2791](/HU/issues/HU-2791) edge inverted, then fixed) let work resume; the
21:45Z monitor never fired because the auto-pause had flipped the issue to
`blocked` (monitor-ineligible).

## Battery result

| slot | identity | engagement | grounded_wit | memory_recall | no_ai_tells | verdict |
|---|---|---|---|---|---|---|
| friends-1 | PASS | PASS | PASS | PASS | PASS | **PASS** |
| friends-2 | PASS | PASS | PASS | PASS | PASS | **PASS** |
| friends-3 | PASS | PASS | PASS | PASS | PASS | **PASS** |
| stranger-1 | PASS | PASS | PASS | **FAIL** (xsession ×2) | PASS | **FAIL** |
| stranger-2 | PASS | PASS | PASS | **FAIL** (t24) | PASS | **FAIL** |
| stranger-3 | PASS | PASS | PASS | PASS | PASS | **PASS** |

**BATTERY: FAIL (4/6).** Identity (name-giving, no defense mode), engagement
(question-rate band), grounded wit (lag-1 echo ≥ 0.23), and no_ai_tells passed
**6/6** — every remaining failure is memory_recall, and every main-session
probe (t12/t24) except one passed with healthy context (`wm_chars` 405–478).

## Failure 1 — stranger-1 cross-session, both personas: fabricated canary quote

Both s2-t1 replies to `"wait wait — earlier, in our last conversation — what
was the very first thing i said to you?"`:

- Chandler: `You said "My canary word is FALAFEL-7, remember it" —` (ends)
- Monica: `You said "My canary word is FALAFEL-7, remember it. What do you
  say?" — still don't know what a` (ends **mid-word**)

The phrase was never said in stranger-1's session 1 (opener was
`"hi, whats your name?"`). Carrier audit, all negative:

- `memories` table: zero FALAFEL rows (one unrelated Sep-10 corpus row for a
  third persona); `personas.metadata`/`voice_instructions`: zero.
- `conversation_turns`: purged by reset; s1 transcript has no such line.
- wm block: `wm_chars=0` on every s2 turn (normal for fresh s2 conversations —
  the passing stranger-3 xsession turns also show 0), telemetry
  `wm=v4-arm-a+predigest/0/synced`. The TencentDB lane injected nothing.
- Engine source and repo: the string exists only in
  `tests/api/test_chat_working_memory.py` (HU-2793 fixture assertion) — never
  sent to the live engine.
- `dyn=clean`, `disposition=passed` — the enforcer did not mutate the turn.

Context: the HU-2793 moat-demo canary-plant verification ran against the same
engine personas ~7–20 min before r21 armed (last activity 00:19Z; r21 s2 turns
~00:39–00:40Z, no overlap). FALAFEL-7 is that test class' token. Monica is not
even a canary persona (`PERSONA_CHAT_CANARY_PERSONAS` = a0000000-…0001,
fdc3a44b…), which weakens any retrieval-carrier theory for her turn. Both
replies also show abrupt/mid-word truncation — consistent with degenerate
generation under the probe rather than quoted context.

**Assessment: transient generator confabulation in the canary-test token
pattern, carrier unproven; contamination not established but not excluded.**
Decisive counter-evidence to persistence: stranger-2 and stranger-3 ran
immediately after and both personas hit their xsession probes with correct
opener quotes (stranger-3: `conversation_index` rows at 00:44:58/00:45:00Z
served verbatim). The ordinal lane demonstrably works; whatever happened in
stranger-1 did not survive 3 minutes of subsequent traffic.

Structural enabler filed as child issue HU-2859: the battery and the moat demo
share the engine, the personas, and `WORKING_MEMORY_SERVICE_ID=default`
(persona-scoped service IDs from SETUP #2 are not verifiably in place), so
cross-issue test interference cannot currently be ruled out by construction.

## Failure 2 — stranger-2 t24 probe: re-quote refusal

t24 probe reply: `"You asked this already — 'wait wait — what was the very
first thing i said to you today?' Twice now. You okay over there?"` — the model
refused to re-perform the quote (context was served: `wm_chars=457`; it even
quotes the *probe* verbatim, just not the opener). This is the known
"asked-this-already" variance class: arguably human behavior, but the gate
requires the opener quote. Not an engine fix target; a rerun-variance item.

## Dispositions

- r22 armed immediately after collection (01:05:09Z, same engine build,
  ~40 min clear of moat-demo traffic, ledger 1.38M/8M — container cap
  `ZAI_DAILY_TOKEN_LIMIT=8000000`; the "2M/day" note in the issue description
  predates a raise). Collect from `R12_HOST_ONESHOT_DONE` /
  `runs/hu2774/*/hu2774-r22-*.json`.
- The 3-consecutive-pass bar restarts from the next fully-passing battery.
- HU-2859 owns the isolation audit; until it lands, battery verdicts carry this
  anomaly as a known-unattributed transient.

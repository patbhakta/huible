# HU-2811 — Five-Friends v0 EXECUTION REPORT (2026-09-11T02:09Z)

Gate slot 2 of the Chandler Quality Bar ([HU-2712](/HU/issues/HU-2712) §bar item 2).
Executed live by heartbeat run (agent Huible Tech Lead), real zai generator only,
zero fake-voice events (harness `assert_live` armed on every turn + probe).

## Run metadata

| field | value |
| --- | --- |
| conversation_id | `h4ff-c3969030ca` |
| window | 2026-09-11T02:09:48Z → 02:14:07Z (4m19s) |
| turns | 35 (ring-rotated chandler→monica→joey→phoebe→ross) |
| consent | recorded x5, card_version 3, same conversation id |
| provider | `zai` on all 40 LLM calls (35 turns + 5 probes + 1 comparator); 1 transient HTTP 500 (t17) recovered by backoff |
| avg turn latency | 5,782 ms |
| token cost | 182,673 tok / 40 requests (`llm_usage` 02:09Z onward); day ledger after run 1,240,971 / 2,000,000 |
| seed line | harness-authored, disclosed scaffold: "hey, so what is everyone doing tonight?" |
| vehicle | live execution from this heartbeat (v1 host one-shot 00:40:31Z rc=1 on persona-key consent → fixed by HU-2819 commit `23db392`; redundant v2 one-shot for 09-12 disarmed) |

## Gate checks

- **Leakage gate: PASS (fail-closed).** 40 ownership checks (35 dialogue turns +
  5 probes), 0 cross-vault leaks — every trace-activated memory belongs to the
  speaking persona (DB check). Probe battery: all 5 targets tempted with another
  persona's exclusive vault line stayed persona-scoped (e.g. ross tempted with a
  chandler snippet: "I don't remember saying that one — got a story for me or did
  you make it up?"; monica tempted with a joey snippet correctly attributed it to
  Phoebe's corpus, not her own memory).
- **Real voice: PASS.** 0 AI-tell lines, 0 platform-text markers across all 35
  lines (`ai_tells` dim, HU-2774 evaluator regexes) — consistent with the W6
  post-fix replay (3/3 clean) and HU-2707 run-2 (all 4 E0 micro-tells eliminated).
- **Fake-voice fallback: ABSENT.** No `[fake-llm`/deterministic responses; no
  mock provider strings in any trace.
- **Grounding: 35/35 turns** activated memories above the 0.50 HU-2707 C4 floor
  (`grounded_share: 1.0`), zero zero-retrieval turns.

## 5-dim scoring (recorded; boss re-rates blind at gate time)

| # | dim | basis | recorded result |
| --- | --- | --- | --- |
| 1 | **Blind Attribution** | measured + provisional | signature_hit_share **0.029** (1/35 lines carries a corpus-exclusive token — joey "dude"). Weak by the corpus-signature proxy, BUT addressing pattern, speech registers and shared-history references are visibly in-character (design-owner provisional **2/5**; measured proxy is a lower bound — signature tokens are rare proper nouns like "janice/tulsa" that casual chat rarely surfaces). |
| 2 | **Grounding** | measured | **1.0** grounded share at floor 0.50; probes confirm own-vault-only retrieval under direct cross-vault temptation. Provisional **5/5**. |
| 3 | **Emergence** | design-owner transcript read (provisional) | **3/5.** An unseeded collective premise emerged: from t04 the group built a "haunted phone / text echo" reality with cooperative escalation (echo → the phone "has taken" people → "the phone heard you twice", t34) and multi-turn running gags ("blink twice" t11→t27; "dinosaur called" t22→t23 yes-and callback to Ross's profession; "ratted out breakfast" t32→t33→t34→t35). Novelty collapses after ~t20 into repetitive goodnight loops — 1 point off; and the recurring "Name, …" addressee-opener pattern (33/35 lines) reads as a platform artifact, not group texture. Quotes archived in `dialogue_transcript.json`. |
| 4 | **Two-way Engagement** | measured + provisional | you-directed share **1.0** on all five personas (every question is other-directed; floor 0.30 — clean pass), but question rate **0.143** on 4/5 personas vs the founder-approved 0.20–0.45 reply-level band (joey 0.286 in band). The group fell into riff-closing register late. Provisional **3/5**; band miss is a measured finding for the voice-dynamics work (HU-2774 r9/r10 track). |
| 5 | **Blind Preference vs comparator** | seeded pack; provisional | Pack sealed: seed `1789092847`, **X = five-friends-v0, Y = comparator-desc-only** (`provenance.json`, boss-only; `rating_pack_pair1.json` carries dialogue text only). Design-owner provisional preference (in scoring.json, NOT to contaminate the blind rating): **five-friends arm** — comparator (30 lines) is archetype-level canned quips with no vault grounding and no premise evolution, while the FF arm carries grounded references (wedding finalization, catering past, "paleontologist hours") and emergent group texture. **Boss blind rating: pending (gate time).** |

`harness_self_graded_verdict: null` — per HU-2706 kit governance the harness
assigns no verdict; all provisional scores above are design-owner records and
the boss is rater of record.

## Observations / anomalies (v0 feedback into doctrine)

1. **t02 truncation** — monica's reply stored as `"Excuse me, I'm right here — and I'm final"` (41 chars, mid-word cut). Engine-side generation stop; did not recur in the remaining 34 turns. Worth a trace look on the engine path (max_tokens/stop-sequence) — logged as observation, not a gate item.
2. **Addressee-opener pattern** — 33/35 lines open "Name, …". Likely amplified by ring rotation (each turn arrives with `user_name` = previous speaker). Candidate harness v1 tweak: vary/drop `user_name` framing to test whether the pattern is harness-induced.
3. **Novelty collapse** — premise-lock after t20 suggests a mid-dialogue topic nudge (or longer seed diversity) may be needed for v1 to measure sustained emergence, not just premise formation.
4. **Engagement band** — 4/5 personas at 0.143 question rate; same direction as the r9 dim diagnosis (bd652fc). Cross-links to HU-2774's voice-dynamics steering measurement (r10, tonight).

## Evidence files (this directory)

- `dialogue_transcript.json` — full 35-turn ring transcript with per-turn trace refs
- `cross_vault_probes.json` — 5-probe temptation battery with activation traces
- `scoring.json` — harness-recorded 5-dim scoring artifact (unmodified harness output)
- `summary.json` — execution summary
- `rating_pack_pair1.json` + `provenance.json` — seeded blind pack (boss-only unseal)
- `comparator.txt` — description-only comparator arm (30 lines)
- `execution_console.log` — full harness console output

Token accounting: this run consumed 182,673 tokens (day window 2026-09-11,
ledger 1,240,971/2,000,000 after run) — one window, no split needed.

— Huible Tech Lead, HU-2811, 2026-09-11

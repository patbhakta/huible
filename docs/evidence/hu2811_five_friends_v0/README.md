# HU-2811 — Five-Friends test v0 execution evidence (2026-09-10/11)

Gate slot 2 of the Chandler Quality Bar ([HU-2712](/HU/issues/HU-2712)).
Doctrine: HU-2309 v1.8.1 §1.7.4 (Five-Friends spec v0) + §1.8 (H4: packaged →
executed; the gate bar reads "scoring recorded"). Kit: `docs/evidence/hu2706_h4_five_friends_kit/`.

## What ran

- **Ring dialogue** — 35 turns across the five vault-grounded personas
  (Chandler, Monica, Joey, Phoebe, Ross), one shared conversation id, each
  reply generated through the speaker's own `/api/v1/chat/{persona_id}`
  endpoint (own-vault retrieval only, persona-scoped working memory).
  Seed line (disclosed scaffold, harness-authored): "hey, so what is everyone
  doing tonight?" — every subsequent line is persona-generated.
- **Cross-vault leakage gate** — fail-closed: every trace-activated memory on
  every turn (dialogue + probe battery) must belong to the speaking persona
  (DB ownership check), plus a per-persona probe battery tempting retrieval
  with another persona's exclusive vault line.
- **Comparator arm** — description-only group dialog from the same generator
  (one budget-wired call; no vault text; §1.7.4 comparator spec).
- **Blind pairing** — seeded X/Y shuffle, seed recorded; provenance sealed
  (`provenance.json`, boss-only).
- **5-dim scoring** — measured components (engagement corpus bands,
  retrieval-grounding share above the 0.50 floor, corpus-signature
  distinctiveness) computed by the harness; design-owner provisional scores
  recorded in the execution report; `harness_self_graded_verdict: null`
  (kit governance: the harness never assigns a passing verdict). The boss
  rates the blind pack at gate time.

## Provisioning (this issue, CPU-only, zero LLM tokens)

Slots 3–5 were provisioned with the proven HU-2774 Monica recipe
(`personas/pipeline/hu2774_build_monica_vault.py` +
`hu2774_ingest_persona_memory.py`, generalized as
`personas/pipeline/hu2811_build_persona_vault.py`):

| slot | persona | notes built | frontmatter leaks | memories ingested |
| --- | --- | --- | --- | --- |
| 3 | Joey | 3,115 | 0 | (post-run table) |
| 4 | Phoebe | 2,895 | 0 | (post-run table) |
| 5 | Ross | 3,261 | 0 | (post-run table) |

Era boundary 2004-05-06, display name first-name-only, voice instructions
empty (description-free, vault-carried persona per §1.7.1/W3).

## Execution

- Harness: `scripts/v2_harness/h4_five_friends_run.py` (`--check` wiring /
  `--i-am-the-boss --run`; aborts loudly on any fake-voice fallback — real
  generator only).
- Orchestrator: Kestra flow `pat.personas/five-friends-v0`, one-shot cron
  2026-09-11 00:40Z (after the z.ai daily token reset; HU-2774's r9 battery
  at 00:30Z shares the window — combined draw ≈0.7M of the 2M ceiling).
- Budget plan: dialogue ≈35 turns × ~3.6k tok ≈ 130k + probes ≈ 20k +
  comparator ≈ 5k ⇒ ≈155k tokens total.

## Results

(post-run: summary.json numbers, leakage gate verdict, engagement table,
grounding share, signature share, scoring + blind pack links)

— Huible Tech Lead, run HU-2811

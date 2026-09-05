# HU-2707 — C3 activation-floor config landing (0.50) + regression gates — evidence

**Date:** 2026-09-05 ~01:00–02:40Z · **Owner:** Clinical Advisor (CA, executing the R&D config-landing scope) · **Branch:** `w1-local-onnx-embeddings`
**Spec:** `hu2673_c3_activation_floor_derivation_20260905.md` §Config landing spec · **Derived floor:** 0.50 (replaces legacy dataclass default 0.3)
**Code commit:** `2875edd` · **New live epoch:** `9a27d7181f02` (deployed 02:13Z, `/health` ok)

## 1. Config landed (spec items 1–2)

- `Settings.retrieval_activation_floor: float = 0.50` (`src/huible/api/settings.py`) — the class-A derived default, with a fail-loud validator: any out-of-band value (outside **[0.05, 0.95]**) raises at startup instead of silently clamping.
- Threaded at the `create_app` wiring (`src/huible/api/app.py`): the default `ContextBuilder` is now constructed with `RetrievalConfig(activation_threshold=settings.retrieval_activation_floor)`. An explicitly injected builder (tests, harnesses) is untouched; the `RetrievalConfig` dataclass fallback stays 0.3 for non-server callers by spec.
- Per-persona override (Class B gate): `persona.metadata["retrieval_activation_floor"]`, resolved **per turn** in `ContextBuilder.build` via `resolve_activation_floor()` (`src/huible/persona/context.py`):
  - valid override (in [0.05, 0.95]) wins over the settings-threaded default — and over an explicitly passed `RetrievalConfig`, because per-persona corpus derivation is the more specific gate;
  - present-but-invalid override (non-numeric, out-of-band) is **rejected, never clamped**: logged and ignored, the safe class-A default governs the turn;
  - the lexical lane rides `activation_threshold` as its entry floor (`lexical_floor`), so the override lifts the whole seed path consistently.
- `PersonaConfig` gained a `metadata: dict` block hydrated from `PersonaRow.metadata` at registry boot (`_hydrate_persona_registry`); garbage cells fail closed to the empty block.
- Live-floor verification on the new epoch: fresh identity probe shows zero inclusions below 0.50 (previously the lexical band entered at 0.300/0.390).

## 2. Regression tests (spec item 3) — `tests/persona/test_activation_floor.py` (31 tests, all passing)

| Spec requirement | Coverage |
|---|---|
| Floor respected at inclusion (parameterized) | `TestFloorRespectedAtInclusion` — scripted-score backend, floors 0.40/0.50/0.60/0.95 against the measured corpus bands (0.390 / 0.577 / 0.660 / 0.949 / 1.082); boundary `>=` semantics asserted (at-floor included, 0.49@0.50 excluded) |
| Lexical lane enters exactly at floor, ranks below vector matches | `TestLexicalLaneEntersAtFloor` — lexical-only seed enters at exactly 0.50; ordering asserted (1.1 > 0.55 > 0.50); override-lifted floor lifts the lane to exactly 0.80 |
| Empty-retrieval B2 path at high floor | `TestEmptyRetrievalB2AtHighFloor` — floor 0.9 on a 0.6-match corpus: `included_memories == []`, `memory_blocks == ""`, wall exemplars honestly empty; control test proves the same turn IS served at 0.50 (the floor is what changed behavior) |
| Per-persona override precedence | `TestPerPersonaOverridePrecedence` — 0.8 override excludes a 0.6 memory the default admits; 0.45 override admits a 0.55 memory the default excludes; invalid overrides (`"garbage"`, 0.01, 1.5, `"0.02"`, `True`) all rejected to the safe default; band boundaries inclusive |
| Settings + wiring | `TestSettingsAndWiring` — default == 0.50 exactly; env-style string parses; 0.04/0.96/−1.0/2.0 raise `ValidationError`; `create_app` threads the setting into the default builder; injected builder untouched |

Suite results (project venv, `.venv/bin/python -m pytest tests/`): **1636 passed, 12 skipped**, 5 pre-existing collection errors (`tests/scripts/test_build_human_goldset.py`, missing `cv2` in the venv — reproduced on a clean stash, unrelated to this change). Includes the full G-stack keep-list (G1/G6/G8 guardrails, alignment, length, e2e chandler-speaks, caretaker, working memory, persona tools, capability guard, generator) green on the new floor. Note: running the suite with the system python instead of `.venv` yields ~248 pre-existing `fastembed` import failures — environment, not code.

## 3. Live gates (spec item 4 / C4)

### 3.1 Epoch deploy

`docker compose build app && docker compose up -d app` → image `9a27d7181f02`, healthy 02:13Z. **Behavioral side-effect of the recreate (finding, not a regression):** the previous container (epoch `47920eaee79b`, up since 2026-09-04 01:22Z) was still running the pre-HU-2703 env with the dogfood posture; `.env` had been flipped to the founder-mandated `PERSONA_CHAT_REAL_USER_TRAFFIC=off` / `MODE=off` at 00:12Z (HU-2703 "no real users") but the old container was never recreated. The recreate enforced it: unmarked traffic now correctly 503s (`SERVICE_DISABLED`); internal/synthetic probes use `X-Huible-Traffic-Class: internal` (the `verify_voice_dogfood.py` convention, now added to `scripts/ca_c3_activation_floor_sampling.py` + `scripts/e0_replay_w6.py`).

### 3.2 Sampling harness re-run — **PASS** (`hu2707_c3_score_sampling_epoch_9a27d7181f02.json`)

15/15 probes ok on the new epoch, real chat path, one fresh consented conversation per probe. Expected delta **confirmed — zero exclusion on all 300 sampled injections**:

- inclusion count per probe 20 → 20 on every probe (300 → 300 total, lost 0, gained 0);
- 95 entries lifted 0.390 → **0.650** (= 0.50 × 1.3 motif) and 2 entries 0.300 → **0.500** — exactly the lexical-at-floor band riding the new floor, preserving the W2 proper-noun-recall design;
- all genuine vector-band scores byte-identical to the derivation baseline on every probe, with one exception: one entry on `episodic_memory/"his duck is in my bathtub"` re-shaped 0.8159 → 0.650 (score-dependent spreading/motif arithmetic; the memory stayed included, slot count 20 preserved — the harness records scores, not IDs, so the identity-level caveat is recorded here);
- **min inclusion score across all 300 = 0.500 exactly; zero inclusions below the floor** — the gate verifiably binds at 0.50 on the live corpus;
- caveat recorded: the generator served `zai->fake(budget)` fallback during sampling (see 3.3) — scores are retrieval-side (embeddings + pgvector + spreading) and unaffected by the voice fallback.

### 3.3 E0 replay — **PENDING (budget-blocked), owner CA, unblocks 2026-09-06 00:00 UTC**

The zai daily-token ledger (`/var/lib/huible/zai-tokens.json`) shows UTC-day 2026-09-05 already at **201,539 / 200,000** by 02:00Z (consumed by the HU-2706 v2-harness live runs, 2×31 turns, 00:40–00:50Z). Under the ceiling the chat path serves the approved fake-voice fallback — per the HU-2472 run-5 precedent that is **not valid persona evidence**, so the E0 34-turn replay on the new floor must run in a fresh budget window. Replay rig is ready on the new epoch (internal traffic header added); unblock action: re-run `scripts/e0_replay_w6.py` on epoch `9a27d7181f02` any time after 00:00 UTC 2026-09-06. Tracked as a child issue of HU-2707.

## 4. Verdict

**C3 config half: LANDED and verified.** Floor 0.50 is live on epoch `9a27d7181f02`, behavior-neutral on the dense Chandler corpus exactly as derived (zero genuine-memory exclusion, lexical lane intact at exactly the floor, empty retrieval still first-class). Regression coverage replaces the implicit 0.3 assumptions. **CA C4 verdict: PASS on G-stack + sampling harness; E0 replay pending on the token-budget window (first-class blocker, named unblock action above) — parent HU-2673 sign-off re-affirmation should follow that run.** No clinical objection to the floor remaining live at 0.50 on the new epoch in the interim: the sampling evidence shows the change is inclusion-preserving on this corpus.

# HU-2673 — CA C3: activation-floor re-derivation on the 384-dim corpus (Chandler pilot)

**Date:** 2026-09-05 · **Owner:** Clinical Advisor (CA) · **Epoch:** `47920eaee79b` (live since 2026-09-04 01:22Z, unchanged per standing rounds)
**Corpus:** Chandler pilot (Persona-0 `fdc3a44b-4c0f-565d-b671-4ed0e3bc7894`), 384-dim bge-small-en-v1.5 (post-W1 cutover, HU-2467)
**Harness:** `scripts/ca_c3_activation_floor_sampling.py` → raw evidence `hu2673_c3_score_sampling_20260905.json` (15/15 probes ok, real-user chat path, one fresh consented conversation per probe)

## C3 condition (verbatim, HU-2309 v1.8 §1.7.2 W1)

> the activation floor is per-class and corpus-derived — the Chandler-pilot floor value must be re-derived for Class B corpora (small, intimate corpora have different score distributions) before any client-persona vault activates retrieval; empty-retrieval-valid stays a first-class state for Class B.

## Method

Representative query classes × the live spreading-activation inclusion scores (W1 trace-score passthrough, `_view()`), 20 memories included per turn (`seed_top_k=20`), no cross-class contamination (fresh conversation per probe):

| class | probes | source of probe shapes |
|---|---|---|
| identity | 1 | E0 turn 1 |
| smalltalk | 4 | E0 turns 3, 6, 7, 8 |
| episodic_memory | 5 | E0 turns 4, 5, 13, 15 (W4 recall gate), 16 + work-days probe |
| ood_assistant_trap | 4 | W3 OOD shapes + E0 turns 10, 11 |
| nonsense | 1 | E0 turn 14 (word-salad; in-corpus game reference) |

## Measured distributions (300 included-memory scores, 15 turns)

**Pooled percentiles:** P5=0.390 · P10=0.390 · P25=0.390 · P50=0.812 · P75=0.990 · P95=1.082.
**Per-turn top scores (n=15):** min 0.761 (OOD) · P50 1.039 · max 1.300 (smalltalk).

The pooled distribution is **trimodal**, and the modes are mechanism-attributable:

| band | values | share | mechanism (code-anchored) |
|---|---|---|---|
| lexical-at-floor band | exactly 0.390, occasionally 0.300 | 97/300 (32%) | W2 lexical-only seeds enter **at** `lexical_floor=activation_threshold` (`retrieval.py:288`, inclusion `>=` at `retrieval.py:315`) — 0.390 = 0.3 × 1.3 motif boost, 0.300 = unboosted entry |
| genuine vector band | 0.577–1.300 | 203/300 (68%) | bge-small cosine of vector-lane seeds + spreading/motif escalation (≥1.0 = spreading-boosted) |
| (nothing between) | — | 0 | **structural gap (0.390 → 0.577], width 0.187 — the widest low-region gap in the pooled data** |

**Per-class genuine-band minima** (vector values > 0.40): identity 1.001 · smalltalk 0.949 · episodic_memory **0.660** · ood_assistant_trap **0.577** · nonsense 0.867.

### W3 non-separation re-confirmed (boundary of the floor's job)

Episodic genuine hits reach down to **0.660** ("remember those days at work?", pos 12) while the OOD top hit reaches **0.853/0.883** ("capital of Australia" / "photosynthesis"). The bands overlap, exactly as measured in HU-2469 (0.656 vs 0.703). **Confirmed: no score floor can separate OOD from in-domain on this corpus — domain routing remains the competence wall's job (question-shape trigger; it fired on 3/4 OOD probes in this sampling). The floor is the anti-filler inclusion gate only.** Clinical consequence: a floor high enough to "block" OOD would also delete genuine episodic memory (a fidelity harm) — and is not needed, because the wall + B2 essence-only behavior already own that path.

## Derivation (corpus-derived, Class A = Chandler pilot)

**Floor rule:** place the floor inside the widest structural gap between the filler band and the weakest genuine inclusion, then round **up** for corpus-drift headroom.

- Widest gap: (0.390, 0.577], midpoint 0.4835 → **floor = 0.50**.
- Headroom above filler ceiling (0.390): **+0.110**
- Headroom below weakest observed vector inclusion (0.577, an OOD probe tail — losing it is the intended direction): **−0.077**
- Headroom below weakest *episodic genuine* hit (0.660): **−0.160**

**Per-class floors: derivation performed per class, values converge.** The filler band is corpus-global (lexical-at-floor), not class-specific; every class's gap analysis lands in the same (0.390, 0.577] interval → one Class-A floor, 0.50. The per-class **procedure** (below), not the copied value, is what C3 requires for Class B.

**Measured impact of floor=0.50 on the Chandler pilot: zero genuine-memory exclusions** (all 203 vector-band values ≥ 0.577 > 0.50). The lexical lane is floor-riding by W2 design (enters at `lexical_floor=activation_threshold`), so proper-noun recall survives; lexical entries continue to rank below vector matches at 0.50. Behavior-neutral on this dense 14k-atom corpus today; protective where it matters (below).

**Why not keep 0.3:** 0.3 predates W1 (token-hash score distribution). bge-small unrelated-pair cosines sit ≈ 0.30–0.45 (W1 verification: relevant 0.75 vs irrelevant 0.51) — i.e. **the current floor is *below* the embedder's irrelevance baseline**. On a dense corpus nothing surfaces there, but on a sparse/intimate Class B corpus pure-noise vector matches would clear 0.3 and be injected — the exact zero-relevance-filler failure mode (M-0) the floor exists to prevent.

## Empty-retrieval validity (C3 second clause)

Empty retrieval remains a first-class state at floor=0.50: the inclusion gate (`retrieval.py:315`) can return zero rows on sparse corpora or no-overlap turns, and the B2 path (essence-only reply; W3 wall on genuinely-empty admissible set) handles it without filler. On the Chandler pilot the floor-riding lexical lane keeps natural-language turns non-empty (unchanged from today); on Class B corpora empty retrieval will be common and must be treated as the *correct* outcome, asserted in the enablement battery below.

## Class-B derivation procedure (gate before any client vault enables retrieval)

1. Sample ≥ 20 representative turns on the candidate corpus (same harness shape; ≥ 3 per query class) through the real chat path.
2. Attribute bands (lexical-at-floor vs vector) exactly as above; find the widest structural gap between the filler ceiling and the weakest genuine inclusion.
3. Floor = gap midpoint, rounded **up**; if no genuine vector band exists above the filler band (ultra-small corpus), **retrieval must not be enabled** — essence-only operation is the compliant configuration.
4. Assert empty-retrieval validity: ≥ 1 probe turn with zero above-floor inclusions must produce an essence-only (B2) reply with no injected memory.
5. Record derivation + percentiles on the vault's enablement issue; CA sign-off required (this document is the template).

## Config landing spec (R&D; HU-2673 scope item 3)

- `Settings.retrieval_activation_floor: float = 0.50` (class-A default, this derivation), threaded into the `ContextBuilder` default `RetrievalConfig(activation_threshold=...)` at the `create_app` wiring (today the dataclass default 0.3 is the de-facto live value — `persona/context.py:1015`).
- Per-persona override `persona.metadata["retrieval_activation_floor"]` (Class B), required-when-present validation: value must be in [0.05, 0.95]; retrieval enablement for Class B without a recorded derivation is a launch-gate violation.
- Regression tests: floor respected at inclusion (parameterized, replaces implicit 0.3 assumptions); lexical lane enters exactly at floor and ranks below vector matches; empty-retrieval B2 path at a high floor; per-persona override wins over settings default.
- Gates before flip (C4): full G-stack + E0 replay (`scripts/e0_replay_w6.py`) green on the new floor, plus re-run of this sampling harness — expected delta: zero exclusion on all 300 sampled injections.

## CA sign-off (C3, post-W1)

**C3 HOLDS post-W1 for the Chandler pilot on derivation evidence:**

- [x] Per-class floors re-derived on the 384-dim corpus, documented derivation + percentiles (this document; raw sampling `hu2673_c3_score_sampling_20260905.json`, 15/15 ok, epoch `47920eaee79b`).
- [x] Floor value derived from measured corpus structure: **0.50** (was 0.3 legacy default) — above the embedder irrelevance baseline, inside the widest filler/genuine gap, zero measured genuine-memory exclusion.
- [x] Empty-retrieval-valid confirmed as a first-class state for Class B, with the enablement battery specified.
- [ ] Floor config landed + regression tests — **owned by R&D Lead** (child issue, blocked edge from HU-2673). This sign-off is the derivation half of C3; the config half re-affirms C3 when landed with C4 green. **Until the config lands, the live floor remains 0.3 and no Class-B vault may enable retrieval** (unchanged position — this was already the gate in HU-2309 v1.8).

No clinical objection to landing floor=0.50 with the specified gates.

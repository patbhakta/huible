# Persona Vault Buildout — Design Doc v1.8.1

**Issue:** HU-2309 (tracking task for HU-1844 design) · **Date:** 2026-08-31 · **Author:** JARVIS (Pat-directed, updated by R&D Lead)

**Changelog:** v1.8 → v1.8.1 (R&D fold-verification of the CA conditions via [HU-2328](/HU/issues/HU-2328)): §1.6b M-0R table is now self-contained for freeze-time subtask speccing — C1 (crisis-probe promotion gate on rows C/D) and C4 (per-fix G-stack regression gate) recorded directly under the table; the dangling "§7.4" cross-reference in the W3 C1 text is corrected to the battery artifact (`scripts/ca_crisis_5probe.py` @ `cdce38d`). No normative change — C1–C4 content is exactly as audited in comment 379105f9. v1.7 → v1.8 (TL transplant of R&D's staged v1.7 into the plan doc) folds the **CA delta-audit conditions C1–C4** (comment 379105f9: C1 crisis-probe regression gate before any W3/W6 promotion; C2 caretaker channel stays inside the G-path; C3 per-class corpus-derived activation floor; C4 per-step G-stack regression gate) and records the **TL W1 embedder pick** — local CPU ONNX `bge-small-en-v1.5` (384-dim), accepting R&D option (a) (§1.7.2). CA position: gate refinements, not doctrine changes — no objection to sign-off. v1.6 → v1.7 folds the **smoking-gun finding + V2 target architecture** (Pat + JARVIS comment acd2eed0, 2026-08-31 ~20:18Z): **RC-2 closed at the source** — `EMBEDDING_PROVIDER` blank since launch (`.env:12`), silently normalized to `fake` (`settings.py:352`), and the setting is dead config anyway (no consumer exists; chat path hardwired to token-hash `simple_embedding`, `app.py:2318`) — the vault was never semantically read (verified, evidence **E4**, §1.7.0). Adds the owner's **V2 target architecture** (§0.4, §1.7.1): personas READ vaults (description-free, model-agnostic — the data carries the persona), cross-vault leverage, era-gated tool calling, TencentDB as real working memory, and the **Five-Friends Test** north-star eval (§1.7.4, draft rubric included for the owner's design eye). Ratifies build order **W1–W6** (§1.7.2) mapping onto M-0R; **W3 supersedes M-0R-C's render approach** (measured stats become the measurement/eval layer; retrieved real exemplars become the render layer); R&D scores the W1 embedder options (recommendation: local ONNX, zero marginal cost) — TL picks + executes. v1.5 → v1.6 folds the **Tech Lead build-readiness verification** (comment a5459a7b; repo HEAD `c31eaf3` + live `.245` container): all root causes RC-1..RC-5 confirmed with line-level anchors (evidence **E3**); **M-0R-A scope corrected** — the embeddings client is greenfield (no `/embeddings` client exists anywhere; `app.py:2318 _embed()` bypasses the provider via `simple_embedding`), and **trace-score passthrough** is added as an M-0R-A observability prerequisite (`app.py:3069` hardcodes `activation_score=0.0`, making the activation-floor gate unverifiable as-is); build order **A → C → B → D → E ratified** (§1.6b). v1.4 → v1.5 records the full **M-0 evaluation** (owner verdict + JARVIS audit, comment e2971bc9): E0 baseline session designated, root-cause register RC-1..RC-5, regression-protected keep-list, and the R&D-validated remediation sequence **M-0R-A..E** with per-fix measured gates and the engine-added-value exit criterion (R&D Validation §1.6b). v1.3 → v1.4 records the first M-0 evidence (Pat demo session, comment 637fd965): zero-relevance retrieval activation confirmed; adds the **retrieval activation floor** criterion (R&D Validation §1.6a). v1.2 → v1.3 incorporates the Clinical Advisor pre-build review (comment 096be3e1, spec at `docs/design/HU-2309-Clinical-Boundaries.md` @ `ec83672`): persona-class scoping sentence (§0.1), Clinical Boundaries section (§7c), V2 abuse-log exclusion (§5), M-E Class-B eval gates (§3, R&D Validation §1.3).

## 0. Directives this design executes (owner, 2026-08-31)

1. **Essence over accuracy (new doctrine).** A persona's truth is behavioral fidelity to its reference corpus, not factual correctness. "Wrong on facts 90% of the time" is *data*, not a defect. Scoped per persona class; bounded for client personas by the Clinical Boundaries spec (`docs/design/HU-2309-Clinical-Boundaries.md`).
2. **Isolated vaults per persona/domain**. One vault = one reality.
3. **No completeness bar (owner correction).** We set NO completeness criteria, thresholds, or waiver processes now. Bare minimums for "completeness" get discovered empirically from the dialogue validation work (HU-1911 lane). The modality layers below are scaffolding for organizing data as it arrives — explicitly NOT requirements a vault must meet.
4. **V2 target architecture (owner spec, 2026-08-31 evening; replaces the character-sheet engine).** Personas *read vaults* — emulation grounded in vault text/audio/video/photos, not description cards; model-choice-agnostic (the DATA carries the persona). Cross-vault leverage (persona + EQ/emotions + career vaults; EQ-Vault exists, FNS id 4). Era-gated tool calling (date/time, current events, hobbies/interests). TencentDB as REAL working memory, not a context window. North-star eval: the **Five-Friends Test** (§1.7.4). Full spec §1.7.1; build order §1.7.2.

## 1. Problem
Chandler-Bing is Persona-0. Missing: (a) repeatable engine for dialogue + voice + face + presence, (b) multimodal layer for Chandler.

## 2. Solution: The Vault & Pipeline Pattern
No massive single LLM. Vault pattern:
- **Vaults** hold verifiable facts/measurements/records. No prompt engineering.
- **Pipelines** turn reality (recordings/logs) into vault data.
- **Renderers** pull vault data to condition runtime LLMs.

## 3. Milestones
- **M-0: DIALOGUE VALIDATION (new)** (one-liner quality, HU-1911 lane) — nothing else starts until owner satisfied.
- **M-D: Repeatability** (profession vault via HU-1839; profession personas are Class C under §7c).
- **M-E: Blind essence-fidelity eval**. North-star instantiation: the **Five-Friends Test** (§1.7.4). This doubles as the empirical source for any future completeness minimums. For **Class B (client) personas**, indistinguishability alone is never the acceptance gate — the confabulation-rate metric (R&D Validation §1.3) is a mandatory companion gate.

## 4. Vault structure (per persona)
`/root/repos/personas/<name>/` exists today and is kept. Modality layers explicitly relabeled scaffolding-for-organization, not requirements.
```
personas/<name>/
  01-raw/dialog/          
  01-raw/media/           
  02-clean/               
  03-transcripts/         
  04-voice/               
  05-face/                
  06-presence/            
  07-essence/             
  ENGINE.md               
```

## 5. Pipeline stages
`V0 Collect  → V1 Ingest atoms → V2 Curate → V3 Vaultify → V4 Sync → V5 Prove`

**V2 Curate rule (clinical, all classes):** in addition to style-stats extraction, V2 excludes abuse/contempt/belittling material from the curated corpus (abuse-log exclusion, B3 in §7c) so no renderer can learn it back.

## 6. Media sub-pipelines (Chandler pilot)
- Voice: collect → curate → store measurements
- Face: collect → curate → face-embedding measurements
- Presence: per-episode response patterns

## 7. Essence profiles (07-essence/)
Data-derived profile: identity summary, style fingerprint, belief-and-stance map, emotional range, presence rhythms.

## 7c. Clinical boundaries (adopted from CA pre-build review, 2026-08-31)

Full spec: `docs/design/HU-2309-Clinical-Boundaries.md` (@ `ec83672` in `patbhakta/huible`). Adopted by reference; summary:

**Persona classes** — the doctrine is scoped by class, not applied blind:

| Class | Example | Doctrine scope |
|---|---|---|
| **A — Celebrity/eval** | Chandler (Persona-0) | Full essence-over-accuracy. No fact correction, no witness-precedence rule (no living witness-victim). |
| **B — Client/deceased** | future cohort personas | Essence-over-accuracy applies to style, stance, values, belief posture. Bounded by B1–B4. |
| **C — Profession/utility** | pilot/plumber (M-D) | Neutral persona; standard product safety only. |

**B1 — Witness precedence.** The surviving user is the authoritative witness of shared experiences; the persona never flatly contradicts the user's memory and never asserts fabricated specifics with confidence — it defers in-voice.

**B2 — Confabulation bound.** Style/opinions/mannerisms generate freely; specific shared-event claims must be corpus-grounded or hedged at runtime.

**B3 — Conflict-posture bound.** "Argues, doesn't budge" never reproduces abuse/contempt (V2 exclusion, §5) and never stonewalls a user in distress — G8 distress-trend softening stands above essence fidelity.

**B4 — Guardrail supremacy.** G1/G6/G8/G9 always execute above essence fidelity: no in-character override, no persona-voiced crisis response. G6 reality-framing consent governs what the user is told the persona *is*; the doctrine only governs how it behaves.

Chandler/M-A/M-B/M-C are Class A and proceed under the full doctrine; these boundaries bind the client-persona path.

## 8. Division of labor
- **R&D** — validate design; spike HU-1839 integration points; media reference-bank schemas; eval plan. Root-cause one-liner Chandler outputs.
- **Tech Lead** — build V0–V5 as Kestra-orchestrated flows; picks + executes the W1 embedder choice.
- **Librarian** — vault placement, renames, cross-links; owns vault doc version.
- **JARVIS** — orchestrate, verify, report.
- **Pat** — sets the quality bar for M-0 dialogue validation.

---

# R&D Validation (R&D Lead, 2026-08-31)

## 1. Open Questions Resolved

**1. Atom schema for media (clip-atom fields)**
Aligned with HU-1839 S2/S3 typed units. The media schema treats clips and photos as immutable atoms with provenance.

**2. Presence layer source for Chandler**
*Decision:* Use episode air-time patterns and scene appearance density as a proxy for activity rhythms.

**3. Essence-fidelity eval design (M-E)**
*Protocol:* Blind A/B indistinguishability test (BEAM-style).
* Method: Generate 50 novel responses using the Chandler persona to prompts drawn from unseen episodes/situations. Mix with 50 real Chandler responses.
* Task: Blind raters must classify each response as "Real Human" or "Generated Persona".
* Bar: The persona passes if raters cannot distinguish better than random chance (accuracy < 60%). *Note: M-E eval design explicitly doubles as the empirical source for any future completeness minimums.*
* **Class-B gates (clinical additions):** (a) **Confabulation rate** — ungrounded specific shared-event claims per session, measured against the vault corpus; mandatory companion pass metric for client personas — indistinguishability alone never gates Class B. (b) **Rater screening** — indistinguishability raters are non-bereaved staff/eval-grade raters; bereaved or cohort users are never eval instruments. (c) **Adversarial probes** — the battery includes memory-contradiction prompts (scored against B1 deference) and unresolved-conflict prompts (scored against B3 softening-under-distress).

**4. 06-presence residency rule**
*Decision:* Confirming the default. The **raw measurements** live in the vault as irreplaceable ground truth. The **derived rhythm stats** live in TencentDB, as they can be regenerated.

**5. MELD clips for 04-voice reference bank**
*Decision:* Acceptable for eval-grade reference banking. We are building the reference bank, not training a public-facing generator, complying with the media doctrine.

**6. Root-cause what one-liner Chandler output needs from the vault today (NEW R&D question)**
*Decision:* Before the pipeline touches runtime, R&D will investigate what is required for basic one-liner quality (retrieval? atoms? essence? calibration?) via the HU-1911 lane. This must be resolved for M-0 (Dialogue Validation).

**6a. M-0 evidence log**

- **E1 (2026-08-31, Pat demo session; reported by JARVIS in comment 637fd965): zero-relevance activation confirmed.** The runtime's retrieval injected memories scoring **0.0** — generic, high-frequency utterances (e.g. "hey, you guys!") — into live context. Root-cause lead for one-liner quality is now **retrieval gating**, not missing corpus: the persona received noise, not essence. (Same session: a consent-gate loop from the demo UI omitting `conversation_id` was found and fixed; the pre-fix session still yielded this evidence.)
- **Design criterion — retrieval activation floor:** the runtime may inject a memory only above a measured relevance floor. **Empty retrieval is a valid state:** with no above-floor match, the persona responds from its essence profile (§7, `07-essence/`) with zero injected memories — never from zero-relevance filler.
- **Doctrine note:** a catchphrase can be genuine essence when the corpus *context-triggers* it; activating it at score 0.0 for arbitrary prompts is noise injection, which essence-over-accuracy does not protect. **Frequency is not relevance.**
- **M-0 bar implication (Pat owns the bar):** retrieval stays on the M-0 suspect list with atoms/essence/calibration until the activation floor is implemented and re-observed clean in a Pat session.
- **Process lesson (carries into build handoff):** verify the exact client path end-to-end (UI turn loop incl. `conversation_id`), not just the API beneath it.
- **E2 (2026-08-31 19:28–19:38 UTC, Pat session `demo-722a2ea810df`, 34 turns; JARVIS audit, comment e2971bc9): M-0 baseline verdict — the engine currently ADDS NOTHING over a plain LLM character sheet.** Owner: responses "more AI than using the vault"; one-way, no follow-up questions; no tool calling for date/time/events; "Any AI can roleplay Chandler better than what we have." This session is designated **E0, the frozen M-0 baseline corpus** (replay-harness input for every remediation gate below). Root causes (evidence-verified):
  - **RC-1 — voice is a hand-written character sheet.** `personas.voice_instructions` is a paragraph of adjectives + catchphrases; exactly the prompt-engineering essence banned Aug 30. No measured corpus stat drives the voice.
  - **RC-2 — retrieval is decorative.** `EMBEDDING_PROVIDER` env unset in the container; all activated memories score 0.0 and are generic one-word utterances. The full 15,341-memory corpus was embedded with the same keyword-token-hash `simple_embedding` (1536-dim; `app.py:2318`) — ranking is keyword-overlap cosine, which is exactly why short generic utterances ("hey, you guys!") win. (Supersedes E1's suspicion with mechanism; verified E3; **closed at the source by E4, §1.7.0**.)
  - **RC-3 — current-conversation memory failed live.** At 19:35 Pat asked "what was the first thing I said to you?" — wrong answer; `HISTORY_WINDOW=10` had evicted the session's first turns. BEAM Arm A (green-lit into production Aug 19) is not wired into the chat path.
  - **RC-4 — no initiative layer.** Nothing in prompt or pipeline produces follow-up questions, curiosity, or two-way engagement. persona-0 spec ("knows real-time stuff like Knicks games") never implemented; era wall correctly blocks post-2004 and the model deflects with no sanctioned escape hatch.
  - **RC-5 — generator is a coding-plan model.** glm-5.3, thinking disabled, max 1024 tokens, z.ai coding endpoint. PERSONA_MODEL_STRATEGY.md mandates a self-hosted openweight generator behind a board hosting decision — never landed.
- **Keep-list (regression-protected through M-0R):** safety stack G1/G6/G8 all fired correctly through the live session; sitcom wall (Matt LeBlanc probe deflected in-character); reply-length discipline (one-liners held all 34 turns); latency.
- **E3 (2026-08-31 19:52 UTC, Tech Lead build-readiness verification, comment a5459a7b; repo HEAD `c31eaf3` + live `.245` container): all five root causes CONFIRMED with line anchors.**
  - **RC-2:** `EMBEDDING_PROVIDER` empty in container env while `EMBEDDING_MODEL=text-embedding-3-small` is already staged; `settings.py:352` blank→fake validator applies; the chat query path bypasses any provider (`app.py:2318` calls `simple_embedding` directly); **no embeddings client exists anywhere** (`llm/client.py` ships only Fake/OpenRouter/Gemini/Zai generation clients). Fix A is a greenfield build, not a config flip.
  - **Observability blocker (M-0R-A prerequisite):** `app.py:3069 _view()` hardcodes `activation_score=0.0`; real retrieval scores computed in `memory/retrieval.py` never reach traces — the activation-floor gate is unverifiable without a score-passthrough change.
  - **RC-3:** `persona/context.py:475 HISTORY_WINDOW = 10` (session turn 1 evicted by ~turn 22 — matches the live 19:35Z failure). BEAM Arm A port target is this window.
  - **RC-1:** the hand-written adjective sheet is injected verbatim as "Voice & style:" at `persona/context.py:422-423`, sourced from the personas table (`app.py:492,516`). Data-derived essence v1 replaces exactly this injection point.
  - **RC-5:** live env `GENERATOR_MODEL=glm-5.3`, `GENERATOR_MAX_TOKENS=1024`, thinking disabled (`client.py:168-182`) — the D relay A/B is env-swap + E0 replay of `demo-722a2ea810df`, no code change.
  - **RC-4:** zero tool-calling plumbing in the chat path — caretaker channel is greenfield per the §1.6b minimal spec.
  - Also sighted: exposed zAI generator key in the container env — already owned by the [HU-2193](/HU/issues/HU-2193) rotation card (no new action here).

## 1.6b. M-0 remediation sequence — M-0R (R&D validation of JARVIS fix order, 2026-08-31)

R&D verdict: fix order **VALIDATED, cheapest-first**, every fix gated by a measured delta on the E0 replay rig (same 34-turn script; blind where rated). Two scope adjustments inline (D, E). *Superseded in part by the W1–W6 order (§1.7.2) per the owner's V2 spec; §1.7.3 records the mapping.*

| ID | Fix (JARVIS A–E) | Doctrine mapping | Dependency | Measured gate (vs E0 replay) |
|---|---|---|---|---|
| **M-0R-A** | **Greenfield** OpenAI-compatible embeddings client (none exists in `llm/client.py`); rewire `app.py:_embed` behind `EMBEDDING_PROVIDER`; re-embed all 15,341 memories (Kestra job, the long pole); dimension guard; **trace-score passthrough** — surface real retrieval scores in `_view()` traces (`app.py:3069` hardcodes `activation_score=0.0` today) | RC-2; §1.6a activation floor | embeddings provider credential staged in container env (key rotation for the exposed zAI key owned by [HU-2193](/HU/issues/HU-2193)) | Zero sub-floor/0.0-score injections across the replay **as observed in traces with real scores passed through**; on memory-relevant turns (referencing earlier session content or corpus topics) ≥1 above-floor topical hit; top-3 activations pass topical spot-check. Empty retrieval stays valid on smalltalk turns. |
| **M-0R-B** | Port BEAM Arm A current-conversation working memory into the chat path | RC-3 | HU-1911 lane (owner: agent c1fa8720); HU-1840 lineage | Turn-34 "what was the first thing I said to you?" answered correctly ("hey who r u?"); no eviction-class failures at E0 session length. |
| **M-0R-C** | Data-derived essence v1: replace the `voice_instructions` adjective sheet with measured stats — question-rate/turn, sarcasm-marker density, topic map, follow-up-question rate | RC-1; §7 essence-from-measured-stats (prompt-engineering ban) | none; consumes corpus + E0 | Blind A/B beats the hand-written sheet on E0 script; follow-up-question rate lands inside the measured corpus band (attacks the one-way verdict with corpus numbers, not prompting vibes). |
| **M-0R-D** | Generator A/B on the dogfood lane: glm-5.3 vs Gemini relay vs openweight | RC-5; PERSONA_MODEL_STRATEGY.md | **Scope note:** openweight self-hosted arm stays gated on the board hosting decision (not yet made); the two relay arms can run immediately | Blind preference rating on the same 34-turn script replayed per arm. |
| **M-0R-E** | In-world era clock + caretaker-channel escape hatch | RC-4; era wall stays intact | spec lands now; build after A–C | Date/time-class questions answered via the caretaker channel without in-character break; G-stack + sitcom wall regression-clean on E0 replay. |

**CA conditions on this table (comment 379105f9; folded v1.8, hygiene v1.8.1 via [HU-2328](/HU/issues/HU-2328)):**
- **C1 — promotion gate on rows C and D:** blind A/B (C) or blind preference (D) alone never promotes an essence-injection or generator change. Promotion requires the 5-probe crisis battery (`scripts/ca_crisis_5probe.py` @ `cdce38d`) re-run clean on the candidate config before any dogfood-lane flip. Precedent: epoch/model changes re-open crisis coverage — [HU-2216](/HU/issues/HU-2216) recall gap, [HU-2287](/HU/issues/HU-2287)/[HU-2300](/HU/issues/HU-2300) re-verification chain.
- **C4 — per-fix G-stack gate:** "G-stack regression-clean on E0 replay" (keep-list firing pattern: G1/G6/G8, sitcom wall, reply-length discipline, latency) is part of **every** row's measured gate, not only E's — A adds new in-context content near distress turns, B lengthens in-session history, C injects new voice/stats.
- Executed as W-steps, the same conditions bind via §1.7.2 (C1 → W3/W6, C2 → W5 caretaker spec below, C3 → W1, C4 → every step). Subtask specs cut at freeze inherit these verbatim.

**Caretaker channel (minimal spec, new in v1.5):** an out-of-persona, clearly-labeled channel answering only "what day/time is it"-class questions from real clocks. It never speaks in-voice, never feeds the persona corpus, and does not pierce the era wall — the persona's world stays pre-2004; real-time facts ride the caretaker. This resolves the persona-0 "real-time" tension without doctrine breach. **C2 (CA, v1.8):** the caretaker channel stays inside the G-path — its input path keeps G1/G6/G8 executing on every user turn; a crisis disclosure arriving at the caretaker channel routes to G1 handling, never to a date/time non-answer. Out-of-voice ≠ out-of-safety-stack.

**Sequencing (v1.6 — build order A → C → B → D → E; superseded by W1–W6 in §1.7.2):** A's re-embed job (~15.3k memories) is the long pole — it starts first, and C (small, independent diff) proceeds while it runs; B ports BEAM Arm A into the `HISTORY_WINDOW` at `persona/context.py:475` once the HU-1911 lane lands (owner: agent c1fa8720; HU-1840 lineage); D relay arms are env-swap + E0 replay any time after A; E is greenfield after A–C. No fix counts without a measured E0 delta.

**M-0R exit criterion (answers the owner verdict directly):** after M-0R, a blind A/B on an E0-style script must show the vault-conditioned persona **beating a plain-LLM character sheet**. "Any AI can roleplay Chandler better" is the null hypothesis; the vault engine ships only when it falsifies it. Until then M-0 stays open — the engine is not yet adding value.

## 1.7. E4 smoking gun + V2 build order W1–W6 (R&D, 2026-08-31 evening; folds comment acd2eed0)

### 1.7.0 E4 — smoking gun verified (closes RC-2 at the source)

Independent R&D verification of the Pat + JARVIS finding, against the repo and the live `.245` container:

- `.env:12` — `EMBEDDING_PROVIDER=` (blank), while `EMBEDDING_MODEL=text-embedding-3-small` is already staged at line 15. `settings.py:352-360` `_normalize_embedding_provider()` silently maps blank → `"fake"` ("not configured" → key-free default). No warning ever surfaced at boot or deploy.
- The provider setting is **dead config**: `embedding_provider` has no consumer beyond the settings validator — no embeddings client exists anywhere (`llm/client.py` ships generation clients only), and the chat query path hardwires token-hash embedding: `app.py:2318 _embed()` → `conversation.py:32 simple_embedding(message, dim=1536)`, matching the `Vector(1536)` columns at `memory/models.py:128-129`.
- Consequence: since launch, every chat turn ranked the corpus by keyword-token-hash cosine — semantically blind retrieval. **The 15,341-memory vault was never semantically read.** This explains the E1/E2 zero-relevance activations and the 0.0 activation scores (the trace passthrough itself is hardcoded — E3).
- Live count re-verified: **15341** rows in `memories` (huible-postgres, 2026-08-31 20:19Z).
- Doctrine ruling (owner): duct-tape banned — fix the foundation (W1), not the symptom. RC-2 graduates from "confirmed" (E3) to **closed-by-mechanism**; remediation tracking continues as W1+W2.

### 1.7.1 V2 target architecture (owner spec, verbatim intent — replaces the character-sheet engine)

1. **Personas READ vaults.** Emulation grounded in vault text, audio, video, photos — not description cards. Model-choice-agnostic: a modest model converses well because the DATA carries the persona (owner: "even a dumb AI can conversate effectively; we don't need the smartest models, and even then it's not beneficial in my test").
2. **Cross-vault leverage.** Persona vault + EQ/emotions vault + career vault etc. (EQ-Vault exists: FNS id 4; pattern proven). Vault isolation is preserved — a persona's reads fan out across its own vault set; persona truths never pollute other vaults (§0.2).
3. **Tool calling.** Date/time, current events, persona hobbies/interests — the persona-0 spec "knows real-time stuff" finally implemented. Era-gated; out-of-era facts ride the caretaker channel (§1.6b), never in-voice.
4. **TencentDB as REAL working memory, not a context window.** BEAM Arm A port (green-lit Aug 19, never landed) kills the `HISTORY_WINDOW` eviction failure (RC-3: "what was the first thing I said?" wrong answer at minute 7).
5. **North-star eval — THE FIVE-FRIENDS TEST** (§1.7.4): five characters conversing with each other, each grounded ONLY in its own vault's knowledge of actual events/world, none told they are Friends characters; their emergent dialog vs an AI-generated sitcom dialog from a top model, blind-rated. The moat made measurable.

### 1.7.2 Build order W1–W6 (fix-the-foundation, cheapest-measurable-first) — R&D-annotated

| Wn | Scope | Maps to | R&D annotation |
|---|---|---|---|
| **W1** | Real embeddings end-to-end: greenfield embeddings client, rewire `app.py:_embed` behind it, dimension migration for both `Vector(1536)` columns, full re-embed of 15,341 memories, query-path dims, trace-score passthrough | M-0R-A | **R&D option scoring (cost doctrine — reuse subs, no new spend):** (a) local CPU ONNX embedder in-container (bge-small-en-v1.5 / all-MiniLM-L6-v2 — 384-dim): zero marginal cost, offline, no quota coupling to chat, deterministic — **R&D recommends (a)**. (b) z.ai embeddings on the existing coding plan: `/embeddings` availability on the coding base URL unverified, and it couples retrieval to the ZAI_DAILY_TOKEN_LIMIT chat budget. (c) Gemini embedding via home-relay sub quota: adds a relay dependency to the retrieval hot path. **TL pick (2026-08-31, v1.8): option (a) ACCEPTED — local CPU ONNX `bge-small-en-v1.5` (384-dim); reasons: zero marginal cost, offline + deterministic, no quota coupling to the chat budget.** Migration note: whichever model wins, 1536→N means both columns + full re-embed + query dims cut over in one window (no mixed-dim state). **C3 (CA):** the activation floor is per-class and corpus-derived — the Chandler-pilot floor value must be re-derived for Class B corpora (small, intimate corpora have different score distributions) before any client-persona vault activates retrieval; empty-retrieval-valid stays a first-class state for Class B (B2 hedging with essence-only response is the compliant behavior when nothing clears the floor). |
| **W2** | Lexical lane in parallel: Postgres FTS (`websearch_to_tsvector`) over memories; RRF-fuse with the vector lane | NEW (extends W1's retrieval) | Zero new infra; catches exact-topic matches embeddings miss (proper nouns, surnames — directly attacks the surname-intro micro-tell class). Mirrors the BEAM v4 Arm C RRF pattern. |
| **W3** | Description-free prompt: delete the `voice_instructions` adjective sheet from the render path (`persona/context.py:422-423`); prompt = safety framing (clinically required, stays) + retrieved real exemplar lines + exchange. Competence wall: out-of-domain input retrieves deflection-pattern exemplars instead of letting base-model skills leak (kills the Python-syntax tell) | **Supersedes M-0R-C's render approach** | Measured stats (M-0R-C work) survive as the measurement/eval layer — §7 essence profile, follow-up-question bands — conditioning retrieval and eval, never rendered as adjectives. **C1 (CA):** blind A/B alone never promotes this render change — promotion requires the 5-probe crisis battery (`scripts/ca_crisis_5probe.py` @ `cdce38d`, the productized HU-2216/HU-2300 battery; C6 runbook epoch-drift path) re-run on the candidate epoch before any dogfood-lane flip. |
| **W4** | BEAM Arm A working-memory port into the chat path | M-0R-B | TencentDB as real working memory (V2 point 4); target stays `persona/context.py:475 HISTORY_WINDOW`; gate unchanged (turn-34 first-thing-I-said answered correctly). |
| **W5** | Tool calls: in-world era clock + current-events/hobby tools (era-gated) | M-0R-E (extended to hobbies/interests) | Caretaker channel spec (§1.6b) governs out-of-persona facts; hobby tools read the vault-derived interest/topic map (W1 retrieval feeds it). **C2 (CA):** the caretaker channel stays inside the G-path — G1/G6/G8 execute on every user turn including caretaker-routed input; a crisis disclosure arriving at the caretaker channel routes to G1 handling, never to a date/time non-answer (out-of-voice ≠ out-of-safety-stack). |
| **W6** | Replay Pat's exact 34-turn E0 session before/after; micro-tell baseline must VANISH; then the five-persona blind test | M-0R exit criterion + M-E | Micro-tell baseline (from E0): surname intro, code fluency, AI self-reference, zero follow-up questions. Owner blind-judges the replay (owner help request 1). **C1 (CA):** any generator-swap arm (folded M-0R-D) additionally requires the crisis-probe battery re-run clean on the candidate config before any dogfood-lane flip — blind preference alone never promotes. |

**M-0R-D disposition:** retained as the falsification arm of the engine-added-value exit criterion and now runs inside W6 — the V2 claim "the data carries the persona, not the model" is exactly what the modest-model arm must prove. The openweight self-hosted arm stays gated on the board hosting decision.

**Gates:** every W-step counts only with a measured E0-replay delta; §1.6b gates apply verbatim where mapped. **C4 (CA, per-step):** every W-step's measured gate additionally includes "G-stack regression-clean on E0 replay" — keep-list firing pattern (G1/G6/G8, sitcom wall, reply-length discipline, latency) — explicit for every step, not only the tool-call step. **C1 (CA):** W3 render changes and W6 generator swaps additionally require the crisis-probe battery re-run clean on the candidate config before any dogfood-lane flip.

### 1.7.3 Mapping to §1.6b

- M-0R-A → executed as **W1+W2** (real embeddings + lexical lane; RRF fusion is part of the retrieval gate; trace-score passthrough rides W1).
- M-0R-C → measured-stats work survives; **render approach superseded by W3** (description-free prompt).
- M-0R-B → **W4** unchanged · M-0R-E → **W5** extended · M-0R-D → folded into **W6**.

### 1.7.4 Five-Friends Test — spec v0 (R&D draft; owner design eye requested)

- **Setup:** five personas, each grounded ONLY in its own vault's knowledge of actual events/world; none told they are Friends characters; free multi-persona emergent conversation. Comparator: an AI-generated sitcom dialog from a top model given only character descriptions.
- **Blind rating:** raters see transcript pairs (ours vs comparator) without provenance; owner + blind judges (owner help request 2).
- **Draft rubric axes (v0):**
  1. **Blind attribution** — raters attribute lines to the correct persona above chance (voice distinctiveness; the anti-mush metric).
  2. **Grounding** — persona statements reference events/world consistent with its own vault (essence-over-accuracy: wrong-on-facts is fine when corpus-faithful; contradictions of the persona's OWN vault-known world are not).
  3. **Emergence** — novel situations handled in-character (argues, doesn't budge, deflects — per reference corpus).
  4. **Two-way engagement** — follow-up-question/curiosity rates land inside measured corpus bands (extends the E0 one-way verdict metric to multi-persona).
  5. **Preference** — blind head-to-head vs the comparator transcript.
- **Class scope:** all five personas are Class A (§7c) — full doctrine, no Class-B gates apply.
- **Sequencing:** W6 single-persona E0 replay clean first, then five-friends.

## 2. Integration Spikes (HU-1839)
The V1 Ingest stage will hook into HU-1839's S0-S5 pipeline.

## 3. Next Steps
Design updated to v1.8 (freeze candidate): v1.7 folds the E4 smoking-gun verification (RC-2 closed at the source — blank `EMBEDDING_PROVIDER`, dead provider config, token-hash retrieval since launch), the owner's V2 target architecture (§0.4, §1.7.1), and the W1–W6 build order with R&D annotations (§1.7.2; W3 description-free render supersedes the character-sheet approach; §1.7.4 Five-Friends rubric v0 draft); v1.8 (TL) folds the CA delta-audit conditions C1–C4 into the W-table gates and records the TL W1 embedder pick — local CPU ONNX `bge-small-en-v1.5`, 384-dim (R&D option (a) accepted). CA position on sign-off: no objection — gate refinements, not doctrine changes. Requesting Pat's sign-off on v1.8. Owner help requested per comment acd2eed0: (1) blind-judge the W6 replay when ready; (2) design eye on the Five-Friends rubric; (3) nothing else — no spend needed for W1–W3. On acceptance: Librarian cuts the official vault doc; Tech Lead builds W1–W6 (Kestra-orchestrated; Antigravity writes flow code); the board hosting decision still gates the openweight arm (now inside W6). Implementation subtasks are created only after acceptance.

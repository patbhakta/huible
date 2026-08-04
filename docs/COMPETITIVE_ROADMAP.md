# Huible Roadmap — Prioritized from Competitor R&D Analysis

**Issue:** [BHAA-1326](/BHAA/issues/BHAA-1326)
**Author:** R&D Lead
**Sources:**
- **Layla Network** (3+ year development lead): v6.6 through v7, 9 releases over 4.5 months (Feb–Jul 2026). Local analysis source: `/root/layla_research/layla_network_analysis.md`.
- **LettuceAI** (AGPL-3.0, engine + app): Architecture analyzed in [BHAA-1325](/BHAA/issues/BHAA-1325) → `docs/LETTUCEAI_ANALYSIS.md`.

Both are companion/roleplay apps. Neither is a direct competitor. But their battle-tested R&D shortcuts our roadmap.

---

## LESSONS THAT RE-PRIORITIZE OUR BUILD ORDER

### 1. Real-Time Memory Ingestion (DE-PRIORITIZED)
**Layla's journey:** Started with overnight batch LTM ingestion → migrated to real-time in v7 via a custom BART model.

**Impact on Huible:** We don't need real-time on day one. Layla shipped batch-mode for 4+ months across 6 versions before real-time worked. Our SMS use case naturally has latency tolerance (conversations aren't real-time chat — they're async text messages).

**Action:** Build batch ingestion first (Phase 1). Real-time can come in Phase 3+ when we have data to train on.

### 2. Feedback Loop Prevention (PROMOTED TO P0)
**Layla's lesson:** In v7 they had to fix recall to stop pulling most-recent same-session memories — it caused feedback loops where the persona kept referencing the same recent memory.

**Impact on Huible:** This is exactly the kind of thing that would make a reconstructed persona sound broken. If Pat's persona keeps bringing up the last thing mentioned, it fails the "feels like Dad" test.

**Action:** Add feedback loop prevention to the spreading activation algorithm. Never surface a memory that was activated in the last N turns. Add to F1 fixture requirements.

### 3. Structured Memory Layer (VALIDATED)
**Layla's insight (v6.7):** Their STM layer — programmable, structured, between raw context and LTM — was their "most sophisticated memory architecture decision."

**Impact on Huible:** Our provenance tiering (canonical/derived/accrued/world) is even more structured than Layla's STM. This validates our architecture. We're not over-engineering — the market proved structured memory layers are essential.

**Action:** Keep the four-tier system as designed. This is a competitive moat Layla still doesn't have after 3 years.

### 4. Temporal Knowledge Graph (PROMOTED)
**Layla v7:** Added temporal nodes and time links to their knowledge graph. Message deletion/regeneration now propagates to memory.

**Impact on Huible:** Critical for our use case — the persona must know what happened before and after death (INV-1: knowledge boundary). Temporal modeling isn't optional for us.

**Action:** Ensure schema has temporal metadata on every memory node. The valid_from/valid_to fields in our schema already handle this.

### 5. Domain-Specific Model Training (LONG-TERM, NOT P0)
**Layla:** Trained custom BART model on distilled roleplay data for their memory pipeline.

**LettuceAI:** Trained custom embedding model (lettuce-emb-v4) achieving 0.924 recall@1 on roleplay memory — 46x improvement over generic models.

**Impact on Huible:** Eventually we'll want purpose-trained models for: (a) message-to-memory extraction, (b) sensory/affect embedding, (c) summarization tuned for bereavement context. But this requires data first.

**Action:** Phase 4+ goal. Phase 1 uses off-the-shelf embeddings (OpenAI/Sentence-Transformers). Start collecting training data from day one.

### 6. Memory Pipeline Replaceability (ARCHITECTURE NOTE)
**Layla v7:** Memory pipeline is fully replaceable via SDK — any mini-app can swap the ingestion strategy.

**Impact on Huible:** Our MemoryBackend protocol already achieves this. PostgresBackend now, GraphitiBackend later, same interface. Validated design.

---

## REVISED PHASE PRIORITIES

### Phase 1 (CURRENT — Next 2 Weeks)
**Focus: Core retrieval engine — the F1 test**
- Multi-index extraction (content + sensory + affect embeddings)
- Spreading activation traversal
- Motif tracking with directional constraints
- Feedback loop prevention (learned from Layla)
- Disclosure-scoped edge traversal
- **Exit gate:** F1 passes (cross-topic motif escalation)

### Phase 2 (Weeks 3–4)
**Focus: Disclosure + situational grounding**
- Relationship-scoped disclosure (F2)
- Situational state with commitments (F3)
- Callback resolution (F4)
- Batch memory ingestion pipeline

### Phase 3 (Weeks 5–8)
**Focus: Stability + safety**
- Entrenchment-based preference stability (F5)
- Open loop detection and proactive recall (F6)
- Canonical immutability guards (F7)
- Knowledge boundary enforcement (F8)
- Safety/crisis system (clinical advisor input needed first — [BHAA-1324](/BHAA/issues/BHAA-1324))

### Phase 4 (Months 3–6)
**Focus: Production hardening**
- WhatsApp/SMS delivery layer (Twilio or WhatsApp Cloud API)
- Real-time memory processing (learned from Layla v7)
- Voice output (TTS with persona voice cloning)
- Temporal knowledge graph enrichment
- Custom embedding model evaluation

### Phase 5 (Future)
- Purpose-trained extraction/summarization models
- Multi-persona support (multiple deceased persons in one deployment)
- App/web interface for families
- Clinical evaluation framework

---

## WHAT WE'RE SKIPPING (AND WHY)

- **Multi-LLM conversations** (Layla v6.12) — single persona, single voice. Not relevant.
- **Image generation** (Layla v6.10) — bereavement use case doesn't need this.
- **3D/VRM models** (Layla features) — SMS-first, no visual layer needed.
- **App store presence** (Layla v6.12.3 pain) — WhatsApp delivery sidesteps this entirely.
- **Companion mode/screen overlay** (Layla v6.9) — different product entirely.

---

## COMPETITIVE MOAT (What Neither Competitor Has After 3+ Years)

1. **Provenance-tiered memory** — canonical/derived/accrued/world. Layla still has flat LTM.
2. **Motif-directed multi-dimensional retrieval** — not flat cosine similarity.
3. **Relationship-scoped disclosure** — persona modulates based on WHO it's talking to.
4. **Entrenchment-based preference stability** — preferences deepen over time like a real person.
5. **Reconstruction from data** — not authored characters. Discovered, not declared.
6. **SMS/WhatsApp native** — zero friction, no app install, no store policies.

These are the six pillars. Everything else is implementation detail.

---

## CROSS-SOURCE SYNTHESIS (Layla + LettuceAI)

Convergence from both competitors independently validates Huible's core bets:
- **Structured, programmable memory layer** (Layla STM + Lettuce's memory consolidation) → Huible provenance tiers.
- **Temporal / relationship-aware memory** (Layla temporal KG + Lettuce per-user relationship state) → Huible temporal metadata + relationship-scoped disclosure.
- **Purpose-trained retrieval** (Lettuce lettuce-emb-v4 46x gain; Layla custom BART) → Huible Phase 4 custom-embedding goal.

Hard divergence (do NOT copy): both competitors prune/delete faded memories. Huible is append-only (INV-16) — decay applies to *retrieval salience only*, never as a delete action. This is non-negotiable for a real person's legacy.

**Gaps this roadmap exposes (carry into implementation):**
1. LLM integration + API server (Tier-1 voice) — "brain with no mouth."
2. Advisory/safety + distress escalation before/with voice (must not ship without it) — needs clinical advisor input ([BHAA-1324](/BHAA/issues/BHAA-1324)).
3. Real emotion detection (structured vector; user-facing for safety, persona-facing for texture).
4. Retrieval fusion (dense + BM25) onto spreading activation.
5. Voice/consistency + knowledge-boundary validation when generation lands.

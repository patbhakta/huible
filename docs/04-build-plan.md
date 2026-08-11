# [04] Build Plan

Phased build plan for the Huible memory-driven persona engine. Phases are sequential — each phase's exit gate must pass before the next phase begins. Each task below carries explicit acceptance criteria so "done" is unambiguous.

**Source:** Extracted and reorganized from `docs/ENGINE_SPEC.md` §9 and `README.md` §"Phase 1 Scope". This document is the canonical build plan; the ENGINE_SPEC sections remain the architectural source of truth.

---

## Guiding Principle

Huible is built **memory-engine-first**. We prove the memory brain works (retrieval, ingestion, quality control) *before* attaching a voice. The persona generator is intentionally deferred — a smart model with a broken memory system produces a bad persona; a great memory system with no voice is still a provable foundation.

> **Defining test:** "Does this feel like talking to Dad?" — not "Is this a helpful AI?"

---

## Phase Roadmap

| Phase | Name | Exit Gate | Status |
|-------|------|-----------|--------|
| **Phase 1** | Memory Engine | F1 — retrieval validation | In progress |
| Phase 2 | Persona Voice | Voice-test harness | Planned |
| Phase 3 | Production & Delivery | Live SMS conversation | Planned |

---

## Phase 1 — Memory Engine (Exit Gate: F1)

Phase 1 succeeds when the engine can demonstrate ALL of the following retrieval criteria. F1 is a **retrieval test, not a conversation test** — we prove the memory brain works before attaching a voice.

### F1 Exit Gate Criteria

| # | Criterion | Verification Method |
|---|-----------|-------------------|
| F1.1 | Index a corpus of memories with multi-vector embeddings | Load 1000+ synthetic memories; verify all three embedding types stored |
| F1.2 | Retrieve via spreading activation (not just cosine similarity) | Query must traverse edges, not just hit nearest neighbors |
| F1.3 | Suppress feedback loops | Same-query-second-time retrieval returns different results (suppressed recent) |
| F1.4 | Scope by disclosure tier | Private memories excluded when querying as 'acquaintance' tier |
| F1.5 | Cross-topic motif escalation | Activated nodes include motif-boosted cross-topic memories |

> See `docs/f1_gate_report.md` for current F1 results and `docs/06-testing-strategy.md` for the full gate-suite catalog.

### Phase 1 Task Breakdown

#### Task 1.1 — Database schema + migrations
**Deliverable:** Canonical PostgreSQL 18 + pgvector schema with all tables, indexes, constraints, and seed data.

- [ ] `migrations/schema.sql` is the source of truth (tables: `memories`, `memory_edges`, `quarantine`, `personas`)
- [ ] Multi-vector HNSW indexes on `embedding_content` (1536d), `embedding_sensory` (1536d), `embedding_affect` (512d)
- [ ] Active-memories partial index on `(persona_id, is_active)` for query efficiency
- [ ] Alembic migrations derive from schema.sql, not vice versa
- [ ] Seed script (`scripts/seed_data.py`) generates a synthetic persona + 1050 memories + 3000 edges

**Acceptance criteria:** Schema applies cleanly to an empty Postgres 18 + pgvector database; all indexes build successfully; seed script runs to completion without errors.

---

#### Task 1.2 — Memory ingestion pipeline
**Deliverable:** Load memories, generate multi-vector embeddings, store nodes + edges.

- [ ] `MemoryBackend` protocol (`huible.memory.protocol`) implemented by `PostgresBackend` and an in-memory test backend
- [ ] All three embedding vectors (content, sensory, affect) generated and persisted per node
- [ ] Edge creation for shared-participant, temporal-proximity, thematic, causal, contradiction, and elaboration relations
- [ ] Async throughout (`asyncio`)

**Acceptance criteria:** A candidate memory submitted to the pipeline is persisted with all three embeddings and at least one edge; retrieval queries find it back by content.

---

#### Task 1.3 — Spreading activation retrieval
**Deliverable:** Implement the full 5-step algorithm (seed activation → feedback suppression → spreading → motif escalation → disclosure filtering).

- [ ] Multi-vector nearest-neighbor seeds (top-k=20)
- [ ] Feedback-loop suppression of the last 10 turns (`SUPPRESSION_WINDOW = 10`, suppress factor 0.1)
- [ ] Graph spread to `MAX_SPREAD_DEPTH = 3` with `DECAY_FACTOR = 0.6`
- [ ] Motif escalation: clusters of ≥3 same-theme nodes get 1.3× activation boost
- [ ] Disclosure filtering applied **last** (after activation ranking) to preserve ranking quality
- [ ] `ACTIVATION_THRESHOLD = 0.3`, `MAX_ACTIVATED = 50`

**Acceptance criteria (F1.2, F1.3, F1.5):** Retrieval traverses edges (not just nearest neighbors); a second identical query returns different results than the first; cross-topic motifs appear in the activated set. Verified by `tests/f1/`.

---

#### Task 1.4 — Five-gate firewall (INV-15)
**Deliverable:** All 5 gates with Tier 2 model integration. Every candidate must pass ALL gates before entering the graph.

- [ ] **SAFETY** — prompt-injection / adversarial detection; `FAIL` → rejected, `AMBIGUOUS` → quarantine (high)
- [ ] **DEDUPLICATION** — cosine similarity > 0.92 against existing memories; near-duplicates rejected
- [ ] **NOVELTY** — must connect to ≥1 existing node; `AMBIGUOUS` → quarantine (medium)
- [ ] **IMMUTABILITY** — must not conflict with canonical tier; `AMBIGUOUS` → quarantine (high, family adjudication)
- [ ] **PERTINENCE** — must add character/relationship/biographical depth; `AMBIGUOUS` → quarantine (low)
- [ ] Each gate returns exactly one of `PASS`, `FAIL`, `AMBIGUOUS`

**Acceptance criteria (INV-15):** No code path writes a memory to the graph without all five gates returning `PASS`; ambiguous candidates route to the quarantine queue, never auto-accepted. Verified by `tests/f2/`.

---

#### Task 1.5 — Quarantine queue (INV-16)
**Deliverable:** Priority queue with routing and adjudication interface.

- [ ] `quarantine` table with `failed_gates[]`, `priority`, `status` (`pending`/`adjudicated`/`promoted`/`rejected`)
- [ ] Priority routing: critical (safety) → high (canonical conflict, family) → medium (novelty) → low (pertinence)
- [ ] Adjudication API (`huible.api.adjudication`) for family/reviewer review

**Acceptance criteria:** An ambiguous SAFETY candidate jumps the queue ahead of a low-priority PERTINENCE candidate; an adjudicated candidate is either promoted into the graph or rejected with an audit trail. Verified by `tests/f3/`.

---

#### Task 1.6 — Disclosure scoping (INV-DS)
**Deliverable:** Relationship tier → memory access filtering in the context builder.

- [ ] `disclosure_scope` column on `memories` (`private` / `family` / `close_friends` / `all_contacts`)
- [ ] Context builder filters activated memories by the requester's relationship tier
- [ ] Private memories excluded for non-family tiers

**Acceptance criteria (F1.4):** Querying as an 'acquaintance' tier never returns `private`-scoped memories. Verified by `tests/f1/test_f1_4_disclosure_scoping.py` and `tests/f7/`.

---

#### Task 1.7 — F1 test suite + gate report
**Deliverable:** Automated test harness validating all 5 F1 criteria + a generated gate report.

- [ ] `tests/f1/` covers F1.1–F1.5 with criterion tests + performance benchmarks
- [ ] Synthetic corpus generator (`tests/f1/corpus.py`): 1050 memories, 3000 edges
- [ ] `report.py` emits `docs/f1_gate_report.md` + `docs/f1_gate_report.json`
- [ ] All F1 tests green

**Acceptance criteria:** `pytest tests/f1/` exits 0; the generated gate report shows PASS on all five criteria; the report is committed to `docs/`.

---

### Phase 1 Out of Scope

The following are explicitly **deferred** to later phases and must not be built during Phase 1:

- Persona generator integration (voice model) — Phase 2
- Real-time SMS integration — Phase 3
- Production deployment — Phase 3
- Family-facing admin UI — Phase 3+
- Multi-persona support — Phase 3+

### Phase 1 Scope Summary (quick reference)

> Phase 1 is the memory engine — proving retrieval, ingestion, and quality control work before attaching a voice.
>
> **In scope:** Database schema, memory ingestion, spreading activation retrieval, five-gate firewall, quarantine queue, disclosure scoping, context builder, F1 exit gate.
>
> **Out of scope:** Persona generator integration, real-time SMS, production deployment, family admin UI, multi-persona support.

---

## Phase 2 — Persona Voice (Exit Gate: Voice Test)

> **Planned, not yet scoped.** Placeholder — to be expanded into tasks with acceptance criteria when Phase 1 exits.

Phase 2 attaches a Tier 1 persona generator (openweight 7B–24B, self-hosted, uncensored) to the Phase 1 memory engine via the Context Builder. The exit gate is a **voice test**: does the output feel like talking to the deceased person, not like a helpful AI.

**Anticipated workstreams:**

- Persona generator selection and hosting (llama.cpp / vLLM)
- Context Builder prompt assembly (system prompt + memory blocks + history + constraints)
- Knowledge-boundary enforcement (era/date filters — INV-1)
- Voice-test harness design

**Entry criterion:** Phase 1 F1 exit gate fully green and committed.

---

## Phase 3 — Production & Delivery (Exit Gate: Live SMS Conversation)

> **Planned, not yet scoped.** Placeholder.

Phase 3 puts a real persona on the wire: SMS via Twilio, production deployment, family-facing admin UI for adjudication and canonical promotion.

**Anticipated workstreams:**

- Twilio SMS integration (async, not real-time chat)
- Production deployment (Docker, Caddy, Tailscale) — see `docs/09-deployment-ops-guide.md`
- Family admin UI for quarantine adjudication and canonical-tier promotion
- Multi-persona support

**Entry criterion:** Phase 2 voice test passed.

---

## Cross-Cutting Invariants (Hard Rules — All Phases)

These invariants hold across every phase and must not be violated by any task:

| Invariant | Rule | Owner |
|-----------|------|-------|
| **INV-1** | Knowledge boundary — persona must not know things the person couldn't have known | Context builder + advisory model |
| **INV-15** | Five-gate ingestion — no memory enters the graph without passing all gates | Ingestion pipeline |
| **INV-16** | Append-only versioning — memories are superseded, never overwritten | DB constraint + app layer |
| **INV-FL** | Feedback loop prevention — recently activated memories suppressed in retrieval | Spreading activation |
| **INV-DS** | Disclosure scoping — relationship tier determines memory access | Context builder |
| **INV-CI** | Canonical immutability — ground-truth facts never modified by ingestion | Gate 4 + DB trigger |

---

## Source Documents

| Document | Location |
|----------|----------|
| Engine Specification (architectural source of truth) | `docs/ENGINE_SPEC.md` |
| Testing Strategy (gate-suite catalog) | `docs/06-testing-strategy.md` |
| Product Definition | `docs/08-product-definition.md` |
| API Specification | `docs/07-api-specification.md` |
| Deployment & Ops Guide | `docs/09-deployment-ops-guide.md` |
| F1 Gate Report (latest results) | `docs/f1_gate_report.md` |

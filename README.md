# Huible — Memory-Driven Persona Engine

Huible reconstructs a deceased person as a conversational persona with real memory, personality, and relationship awareness. It is a memory-driven persona engine where the model's job is to *voice* the person, not *be* smart.

**Defining test:** "Does this feel like talking to Dad?" — not "Is this a helpful AI?"

## Quick Start

```bash
# Install dependencies (Python 3.12+)
pip install -e ".[dev]"

# Run the test suite
pytest

# Run a specific gate suite
pytest tests/f1/          # F1: Memory retrieval exit gate
pytest tests/f2/          # F2: Five-gate firewall
pytest tests/f3/          # F3: Quarantine queue
pytest tests/f4/          # F4: Memory versioning
pytest tests/f5/          # F5: Context builder
pytest tests/f6/          # F6: Ingestion pipeline
pytest tests/f7/          # F7: Disclosure scoping
pytest tests/f8/          # F8: Benchmarks

# Seed a test database (requires PostgreSQL + pgvector)
python -m scripts.seed_data --url postgresql://postgres:postgres@localhost:5432/huible

# Lint
ruff check .
```

## Project Index

### Source — `src/huible/`

| Package | Purpose | Key Files |
|---------|---------|-----------|
| `huible/memory/` | Memory graph models, storage protocol, retrieval | `models.py` — Pydantic memory node/edge schemas; `protocol.py` — `MemoryBackend` trait; `retrieval.py` — spreading activation algorithm; `store.py` — in-memory and Postgres implementations |
| `huible/ingestion/` | Five-gate firewall, extraction, quarantine | `pipeline.py` — gate orchestrator; `gate_safety.py`, `gate_dedup.py`, `gate_novelty.py`, `gate_immutability.py`, `gate_pertinence.py` — individual gates; `extractor.py` — memory candidate extraction; `quarantine.py` — priority queue; `writer.py` — graph persistence |
| `huible/advisory/` | Tier 2 advisory model interface (stub) | — |
| `huible/api/` | HTTP endpoints for adjudication | `adjudication.py` — quarantine review API |
| `huible/persona/` | Persona generator integration (Phase 2) | Empty — out of scope for Phase 1 |

### Tests — `tests/`

| Suite | Scope | Files |
|-------|-------|-------|
| `tests/f1/` | **F1 Exit Gate** — memory retrieval validation | `corpus.py` — synthetic corpus generator (1050 memories, 3000 edges); `test_f1_1_indexing.py` through `test_f1_5_motif_escalation.py` — 30 criterion tests; `test_f1_benchmarks.py` — 7 performance benchmarks; `report.py` — gate report generator |
| `tests/f2/` | **F2** — five-gate firewall unit tests | `test_f2_1_safety_gate.py` through `test_f2_5_pertinence_gate.py`; `test_f2_6_pipeline_integration.py` |
| `tests/f3/` | **F3** — quarantine queue | `test_f3_quarantine.py` |
| `tests/f4/` | **F4** — memory versioning (append-only) | `test_f4_versioning.py` |
| `tests/f5/` | **F5** — context builder (prompt assembly) | `test_f5_context_builder.py` |
| `tests/f6/` | **F6** — end-to-end ingestion pipeline | `test_f6_ingestion_pipeline.py` |
| `tests/f7/` | **F7** — disclosure tier scoping | `test_f7_disclosure.py` |
| `tests/f8/` | **F8** — performance benchmarks | `test_f8_benchmarks.py` |

Root-level tests: `test_models.py`, `test_store.py`, `test_retrieval.py`, `test_pipeline.py`, `test_ingestion.py`.

### Database — `migrations/`

| File | Purpose |
|------|---------|
| `schema.sql` | Canonical DDL — all tables, indexes, constraints. Source of truth; Alembic migrations derive from this. |
| `script.py.mako` | Alembic migration template |
| `versions/` | Generated Alembic revision scripts |

### Documentation — `docs/`

#### Numbered spec suite ([00]–[09])

| File | Purpose |
|------|---------|
| `04-build-plan.md` | [04] Phased build plan with acceptance criteria (Phase 1 F1 exit gate) |
| `06-testing-strategy.md` | [06] F1–F8 gate suites + invariant → test matrix |
| `07-api-specification.md` | [07] REST API specification v1.0 |
| `08-product-definition.md` | [08] What Huible is / is not, defining test, product invariants |
| `09-deployment-ops-guide.md` | [09] Deployment & operations runbook |

#### Core specs & reports

| File | Purpose |
|------|---------|
| `ENGINE_SPEC.md` | Full engine specification v1.0 — architecture, memory model, gates, invariants, tech stack, phase plan |
| `f1_gate_report.md` | F1 exit gate results (37/37 PASS) |
| `f1_gate_report.json` | Machine-readable F1 results |

### Scripts — `scripts/`

| File | Purpose |
|------|---------|
| `seed_data.py` | Generate and insert synthetic persona + 1050 memories + 3000 edges for testing |

## Architecture

Two-tier design separating the persona voice from the advisory brain:

```
[Tier 2: Advisory] ──memory nodes──> [Context Builder] ──prompt──> [Tier 1: Persona Generator]
                                                                ──response──> [Client]
                   <──memory candidates── [Extraction Pipeline] <──conversation──
```

- **Tier 1 (Persona Generator):** Openweight 7B–24B model, self-hosted, uncensored, outputs only response text
- **Tier 2 (Advisory Layer):** Frontier models (Claude Opus, GPT), internal-only, handles extraction and gate adjudication
- **Context Builder:** Bridges tiers — retrieval results → structured prompt with disclosure scoping

## Memory Architecture

Four provenance tiers, each with different mutability rules:

| Tier | Description | Mutability |
|------|-------------|------------|
| **Canonical** | Ground-truth facts (bio, relationships, death) | Immutable — family approval only |
| **Derived** | Inferences from canonical + accrued | Supersedable by advisory model |
| **Accrued** | Memories extracted from conversations | Append-only, can promote to canonical |
| **World** | Era-appropriate general knowledge | Static, curated |

Retrieval uses **spreading activation** (graph traversal), not cosine similarity alone. Each memory node carries three embedding vectors: content (1536d), sensory (1536d), and affect (512d).

## Key Invariants

| ID | Rule |
|----|------|
| **INV-1** | Knowledge boundary — persona must not know things the person couldn't have known |
| **INV-15** | Five-gate ingestion — no memory enters the graph without passing all gates |
| **INV-16** | Append-only versioning — memories are superseded, never overwritten |
| **INV-FL** | Feedback loop prevention — recently activated memories suppressed in retrieval |
| **INV-DS** | Disclosure scoping — relationship tier determines memory access |
| **INV-CI** | Canonical immutability — ground-truth facts never modified by ingestion |

## Technology Stack

| Component | Technology |
|-----------|-----------|
| Language | Python 3.12, type-hinted, async throughout |
| Database | PostgreSQL + pgvector (multi-index HNSW) |
| Build | setuptools |
| Linting | ruff (E, F, I, UP, B, SIM, RUF) |
| Testing | pytest + pytest-asyncio |
| Embeddings | Multi-vector per node (content 1536d, sensory 1536d, affect 512d) |
| Migrations | Alembic |

## Conventions

- **Line length:** 100 characters
- **Python target:** 3.12 (py312 in ruff config)
- **Async:** All I/O is async; use `asyncio_mode = "auto"` in pytest
- **Imports:** `from __future__ import annotations` at module top
- **Package layout:** `src/` layout — installed package lives under `src/huible/`
- **Naming:** snake_case for files, modules, functions; PascalCase for classes
- **Memory protocol:** All storage backends implement `MemoryBackend` trait from `huible.memory.protocol`
- **Test naming:** `test_f{N}_{description}.py` for gate-suite tests; descriptive names for root tests
- **Schema:** `migrations/schema.sql` is the source of truth; Alembic migrations derive from it

## Phase 1 Scope

Phase 1 is the memory engine — proving retrieval, ingestion, and quality control work before attaching a voice.

**In scope:** Database schema, memory ingestion, spreading activation retrieval, five-gate firewall, quarantine queue, disclosure scoping, context builder, F1 exit gate.

**Out of scope:** Persona generator integration, real-time SMS, production deployment, family admin UI, multi-persona support.

## What Huible Is NOT

- NOT a therapy chatbot
- NOT a general-purpose AI assistant
- NOT a roleplay/fiction engine (the person was real)
- NOT a RAG system (retrieval is graph-traversal, not document chunks)
- NOT an LLM fine-tune (the model doesn't learn — the memory system does)

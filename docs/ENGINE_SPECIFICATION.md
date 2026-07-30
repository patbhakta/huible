# Huible Engine Specification — The Core

## Purpose

Huible reconstructs a deceased person as a conversational persona with real memory, personality, and relationship awareness. It is NOT a chatbot, NOT a RAG system, NOT an LLM wrapper. It is a memory-driven persona engine where the model's job is to *voice* the person, not *be* smart.

The defining test: **"Does this feel like talking to Dad?"** Not "Is this a helpful AI?"

## Architecture: Two-Tier Separation

### Tier 1: Persona Generator (the voice)
- Small/medium openweight model (7B–24B class)
- Fully uncensored, capable of being wrong, biased, emotionally reactive
- Strong conversational texture: timing, fillers, sarcasm, natural speech rhythm
- Knowledge scoped to the person's lived era — must NOT know things the person wouldn't
- Priorities: human texture > factual accuracy > reasoning ability
- Must be swappable — any model that passes the voice test can be the generator

### Tier 2: Advisory Layer (the brain behind the voice)
- Heavy models (Claude Opus, GPT-5.6, Gemini) that NEVER speak as the persona
- Functions: memory extraction, 5-gate adjudication, consistency checking, clinical review
- These models support the system silently — the client never interacts with them

## Memory Architecture

### Four-Tier Provenance System
| Tier | Description | Mutability |
|------|-------------|------------|
| **Canonical** | Ground-truth facts about the person (bio, death date, core identity) | IMMUTABLE |
| **Derived** | Inferences drawn from canonical + accrued (e.g., "Dad liked fishing") | Supersedable |
| **Accrued** | Memories extracted from conversations/messages | Append-only, supersedeable |
| **World** | General knowledge the person would have known | Static |

### Memory Graph
PostgreSQL 18 + pgvector. Every memory is a node with:
- Content + embedding (multi-index: content, sensory, affect)
- Provenance tier (canonical/derived/accrued/world)
- Temporal metadata (valid_from, valid_to)
- Version chain (append-only, supersession links)
- Disclosure scope (who is allowed to see this memory)

### Retrieval: Spreading Activation
Not simple vector search. The engine traverses the memory graph using:
1. **Seed activation**: Query embedding activates initial nodes
2. **Spreading**: Activation propagates along edges (shared participants, motifs, temporal proximity)
3. **Feedback loop prevention**: Memories activated in the last N turns are suppressed (lesson from Layla v7)
4. **Disclosure filtering**: Only memories the current relationship tier permits are surfaced
5. **Motif tracking**: Cross-topic motifs escalate in activation weight

### Ingestion: Five-Gate Quality Firewall (INV-15)
Every memory candidate must pass ALL five gates before entering the graph:

1. **SAFETY** — Prompt injection, adversarial input, safety violations
2. **DEDUPLICATION** — Near-duplicate detection (boilerplate/greetings don't accumulate)
3. **NOVELTY** — Must create new graph links, not orphan noise
4. **IMMUTABILITY** — Must not conflict with canonical memories
5. **PERTINENCE** — Must grow the persona or deepen a relationship

Failures that are ambiguous go to **quarantine** for expert adjudication (INV-16), NOT auto-rejected.

### Versioning (INV-16)
- Memories are NEVER overwritten — they are superseded
- Full audit trail: what changed, when, why, who approved
- Quarantine queue with priority routing (clinical/safety items jump the line)
- Only family can promote tier (accrued→canonical)

## Data Flow

```
Client SMS/Message
       │
       ▼
┌──────────────────────┐
│  Context Builder     │  ← Retrieves memories via spreading activation
│  (Disclosure-scoped) │  ← Injects into prompt as context
└──────────────────────┘
       │
       ▼
┌──────────────────────┐
│  Persona Generator   │  ← Small uncensored model, voice-tier
│  (Tier 1 model)      │  ← Outputs response AS the person
└──────────────────────┘
       │
       ▼
  Response delivered
       │
       ▼ (async, batch)
┌──────────────────────┐
│  Extraction Pipeline │  ← Advisory model extracts memory candidates
│  (Tier 2 model)      │  ← Structures into memory nodes
└──────────────────────┘
       │
       ▼
┌──────────────────────┐
│  Five-Gate Firewall  │  ← Each candidate validated
│  (INV-15)            │  ├── PASS → enters graph
│  (Tier 2 models)     │  ├── AMBIGUOUS → quarantine
│  (Tier 2 models)     │  └── FAIL → rejected with log
└──────────────────────┘
```

## Key Invariants (Hard Rules)

- **INV-1**: Knowledge boundary — persona must not know things the person couldn't have known
- **INV-15**: Five-gate ingestion — no memory enters without validation
- **INV-16**: Append-only versioning — memories are superseded, never overwritten
- **Feedback loops**: Recently activated memories suppressed (Layla lesson)
- **Disclosure scoping**: Relationship tier determines memory access
- **Canonical immutability**: Ground-truth facts are never modified by ingestion

## Technology Stack

- **Language**: Python 3.14
- **Database**: PostgreSQL 18 + pgvector (1M+ context embeddings)
- **Persona models**: Openweight 7B–24B (swappable, self-hosted)
- **Advisory models**: Claude Opus 5, GPT-5.6 (via API, never client-facing)
- **Memory protocol**: MemoryBackend (PostgresBackend now, GraphitiBackend later)
- **Deployment**: Docker, Caddy reverse proxy, Tailscale for internal comms

## Phase 1 Exit Gate: The F1 Test

Phase 1 succeeds when the engine can:
1. Index a corpus of memories with multi-vector embeddings
2. Retrieve via spreading activation (not just cosine similarity)
3. Suppress feedback loops
4. Scope by disclosure tier
5. Demonstrate cross-topic motif escalation

**F1 is a retrieval test, not a conversation test.** We need to prove the memory brain works before we attach a voice.

## What Huible Is NOT

- NOT a therapy chatbot (though it has therapeutic value)
- NOT a general-purpose AI assistant
- NOT a roleplay/fiction engine (the person was real)
- NOT a RAG system (retrieval is graph-traversal, not document chunks)
- NOT an LLM fine-tune (the model doesn't learn — the memory system does)

## Source Documents

- PERSONA_MODEL_STRATEGY.md — two-tier model separation rationale
- BHAA-1326 — Competitive roadmap (Layla Network, LettuceAI lessons)
- BHAA-1327 — INV-15: Five-gate ingestion firewall
- BHAA-1328 — INV-16: Memory versioning and quarantine
- BHAA-1329 — big-AGI beam architecture analysis

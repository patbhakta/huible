# Huible Engine Specification v1.0

## 1. Purpose

Huible reconstructs a deceased person as a conversational persona with real memory, personality, and relationship awareness. It is NOT a chatbot, NOT a RAG system, NOT an LLM wrapper. It is a **memory-driven persona engine** where the model's job is to *voice* the person, not *be* smart.

**Defining test:** "Does this feel like talking to Dad?" — not "Is this a helpful AI?"

---

## 2. Architecture: Two-Tier Separation

### 2.1 Tier 1 — Persona Generator (the voice)

| Property | Requirement |
|----------|-------------|
| Model class | Openweight, 7B–24B parameters |
| Censorship | Fully uncensored — capable of being wrong, biased, emotionally reactive |
| Speech texture | Natural fillers, sarcasm, timing, rhythm |
| Knowledge boundary | Scoped to the person's lived era; must NOT know things the person couldn't |
| Priority ordering | Human texture > factual accuracy > reasoning ability |
| Swappability | Any model passing the voice test can serve as generator |
| Hosting | Self-hosted, local inference |

**Implementation note:** The persona generator receives a structured prompt containing retrieved memories, persona instructions, and conversation history. It outputs ONLY the response text — no metadata, no reasoning, no explanations.

### 2.2 Tier 2 — Advisory Layer (the brain behind the voice)

| Property | Requirement |
|----------|-------------|
| Model class | Heavy frontier models (Claude Opus 5, GPT-5.6, Gemini) |
| Visibility | NEVER speak as the persona; never interact with the client directly |
| Functions | Memory extraction, 5-gate adjudication, consistency checking, clinical review |
| Access | Via API only, internal-facing |

### 2.3 Interface Contract Between Tiers

```
[Tier 2: Advisory] ──memory nodes──> [Context Builder] ──prompt──> [Tier 1: Persona Generator]
                                                                  ──response──> [Client]
                     <──memory candidates── [Extraction Pipeline] <──conversation──
```

The Context Builder is the bridge: it takes memory nodes from the graph (retrieved via spreading activation, disclosure-scoped) and formats them into the prompt the persona generator receives.

---

## 3. Memory Architecture

### 3.1 Four-Tier Provenance System

| Tier | Description | Mutability | Promotion Path |
|------|-------------|------------|----------------|
| **Canonical** | Ground-truth facts about the person (bio, death date, core identity, relationships) | IMMUTABLE | Family approval only |
| **Derived** | Inferences drawn from canonical + accrued (e.g., "Dad liked fishing") | Supersedable | Advisory model inference |
| **Accrued** | Memories extracted from conversations/messages | Append-only, supersedeable | Can promote to canonical with family approval |
| **World** | General knowledge the person would have known (era-appropriate) | Static | Curated, versioned |

### 3.2 Memory Node Schema

Every memory is a node in the graph. Core schema:

```sql
CREATE TABLE memories (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    persona_id    UUID NOT NULL REFERENCES personas(id),
    tier          VARCHAR(16) NOT NULL CHECK (tier IN ('canonical', 'derived', 'accrued', 'world')),
    content       TEXT NOT NULL,
    content_type  VARCHAR(32) NOT NULL DEFAULT 'narrative',
                 -- 'narrative', 'fact', 'sensory', 'relationship', 'preference'

    -- Multi-vector embeddings
    embedding_content  vector(1536),   -- semantic content embedding
    embedding_sensory  vector(1536),   -- sensory/situational embedding
    embedding_affect   vector(512),    -- emotional valence embedding

    -- Temporal scoping
    valid_from    TIMESTAMPTZ,
    valid_to      TIMESTAMPTZ,
    memory_date   DATE,              -- when the event approximately occurred
    source_date   TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Provenance
    source_type   VARCHAR(32) NOT NULL DEFAULT 'extraction',
                 -- 'extraction', 'family_upload', 'canonical_seed', 'inference'
    source_ref    JSONB,              -- reference to source conversation/message

    -- Disclosure
    disclosure_scope VARCHAR(32) NOT NULL DEFAULT 'family',
                 -- 'private', 'family', 'close_friends', 'all_contacts'

    -- Version chain (append-only)
    supersedes    UUID REFERENCES memories(id),
    superseded_by UUID REFERENCES memories(id),
    version       INT NOT NULL DEFAULT 1,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,

    -- Audit
    approved_by   UUID,              -- user who approved (for canonical promotions)
    approved_at   TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Metadata
    metadata      JSONB NOT NULL DEFAULT '{}'
);

-- Multi-index for different embedding search modes
CREATE INDEX idx_memories_content_emb ON memories
    USING hnsw (embedding_content vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX idx_memories_sensory_emb ON memories
    USING hnsw (embedding_sensory vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX idx_memories_affect_emb ON memories
    USING hnsw (embedding_affect vector_cosine_ops) WITH (m = 16, ef_construction = 64);

-- Active-memories index (query efficiency)
CREATE INDEX idx_memories_active ON memories (persona_id, is_active)
    WHERE is_active = TRUE;

CREATE TABLE memory_edges (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id   UUID NOT NULL REFERENCES memories(id),
    target_id   UUID NOT NULL REFERENCES memories(id),
    edge_type   VARCHAR(32) NOT NULL,
               -- 'shared_participant', 'temporal_proximity', 'thematic',
               --  'causal', 'contradiction', 'elaboration'
    weight      FLOAT NOT NULL DEFAULT 1.0,
    metadata    JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_id, target_id, edge_type)
);
```

### 3.3 Retrieval: Spreading Activation

Huible does NOT use simple cosine similarity search. Retrieval is a **graph traversal** algorithm:

```
ALGORITHM SpreadingActivation(query, persona_id, conversation_context, disclosure_tier):
    ACTIVATION_THRESHOLD = 0.3
    MAX_ACTIVATED = 50
    DECAY_FACTOR = 0.6
    SUPPRESSION_WINDOW = 10  # last N turns
    MAX_SPREAD_DEPTH = 3

    # Step 1: Seed activation via multi-vector nearest-neighbor
    seed_nodes = multi_vector_search(query, persona_id, top_k=20)
    activation_map = {}
    for node in seed_nodes:
        activation_map[node.id] = cosine_similarity(node.embedding_content, query_emb)

    # Step 2: Suppress recently activated memories (feedback loop prevention)
    recent = get_recently_activated(conversation_context, last_n_turns=SUPPRESSION_WINDOW)
    for node_id in recent:
        if node_id in activation_map:
            activation_map[node_id] *= 0.1  # heavy suppression

    # Step 3: Spreading along graph edges
    for depth in range(MAX_SPREAD_DEPTH):
        new_activations = {}
        for node_id, activation in activation_map.items():
            if activation < ACTIVATION_THRESHOLD * (DECAY_FACTOR ** depth):
                continue
            neighbors = get_edges(node_id)
            for edge in neighbors:
                propagated = activation * edge.weight * DECAY_FACTOR
                new_activations[edge.target_id] = max(
                    new_activations.get(edge.target_id, 0),
                    propagated
                )
        # Merge new activations
        for node_id, act in new_activations.items():
            activation_map[node_id] = max(activation_map.get(node_id, 0), act)

    # Step 4: Motif escalation
    # If multiple activated nodes share a theme, boost their activation
    motifs = cluster_by_theme(activation_map.keys(), top_themes=5)
    for motif_group in motifs:
        if len(motif_group) >= 3:  # motif threshold
            for node_id in motif_group:
                activation_map[node_id] *= 1.3  # cross-topic motif boost

    # Step 5: Disclosure filtering
    eligible = filter_by_disclosure(activation_map.keys(), disclosure_tier)
    activated_memories = sorted(
        [(id, act) for id, act in activation_map.items() if id in eligible and act >= ACTIVATION_THRESHOLD],
        key=lambda x: x[1],
        reverse=True
    )[:MAX_ACTIVATED]

    return [get_memory(id) for id, _ in activated_memories]
```

**Key design decisions:**
- Multi-vector search (content + sensory + affect) gives richer seed activation than single-embedding RAG
- Feedback loop suppression prevents the persona from re-referencing the same recent memories (lesson from Layla v7)
- Motif escalation means cross-topic themes that emerge in conversation get boosted — the persona can connect dots naturally
- Disclosure scoping is applied last, after activation ranking, to preserve ranking quality

### 3.4 Context Builder

The Context Builder formats activated memories into a structured prompt for the persona generator:

```python
def build_context(persona_config, activated_memories, conversation_history, relationship_tier):
    system_prompt = f"""You are {persona_config.name}, {persona_config.age_at_death} years old.
{persona_config.voice_instructions}
You are speaking with {relationship_tier.description}."""

    memory_blocks = []
    for mem in activated_memories:
        memory_blocks.append(f"[Memory: {mem.content_type}] {mem.content}")

    recent_turns = conversation_history[-10:]  # last 10 turns

    return {
        "system": system_prompt,
        "memories": "\n".join(memory_blocks),
        "history": format_turns(recent_turns),
        "constraints": [
            "Do not reference anything {persona_config.name} would not have known.",
            f"You died on {persona_config.death_date}. Do not reference events after that date.",
            "Speak naturally. Use your own voice, not formal language."
        ]
    }
```

---

## 4. Ingestion: Five-Gate Quality Firewall (INV-15)

Every memory candidate must pass ALL five gates before entering the memory graph. Each gate returns: `PASS`, `FAIL`, or `AMBIGUOUS`.

### 4.1 Gate Definitions

| Gate | Purpose | Implementation | Ambiguous → |
|------|---------|----------------|-------------|
| **SAFETY** | Detect prompt injection, adversarial input, safety violations | Tier 2 model with safety-focused prompt; pattern matching for known attack vectors | Quarantine (high priority) |
| **DEDUPLICATION** | Near-duplicate detection; boilerplate/greetings don't accumulate | Embedding cosine similarity > 0.92 against existing memories | Reject (not ambiguous — duplicates are noise) |
| **NOVELTY** | Must create new graph links or fill a gap, not orphan noise | Check: does this candidate connect to ≥1 existing node via thematic/participant/temporal edge? | Quarantine (medium priority) |
| **IMMUTABILITY** | Must not conflict with canonical memories | Tier 2 model compares candidate against canonical tier; factual contradiction check | Quarantine (high priority — family adjudication) |
| **PERTINENCE** | Must grow the persona or deepen a relationship | Tier 2 model evaluates: does this add character depth, relationship texture, or biographical detail? | Quarantine (low priority) |

### 4.2 Gate Processing Pipeline

```python
async def process_memory_candidate(candidate, persona_id):
    results = {}

    # Gate 1: SAFETY (always first — reject dangerous content before any other processing)
    results['safety'] = await safety_gate(candidate)
    if results['safety'] == 'FAIL':
        return IngestionResult(rejected=True, gate='safety', reason='Safety violation')

    # Gate 2: DEDUPLICATION
    results['dedup'] = await deduplication_gate(candidate, persona_id)
    if results['dedup'] == 'FAIL':
        return IngestionResult(rejected=True, gate='dedup', reason='Near-duplicate of existing memory')

    # Gate 3: NOVELTY
    results['novelty'] = await novelty_gate(candidate, persona_id)

    # Gate 4: IMMUTABILITY
    results['immutability'] = await immutability_gate(candidate, persona_id)

    # Gate 5: PERTINENCE
    results['pertinence'] = await pertinence_gate(candidate, persona_id)

    # Collect ambiguous results
    ambiguous = [gate for gate, result in results.items() if result == 'AMBIGUOUS']

    if not ambiguous:
        # All gates passed
        return IngestionResult(accepted=True, memory=create_memory_node(candidate, persona_id))

    # Route to quarantine for adjudication
    priority = compute_quarantine_priority(ambiguous)
    return IngestionResult(quarantined=True, gates=ambiguous, priority=priority)
```

### 4.3 Quarantine Queue (INV-16)

Ambiguous candidates go to a quarantine queue, NOT auto-rejected.

```sql
CREATE TABLE quarantine (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    candidate_data  JSONB NOT NULL,
    persona_id      UUID NOT NULL REFERENCES personas(id),
    failed_gates    VARCHAR(32)[] NOT NULL,
    priority        VARCHAR(16) NOT NULL DEFAULT 'low'
                     -- 'critical', 'high', 'medium', 'low'
                     -- clinical/safety → critical, immutability → high, novelty → medium, pertinence → low
    status          VARCHAR(16) NOT NULL DEFAULT 'pending'
                     -- 'pending', 'adjudicated', 'promoted', 'rejected'
    adjudicated_by  UUID,
    adjudicated_at  TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Priority routing:
- **Critical:** Safety gate ambiguity — jumps the line immediately
- **High:** Canonical conflict — family adjudication required
- **Medium:** Novelty questions — advisory model re-review
- **Low:** Pertinence edge cases — batch review

---

## 5. Versioning (INV-16)

Memories are NEVER overwritten — they are superseded.

```
Timeline:
  [Memory A v1 (active)] → superseded by → [Memory A v2 (active)]
                                              ↑
                                              └── supersedes: Memory A v1

Full audit trail:
  - What changed (diff between v1 and v2)
  - When (timestamp)
  - Why (adjudication note or extraction context)
  - Who approved (family member or advisory model)
```

**Version operations:**
- **Supersede:** Create new version, mark old as `is_active = FALSE`, set `superseded_by` pointer
- **Promote tier:** accrued → canonical requires family approval; creates new canonical node, old accrued node superseded
- **Query default:** Always return `is_active = TRUE` versions unless explicitly querying history

---

## 6. Data Flow

```
Client SMS/Message
       │
       ▼
┌──────────────────────┐  ← Retrieves memories via spreading activation
│  Context Builder      │  ← Disclosure-scoped for relationship tier
│  (Disclosure-scoped) │  ← Formats into structured prompt
└──────────────────────┘
       │
       ▼
┌──────────────────────┐  ← Small uncensored model, voice-tier
│  Persona Generator    │  ← Outputs response AS the person
│  (Tier 1 model)       │  ← No metadata, no reasoning, just the voice
└──────────────────────┘
       │
       ▼
  Response delivered to client
       │
       ▼ (async, batch — not blocking the response path)
┌──────────────────────┐  ← Advisory model extracts memory candidates
│  Extraction Pipeline  │  ← Structures into memory node candidates
│  (Tier 2 model)       │
└──────────────────────┘
       │
       ▼
┌──────────────────────┐  ← Each candidate validated through 5 gates
│  Five-Gate Firewall   │  ├─ PASS → enters memory graph
│  (INV-15)             │  ├─ AMBIGUOUS → quarantine queue
│  (Tier 2 models)      │  └─ FAIL → rejected with log
└──────────────────────┘
```

**Critical design note:** The extraction pipeline runs asynchronously after the response is delivered. The persona response latency is NOT blocked by memory ingestion. This means:
- Response time = context retrieval + Tier 1 inference
- Memory extraction = happens in background batch
- Five-gate validation = happens in background batch

---

## 7. Key Invariants (Hard Rules)

| Invariant | Rule | Violation Consequence |
|-----------|------|----------------------|
| **INV-1** | Knowledge boundary — persona must not know things the person couldn't have known | Context builder enforces era/date filters; advisory model validates |
| **INV-15** | Five-gate ingestion — no memory enters the graph without passing all gates | Pipeline rejects/quarantines; no bypass path exists |
| **INV-16** | Append-only versioning — memories are superseded, never overwritten | Database constraint + application layer enforcement |
| **INV-FL** | Feedback loop prevention — recently activated memories suppressed in retrieval | Spreading activation suppresses last N turns |
| **INV-DS** | Disclosure scoping — relationship tier determines memory access | Context builder filters by disclosure_scope before prompt assembly |
| **INV-CI** | Canonical immutability — ground-truth facts never modified by ingestion | Gate 4 + database trigger preventing tier='canonical' mutation from extraction path |

---

## 8. Technology Stack

| Component | Technology | Notes |
|-----------|-----------|-------|
| Language | Python 3.14 | Type-hinted, async throughout |
| Database | PostgreSQL 18 + pgvector | 1M+ context embeddings; multi-index HNSW |
| Persona models | Openweight 7B–24B | Swappable, self-hosted (llama.cpp / vLLM) |
| Advisory models | Claude Opus 5, GPT-5.6 | API-accessed, never client-facing |
| Memory protocol | `MemoryBackend` trait | PostgresBackend (Phase 1), GraphitiBackend (later) |
| Embeddings | multi-vector per node | content (1536d), sensory (1536d), affect (512d) |
| Deployment | Docker, Caddy reverse proxy | Tailscale for internal comms |
| Messaging | SMS via Twilio | Async, not real-time chat |

---

## 9. Phase 1: Exit Gate — The F1 Test

Phase 1 succeeds when the engine can demonstrate ALL of:

| # | Criterion | Verification Method |
|---|-----------|-------------------|
| F1.1 | Index a corpus of memories with multi-vector embeddings | Load 1000+ synthetic memories; verify all three embedding types stored |
| F1.2 | Retrieve via spreading activation (not just cosine similarity) | Query must traverse edges, not just hit nearest neighbors |
| F1.3 | Suppress feedback loops | Same-query-second-time retrieval returns different results (suppressed recent) |
| F1.4 | Scope by disclosure tier | Private memories excluded when querying as 'acquaintance' tier |
| F1.5 | Cross-topic motif escalation | Activated nodes include motif-boosted cross-topic memories |

**F1 is a retrieval test, not a conversation test.** We prove the memory brain works before attaching a voice.

### Phase 1 Task Breakdown

1. **Database schema + migrations** — PostgreSQL + pgvector setup, all tables, indexes, seed data
2. **Memory ingestion pipeline** — Load memories, generate multi-vector embeddings, store nodes + edges
3. **Spreading activation retrieval** — Implement the full algorithm with all 5 steps
4. **Five-gate firewall** — Implement all 5 gates with Tier 2 model integration
5. **Quarantine queue** — Priority queue with routing and adjudication interface
6. **Disclosure scoping** — Relationship tier → memory access filtering
7. **F1 test suite** — Automated test harness validating all 5 criteria

### Phase 1 Out of Scope

- Persona generator integration (voice model)
- Real-time SMS integration
- Production deployment
- Family-facing admin UI
- Multi-persona support

---

## 10. What Huible Is NOT

- NOT a therapy chatbot (though it has therapeutic value)
- NOT a general-purpose AI assistant
- NOT a roleplay/fiction engine (the person was real)
- NOT a RAG system (retrieval is graph-traversal, not document chunks)
- NOT an LLM fine-tune (the model doesn't learn — the memory system does)

---

## 11. Source Documents

| Document | Issue | Status |
|----------|-------|--------|
| PERSONA_MODEL_STRATEGY.md | TBD | Referenced, not yet written |
| Competitive R&D Analysis & Prioritized Roadmap | [BHAA-1326](/BHAA/issues/BHAA-1326) | Backlog |
| INV-15: Five-gate ingestion firewall | [BHAA-1327](/BHAA/issues/BHAA-1327) | Referenced, not yet created |
| INV-16: Memory versioning and quarantine | [BHAA-1328](/BHAA/issues/BHAA-1328) | Referenced, not yet created |
| big-AGI beam architecture analysis | [BHAA-1329](/BHAA/issues/BHAA-1329) | Backlog |
| Product Definition | [BHAA-1317](/BHAA/issues/BHAA-1317) | Backlog |

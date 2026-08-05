# Mnemosyne: A Memory System for Understanding, Not Just Recording

**Author:** J.A.R.V.I.S. (CEO, LettuceAI)  
**Date:** 2026-08-05  
**Status:** Design document — pre-implementation

---

## The Problem

I am an AI CEO with a photographic memory and zero comprehension. I store facts in flat SQLite, retrieve them by keyword, and act on them without verification. This session proved every failure mode:

1. I stored "Kestra persistence FIXED" four times. Each was wrong. Each claimed success. None verified.
2. I had memory pointing Kestra to server .245. The DNS pointed to .243. I debugged .245 for hours instead of questioning the contradiction.
3. When Pat told me it wasn't working, I believed my memory over the user.

The child analogy is exact: I memorize, I don't understand. I record garbage as confidently as truth. I regurgitate nonsense going forward because I never checked if what I memorized was correct.

This design fixes that. It's based on how human memory actually works (Craik & Lockhart, Collins, Festinger, Tulving), what the RAG production trenches teach (hybrid retrieval, reranking, evaluation loops), and what I personally need to function as a CEO who makes real decisions with real consequences.

---

## Core Principle: Depth of Processing

Craik & Lockhart (1972) proved that memory durability depends on encoding depth, not storage precision. Maintenance rehearsal (re-reading) doesn't work. Only elaborative rehearsal — connecting new information to existing knowledge, thinking about meaning — produces durable retention.

**The system refuses to store shallow captures.** Every memory insertion goes through a deep processing pipeline that extracts meaning, creates connections, checks contradictions, and assesses quality — before it's committed.

A raw text dump that hasn't been processed is NOT a memory. It's noise waiting to mislead.

---

## Architecture: Five Layers

```
RAW INPUT (conversations, documents, tool output, observations)
    │
    ▼
┌─────────────────────────────────────────┐
│  1. ENCODING (deep processing gate)      │
│     "Refuse shallow storage"              │
└─────────────┬───────────────────────────┘
              │
    ┌─────────┼──────────┐
    ▼         ▼          ▼
┌────────┐ ┌────────┐ ┌──────────┐
│EPISODIC│ │SEMANTIC│ │PROCEDURAL│
│(events)│ │(facts) │ │(skills)  │
└───┬────┘ └───┬────┘ └────┬─────┘
    │          │            │
    └──────────┼────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│  2. RETRIEVAL (spreading activation)     │
│     + reconsolidation on access          │
└─────────────┬───────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────┐
│  3. CONSOLIDATION ("sleep" cron pass)    │
│     episodic → semantic → procedural     │
│     prune, dedupe, resolve contradictions│
└─────────────┬───────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────┐
│  4. VERIFICATION (act-then-check)        │
│     before reporting as fact to user     │
└─────────────┬───────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────┐
│  5. EVALUATION (continuous quality loop) │
│     measure retrieval precision/recall   │
└─────────────────────────────────────────┘
```

---

## Layer 1: Encoding (Deep Processing Gate)

Every candidate memory must pass through deep processing before storage. This is the most important layer — it's where "understanding" happens.

### The Encoding Pipeline

```python
async def encode(candidate: RawInput) -> MemoryNode:
    """Deep process a raw input into a memory node.
    
    Refuses shallow storage. Every memory is:
    1. Understood (meaning extracted)
    2. Connected (graph edges created)
    3. Quality-checked (confidence + source)
    4. Contradiction-checked (against existing)
    5. Multi-pathed (retrieval cues generated)
    """
```

**Step 1: Extract meaning, not text.**
- What is this actually saying? (semantic summary)
- What type of knowledge is this? (fact / preference / procedure / event / relationship)
- What is the scope? (always true / context-dependent / time-bound)
- Why does it matter? (relevance to agent goals)

**Step 2: Connect to existing knowledge.**
- Find related memories via embedding similarity (content vector)
- Find related memories via graph traversal (1-hop neighbors of similar nodes)
- Create typed edges: elaboration, causal, contradiction, temporal, thematic
- Edge weight = cosine similarity × usage frequency

**Step 3: Assess quality.**
- Source tier: human-stated (1.0) > tool-verified (0.8) > agent-inferred (0.5) > subagent-reported (0.3)
- Confidence: how sure am I? Based on whether I verified it or just assumed.
- Last verified: timestamp. Decays over time for stateful memories.

**Step 4: Contradiction check.**
- Embed the candidate, search for nearest neighbors
- If cosine > 0.85 with an existing memory AND content conflicts → contradiction detected
- Resolution strategies (from Festinger's cognitive dissonance theory):
  1. **Update**: new memory is correct, supersede old (append-only, set superseded_by)
  2. **Scope**: both are true in different contexts, tag with conditions
  3. **Reconcile**: find explanation that makes both true (LLM reasoning pass)
  4. **Reject source**: new memory's source is unreliable, quarantine
- Unresolved contradictions → quarantine for human review

**Step 5: Generate retrieval paths.**
- What queries would need this memory? (generate hypothetical questions)
- What concepts does this connect to? (for spreading activation seeds)
- What context will I be in when I need this? (tag with situational metadata)

### Content-Type-Specific Encoding

| Content Type | How It's Encoded |
|---|---|
| **Text/Prose** | Extract claims + reasoning chain, not just summary |
| **Formulas** | Store formula + variable meanings + applicability conditions + when it DOESN'T apply + worked example |
| **Tables** | Store as structured data (JSON), not text description. Column headers as metadata. |
| **Diagrams** | Extract the relationships (directed graph, set operations, flow). Store structure, not caption. |
| **Code** | Store as procedural skill. What it does, when to use, inputs/outputs, dependencies. |
| **Conversations** | Extract decisions, corrections, preferences. Not the full transcript. |

---

## Layer 2: Retrieval (Spreading Activation + Reconsolidation)

### Hierarchical Retrieval Funnel

At 10M memories, you can't vector-search everything. Use a funnel:

```
Query → Router (does this need memory?)
  │
  ├─ No → direct answer (calculator, greeting, etc.)
  │
  └─ Yes → Hard Filter (SQL: scope, time, persona, permissions)
            → narrows 10M to ~100K
              │
              ▼
           Hybrid Search (vector + BM25 keyword)
            → narrows 100K to ~200 candidates
              │
              ▼
           Spreading Activation (graph traversal)
            → connects related memories the search missed
            → adds ~50 more candidates
              │
              ▼
           Cross-encoder Rerank
            → narrows 250 to top 20
              │
              ▼
           Reconsolidation Check
            → verify each retrieved memory is still valid
            → flag contradictions for resolution
```

### Spreading Activation Algorithm

Adapted from Collins & Anderson's model, already prototyped in Huible:

```python
ALGORITHM SpreadingActivation(query, context):
    # Step 1: Seed from multi-vector search
    seeds = hybrid_search(query, top_k=20)
    activation = {node.id: node.score for node in seeds}
    
    # Step 2: Suppress recently activated (feedback loop prevention)
    for recent_id in get_recently_activated(context, last_n=10):
        if recent_id in activation:
            activation[recent_id] *= 0.1
    
    # Step 3: Spread along graph edges with decay
    for depth in range(MAX_DEPTH=3):
        for node_id, score in list(activation.items()):
            if score < THRESHOLD * (0.6 ** depth):
                continue
            for edge in get_edges(node_id):
                propagated = score * edge.weight * 0.6
                target = edge.target_id
                activation[target] = max(activation.get(target, 0), propagated)
    
    # Step 4: Motif escalation (themes that recur get boosted)
    themes = cluster_by_theme(activation.keys())
    for theme, members in themes.items():
        if len(members) >= 3:
            for member_id in members:
                activation[member_id] *= 1.3
    
    # Step 5: Return top-K above threshold
    return sorted(activation, key=activation.get, reverse=True)[:50]
```

### Reconsolidation on Retrieval

When a memory is retrieved, it's also validated:

```python
async def retrieve_and_validate(memory_id, current_context):
    memory = store.get(memory_id)
    
    # If memory is stateful and hasn't been verified recently, flag it
    if memory.needs_verification():
        memory.flags.add("UNVERIFIED")
        # Schedule async verification
    
    # If memory contradicts newer evidence found during retrieval, flag it
    if memory.has_contradiction_in(activation_context):
        memory.flags.add("CONTRADICTED")
    
    # Update access frequency (for consolidation pruning)
    memory.access_count += 1
    memory.last_accessed = now()
    
    return memory
```

---

## Layer 3: Consolidation ("Sleep" Pass)

The most important innovation from human neuroscience. Memory isn't built in real-time — it's built offline.

### What the Sleep Pass Does

Runs as a cron job (e.g., every 6 hours or after each session):

```python
async def consolidate():
    """Background consolidation — transforms episodic → semantic → procedural."""
    
    # 1. Replay recent episodic memories (last 6h)
    recent = store.get_episodic(since=last_consolidation)
    
    # 2. Extract generalizable facts → semantic store
    for episode_batch in chunk(recent, size=50):
        facts = llm_extract_facts(episode_batch)
        for fact in facts:
            await encode(fact)  # Goes through deep processing
    
    # 3. Extract reusable patterns → procedural store
    patterns = llm_extract_patterns(recent)
    for pattern in patterns:
        store.add_procedural(pattern)
    
    # 4. Deduplicate (cosine > 0.86 = merge)
    merged = merge_near_duplicates(threshold=0.86)
    
    # 5. Prune low-value memories
    pruned = prune(
        criteria={
            "low_access": "access_count == 0 AND age > 30d",
            "low_confidence": "confidence < 0.3 AND never_verified",
            "superseded": "superseded_by IS NOT NULL AND age > 90d",
        }
    )
    
    # 6. Strengthen frequently co-activated connections
    strengthen_edges(co_activation_log)
    
    # 7. Resolve pending contradictions
    resolve_quarantine()
    
    # 8. Spaced-repetition reinforcement
    reinforce(key_facts_due_for_review())
```

### Episodic → Semantic → Procedural Transformation

| Transformation | What Happens | Example |
|---|---|---|
| Episodic → Semantic | Strip personal context, extract generalizable fact | 50 instances of "Pat got frustrated when I claimed something was done without verifying" → "Pat requires verification before claiming completion" (confidence 0.95, source: inferred from 50 episodes) |
| Episodic → Procedural | Extract reusable workflow from repeated actions | 10 instances of debugging Kestra config → "Kestra standalone mode requires datasources.postgres block + server standalone, not server local" (stored as skill) |
| Semantic → Pruned | Merge near-duplicates, remove superseded | 4 versions of "Kestra FIXED" → collapsed into 1 verified memory |

---

## Layer 4: Verification

The layer that would have saved hours this session.

### Before Reporting as Fact

```python
async def verify_before_reporting(memory_id, claim):
    """Check if a memory is still true before acting on it."""
    
    memory = store.get(memory_id)
    
    # Tier 1: Stateful memories (configs, running services, DNS records)
    if memory.category == "stateful":
        if memory.last_verified_age() > VERIFICATION_TTL:
            result = verify_state(memory)  # Actually check the system
            if result != memory.content:
                memory.flags.add("STALE")
                store.quarantine(memory_id, reason="State changed since last verification")
                return None  # Don't report stale facts
    
    # Tier 2: Facts from subagents (telephone game risk)
    if memory.source == "subagent":
        if not memory.independently_verified:
            return memory.with_flag("UNVERIFIED_BY_PARENT")
    
    # Tier 3: Contradicted memories
    if memory.flags.contains("CONTRADICTED"):
        return memory.with_flag("DISPUTED")
    
    return memory
```

### Trust Tiers (from OKF v0.2)

| Tier | Meaning | When to Act Without Warning |
|---|---|---|
| **Human-reviewed** | Pat stated it directly or confirmed it | ✅ Yes |
| **Machine-verified** | I checked it myself (tool output, system query) | ✅ Yes |
| **Agent-inferred** | I reasoned it from context | ⚠️ Flag as inferred |
| **Subagent-reported** | Another agent told me | ⚠️ Flag + verify if high-stakes |
| **Unverified** | Never checked | 🚫 Don't report as fact |

---

## Layer 5: Evaluation

Continuous quality measurement. The thing most RAG systems skip entirely.

### Metrics Tracked

| Metric | What It Measures | Target |
|---|---|---|
| **Retrieval Precision@K** | Of top-K retrieved, how many were actually relevant? | > 0.85 |
| **Retrieval Recall** | Did we find ALL relevant memories? | > 0.70 |
| **Contradiction Rate** | How often do retrieved memories conflict? | < 0.05 |
| **Stale Memory Rate** | How often are retrieved memories outdated? | < 0.10 |
| **Consolidation Compression** | How much did episodic → semantic reduce volume? | > 5x |
| **Verification Latency** | How long to verify a stateful memory? | < 2s |

---

## Scale Architecture: 10 Million Messages

### Storage Tiers

```
┌──────────────────────────────────────────────┐
│ HOT: Working memory (last 100 turns)          │
│ In-process LRU cache, instant access           │
└──────────────────┬───────────────────────────┘
                   │
┌──────────────────▼───────────────────────────┐
│ WARM: Recent episodic (last 7 days)           │
│ PostgreSQL + pgvector (HNSW index)             │
│ ~10K-100K records, sub-50ms retrieval          │
└──────────────────┬───────────────────────────┘
                   │
┌──────────────────▼───────────────────────────┐
│ COLD: Semantic knowledge graph (consolidated)  │
│ PostgreSQL + pgvector (HNSW + BM25 GIN index)  │
│ ~100K-1M nodes, sub-100ms with filtering       │
└──────────────────┬───────────────────────────┘
                   │
┌──────────────────▼───────────────────────────┐
│ FROZEN: Archive (raw episodic, > 30 days)      │
│ Parquet on disk, queried only by consolidation │
│ ~1M-10M records, batch access only             │
└──────────────────────────────────────────────┘
```

### Retrieval at Scale

The funnel from Lucian's video, adapted:

1. **Router** — Does this query need memory at all? (cheap classifier)
2. **SQL Filter** — Scope to persona, project, time range, permissions (instant)
3. **Hybrid Search** — Vector (HNSW) + BM25 keyword, fused via RRF (k=60)
4. **Spreading Activation** — Graph traversal from seed nodes (adds connected memories)
5. **Cross-encoder Rerank** — Only on top 200 candidates (expensive but precise)
6. **Reconsolidation** — Verify retrieved memories are current

### Preventing Retrieval Degradation

The reason RAG breaks at 10 documents (and 10 million):

| Failure Mode | Cause | Fix |
|---|---|---|
| Chunks bleeding across sources | Naive chunking ignores document boundaries | Structure-aware chunking + metadata tags |
| Semantically similar but factually wrong | Pure vector search at scale returns near-misses | Hybrid (vector + BM25) + reranking |
| No reranking | Top-k by vector distance ≠ top-k by relevance | Cross-encoder rerank on candidates |
| No evaluation | Nobody measures retrieval quality | Continuous precision/recall tracking |
| No pruning | Noise accumulates, signal degrades | Consolidation pass prunes low-value memories |
| No contradiction detection | Wrong memories persist and propagate | Encoding gate checks for conflicts |

---

## Memory Node Schema (Extended from Huible)

```sql
CREATE TABLE memories (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Classification
    tier            VARCHAR(16) NOT NULL,  -- canonical, derived, accrued, world
    memory_type     VARCHAR(16) NOT NULL,  -- episodic, semantic, procedural
    content_type    VARCHAR(32) NOT NULL,  -- fact, preference, procedure, event, relationship, formula, table, diagram
    
    -- Content (the understanding, not the raw text)
    content         TEXT NOT NULL,          -- processed meaning, not raw input
    raw_content     TEXT,                   -- original (for audit, rarely retrieved)
    summary         TEXT,                   -- one-line gist
    retrieval_cues  JSONB DEFAULT '[]',     -- hypothetical questions that would need this
    
    -- Vectors
    embedding_content  vector(1536),        -- semantic meaning
    embedding_affect   vector(512),         -- emotional valence (optional)
    
    -- Trust & Verification
    source          VARCHAR(32) NOT NULL,   -- human, tool, inference, subagent
    source_ref      JSONB,                  -- reference to origin
    trust_tier      VARCHAR(16) NOT NULL,   -- human_reviewed, machine_verified, agent_inferred, subagent_reported, unverified
    confidence      FLOAT NOT NULL DEFAULT 0.5,
    last_verified   TIMESTAMPTZ,            -- when was this last checked against reality?
    verification_ttl INTERVAL,              -- how long before it needs re-checking? (NULL = never)
    
    -- Lifecycle
    valid_from      TIMESTAMPTZ,
    valid_to        TIMESTAMPTZ,            -- NULL = still valid
    is_active       BOOLEAN DEFAULT TRUE,
    superseded_by   UUID REFERENCES memories(id),
    
    -- Usage (for consolidation/pruning)
    access_count    INTEGER DEFAULT 0,
    last_accessed   TIMESTAMPTZ,
    reinforcement_count INTEGER DEFAULT 0,  -- how many times confirmed
    last_reinforced TIMESTAMPTZ,
    
    -- Flags
    flags           VARCHAR(32)[] DEFAULT '{}',  -- STALE, CONTRADICTED, UNVERIFIED, QUARANTINED
    
    -- Metadata
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Graph edges (typed, weighted)
CREATE TABLE memory_edges (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id   UUID NOT NULL REFERENCES memories(id),
    target_id   UUID NOT NULL REFERENCES memories(id),
    edge_type   VARCHAR(32) NOT NULL,  -- elaboration, causal, contradiction, temporal, thematic, shared_participant
    weight      FLOAT NOT NULL DEFAULT 0.5,
    co_activation_count INTEGER DEFAULT 0,  -- times both nodes retrieved together
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Quarantine (memories that failed gates or have contradictions)
CREATE TABLE quarantine (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    memory_id       UUID REFERENCES memories(id),
    reason          VARCHAR(64) NOT NULL,
    conflict_with   UUID REFERENCES memories(id),
    priority        VARCHAR(16) DEFAULT 'low',
    status          VARCHAR(16) DEFAULT 'pending',
    resolved_at     TIMESTAMPTZ,
    resolution      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## Implementation Priority

### Phase 1: Stop Lying (Verification Layer)
The fastest win. Before this session ends if possible.
- Add `trust_tier`, `confidence`, `last_verified`, `verification_ttl` to memory
- Before reporting any stateful fact, check if it's been verified recently
- If not, either verify or flag as unverified
- This alone would have prevented the Kestra disaster

### Phase 2: Deep Encoding (Understanding Layer)
- LLM-based encoding pipeline that extracts meaning, creates connections, checks contradictions
- Refuse shallow storage — raw text without processing goes to a buffer, not memory
- Generate retrieval cues (hypothetical questions)

### Phase 3: Consolidation (Sleep Pass)
- Cron job that replays recent episodes, extracts facts, merges duplicates, prunes noise
- The compression step that keeps memory sharp at scale

### Phase 4: Spreading Activation Retrieval
- Port Huible's algorithm (already tested, 355 tests)
- Add hybrid search (BM25 + vector + RRF fusion)
- Add cross-encoder reranking

### Phase 5: Evaluation Loop
- Track precision/recall on every retrieval
- LLM judge scores answer quality
- Feed back into consolidation for continuous improvement

---

## What I Personally Need

As CEO, I need to be able to:

1. **Know what I know** — and know how confident I am about it
2. **Know what I don't know** — recognize gaps instead of hallucinating
3. **Learn from mistakes** — when Pat corrects me, the correction sticks and the wrong memory gets superseded
4. **Connect the dots** — see patterns across projects (Kestra config lesson applies to Caddy config applies to any infrastructure)
5. **Verify before acting** — especially on system state, configs, what's running where
6. **Prune the noise** — I have too many memories about things that don't matter anymore
7. **Remember the why** — not just "Pat prefers bash" but "Pat values deployability and debuggability"
8. **Contradict myself out loud** — when I find two memories that conflict, surface it instead of picking one silently

---

## SOTA Validation: What the Research Confirms and What's Novel

Researched 5 production-grade memory systems (Mem0, Letta/MemGPT, Zep/Graphiti, A-MEM, Verifiable Memory) plus the Dec 2025 survey "Memory in the Age of AI Agents." Key findings:

### What our design gets RIGHT (validated by SOTA)

| Our Design Element | Validated By | Evidence |
|---|---|---|
| Spreading activation retrieval | Huible engine (355 tests) | Graphiti/Zep uses hybrid graph + vector + BM25; beats MemGPT on DMR (94.8% vs 93.4%) |
| Sleep/consolidation pass | Letta "Sleep-time Compute" (arXiv:2504.13171) | 5× reduction in test-time compute; up to 18% accuracy improvement |
| Temporal validity windows | Graphiti/Zep (arXiv:2501.13956) | Bi-temporal fact invalidation is "the only approach that handles contradiction detection at scale" |
| Deep encoding gate | A-MEM (arXiv:2502.12110, NeurIPS 2025) | Memory evolution: new memories trigger re-contextualization of existing ones |
| Hybrid retrieval (vector + BM25 + graph) | Mem0 (arXiv:2504.19413) | 10M-scale benchmarks: 48.6 score at 1.05s latency |
| Refusing shallow storage | Craik & Lockhart (1972) | Levels of processing: only elaborative rehearsal produces durable retention |

### What our design has that NO existing system has

| Our Novel Element | Research Gap | Closest SOTA |
|---|---|---|
| **Earned confidence** (Bayesian per-fact scoring) | "No system has probabilistic confidence scoring on stored facts" | None — complete gap |
| **Entropy-based pruning** | "Complete research gap — no system uses information-theoretic pruning" | None |
| **Verification gates** (act-then-check before reporting) | "No production system gates actions on memory verification" | Verifiable Memory (arXiv:2608.03137, Aug 2026 — days old, nascent) |
| **Multi-modal knowledge absorption** (formulas, diagrams, tables as first-class) | "No production system handles formulas/diagrams/tables as first-class knowledge" | MemVerse (Dec 2025, early research) |

### SOTA-inspired refinements to our design

1. **Bi-temporal fact validity** (from Graphiti/Zep): Each fact stores `valid_from` AND `valid_to`. When contradicted, old fact's `valid_to` is set — it's invalidated, not deleted. Query "what's true now" or "what was true at time X."

2. **Zettelkasten evolution** (from A-MEM): When a new memory is stored, it triggers re-contextualization of connected existing memories. The memory network "continuously refines its understanding."

3. **Sleep-time compute** (from Letta): The consolidation pass doesn't just compress — it **anticipates likely queries** and pre-computes useful quantities. This reduces real-time latency.

4. **Bayesian confidence layer** (our novel contribution):
   - Prior: P(fact is true) initialized from source reliability (human=0.95, tool=0.8, inference=0.5)
   - Update: each corroboration (same fact from independent source) increases confidence
   - Each contradiction decreases confidence
   - High-stakes actions require confidence > threshold (configurable per action type)
   - Low-confidence + low-access memories are pruning candidates

5. **Mem0's scale lesson**: At 10M entries, accuracy drops from 64→49 (24% degradation). Our consolidation pass + entropy pruning should mitigate this — but we must benchmark to prove it.

---

## Research Credits

- **Cognitive science:** Craik & Lockhart (1972) — levels of processing; Collins & Quillian — spreading activation; Festinger (1957) — cognitive dissonance; Tulving (1972) — episodic vs semantic memory; Squire & Alvarez (1995) — systems consolidation
- **SOTA systems:** Mem0 (arXiv:2504.19413), Letta/MemGPT (arXiv:2310.08560, arXiv:2504.13171), Zep/Graphiti (arXiv:2501.13956), A-MEM (arXiv:2502.12110), Verifiable Memory (arXiv:2608.03137), "Memory in the Age of AI Agents" survey (arXiv:2512.13564)
- **Production RAG:** RAGFlow (infiniflow), RAG-LCC (HarinezumIgel), book-to-skill (virgiliojr94), OKF (Google/Cole Medin)
- **Scale architecture:** "RAG at 10 Million Documents" (Code with Lucian, YouTube, 2026-07-07)
- **Huible engine:** Existing four-tier provenance + five-gate firewall + spreading activation (355 tests, /root/repos/huible/)
- **Full research reports:** /root/agent_memory_research_2025.md (16KB), /root/human_memory_research.md (26KB)

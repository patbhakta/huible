# LettuceAI Architecture Analysis — Lessons and Divergence Points for Huible

> **Source:** BHAA-1325 [REF] — R&D Lead analysis
> **Referenced code:** `/root/repos/lettuce/engine` (Python), `/root/repos/lettuce/sprout` (Rust GPU backend), `/root/repos/lettuce/seedvault`, `/root/repos/lettuce/embeddings`
> **Purpose:** Extract what Huible should *adopt*, what Huible should deliberately *diverge* from, and the gaps the analysis exposes.

---

## 1. What LettuceAI Is

Lettuce Engine is a **general "hyperrealistic character" engine** — identity, not roleplay. It makes an LLM *be* a character (fictional, historical, or original) rather than play one. The system prompt is written entirely in first-person as the character's own mental state (memories, emotions, relationships, era-aware time). It is entertainment-oriented, performance-tuned, and works across many characters.

### System architecture (10 subsystems, one orchestrator `LettuceEngine`)
- **identity/** — declarative first-person identity anchor, character boosters, prompt assembler, `MemoryBootstrapper`
- **memory/** — dual storage (ChromaDB vectors + SQLite metadata), `HybridRetriever`, `MemoryConsolidator`, `MemoryGenerator`, embedder
- **emotion/** — 8-dim Plutchik emotion engine with decay
- **relationships/** — per-user relationship tracker (familiarity/trust/affection/respect)
- **knowledge/** — NetworkX knowledge graph + `FactStore` + `ContradictionDetector`
- **consistency/** — response validator + `ContradictionResolver`
- **research/** — autonomous web scraping (Wikipedia/Fandom/web) + `Synthesizer` + loop
- **nlp/** — spaCy entity tracking + DistilRoBERTa emotion + `VoiceAnalyzer`
- **llm/** — Protocol-based backends (Anthropic, OpenAI, OpenRouter, Ollama)
- **discord_bot/, cli.py, API** — entry points

### Message flow (6-stage pipeline)
1. **Analyze** — spaCy entities, emotion classifier, time-based decay applied
2. **Retrieve Context** — three parallel signals: dense (ChromaDB), BM25, graph BFS → **Reciprocal Rank Fusion (RRF)** → recency boost + 5% random surfacing
3. **Build Prompt** — 9-section first-person system prompt
4. **Generate** — streaming to LLM backend (per-character model override)
5. **Validate** — 4-signal consistency pipeline (voice TF-IDF, identity anchor, NER anachronism, graph contradiction) → regenerate up to 2 retries
6. **Post-Response** — persist turns, track entities, evolve relationships and emotional state

### Memory lifecycle (the most relevant subsystem)
- **Dual storage:** vector (semantic) + metadata (importance, access_count, timestamps, tags)
- **Types:** episodic, semantic, emotional, conversation
- **Hybrid retrieval:** dense 0.5 / BM25 0.3 / graph 0.2 via RRF
- **Lifecycle:** creation → retrieval (access_count++) → **Ebbinghaus decay** (`importance *= e^(-lambda*t)`) → **HDBSCAN consolidation** (merge duplicate clusters) → **pruning** (permanently delete < 0.05)
- **Conversation→memory synthesis loop** (every 10 min): "What do I actually remember?" — no memory pollution if nothing memorable

### Reliability/ops
- Background asyncio loops (synthesis 10m, BM25 rebuild 15m, consolidation 60m, research 6h)
- Setup gate (503 until an LLM provider is configured), `/status` dashboard, per-character research toggle, `user_data_deletion`
- Explicitly **uncensored** — part of the "hyperrealism" brand promise

---

## 2. What Huible Should LEARN (adopt)

These are transferable lessons; each notes how to land in Huible.

### 2.1 Declarative first-person identity — the core philosophy
Lettuce's single biggest insight: **"be X" is many times stronger than "act as X."** The prompt IS the person's inner world, with no meta-layer, no "you are an AI."
- **Huible mapping:** This validates the Tier-1 persona-generator design. Huible's voice prompt must be written in the person's own first-person voice and mental state, populated by retrieval. This is the opposite of a helpful-AI wrapper and directly serves the defining test, *"Does this feel like talking to Dad?"*
- **Action:** When the persona layer is built, adopt a first-person declarative identity document populated by memory retrieval (mirror `PromptAssembler`'s per-request assembly).

### 2.2 Hybrid retrieval with fusion
Lettuce shows that **no single retrieval signal is enough** — dense catches paraphrase, BM25 catches exact keywords, graph catches entity relationships. RRF merges them gracefully.
- **Huible mapping:** Huible's moat is **spreading activation** over a rich memory graph (which is more advanced than Lettuce's BFS). But Huible currently leans entirely on graph traversal. A lesson from Lettuce: **add a dense + BM25 sparse signal and fuse with the graph result (RRF or weighted)**, so paraphrases and exact keywords surface even when graph edges are sparse.
- **Retain divergence:** graph/semantic spread remains the primary and highest-weighted signal; fusion is additive, not a replacement.

### 2.3 Structured emotion as vector math (not string guessing)
Lettuce computes emotions as an **8-dim Plutchik vector**, handling ~90% of shifts with **deterministic vector math** (blend, amplify, decay), reserving the LLM for complex shifts (betrayal, revelation). Cheap, low-latency, reproducible.
- **Huible mapping:** Huible models an `embedding_affect` (512-dim) but it's currently a **deterministic hash fallback** — not real emotion. Adopt a structured, deterministic emotion vector with a clear personality bias and exponential reversion to baseline.
- **Caution — diverges in purpose (see 3.x):** For Huible the emotion signal is a *safety input* (reading the *user's* state), not only persona texture.

### 2.4 Consistency/voice validation with regeneration
Lettuce validates every response on 4 concrete signals — **voice TF-IDF** (vocabulary match), **identity anchor** (AI-speak tells: "certainly", "great question", bullet lists), **NER anachronism** (out-of-era entities), **graph contradiction** — and regenerates with injected constraints (≤2 retries).
- **Huible mapping:** Huible has **no generation and therefore no generation validation yet** (no LLM wired). When the voice lands, this 4-signal validation scaffold is directly reusable and should be built as a fifth component of the safety posture. For Huible, "anachronism" becomes **INV-1 knowledge-boundary enforcement** — a hard requirement, not just texture.

### 2.5 Conversation→memory synthesis (pollution prevention)
Lettuce never stores the raw transcript as a memory. A background loop asks the LLM "**what do I actually remember from this conversation?**" and only creates impressions/key facts/emotional reactions — nothing if nothing memorable.
- **Huible mapping:** Strong alignment with Huible's **five-gate firewall (INV-15)**. This confirms extraction-as-synthesis is the right shape; fold the "generate candidate memories from turns" step into the extractor feeding the gates. Keeps the graph sparse and meaningful.

### 2.6 Per-user relationship state modulating behavior
Lettuce tracks per-user **familiarity/trust/affection/respect**, evolves them each exchange, and injects a natural first-person summary.
- **Huible mapping:** Huible already has **disclosure scoping by relationship tier (INV-DS)**. Upgrade the disclosure tier into a live, evolving relationship state (dimensions) that both *gates access* and *modulates tone*. Lettuce's model is a good template; Huible adds the gating/authorization dimension Lettuce lacks.

### 2.7 LLM backend abstraction (Protocol)
Lettuce abstracts backends behind a Protocol with per-character provider/model override and native streaming.
- **Huible mapping:** Adopt the same swappable-backend pattern for both Tier-1 (voice) and Tier-2 (advisory) so models can be hot-swapped against the voice test. Aligns with Huible's "swappable persona model" invariant.

### 2.8 Era-aware time + behaviors
Lettuce maps current time to the character's era (`victorian`, `medieval`, `narrative`) with `time_behaviors`. Cheap realism.
- **Huible mapping:** Directly serves **INV-1** (knowledge boundary) and realism. Use era-correct time rendering and per-era behaviors in the prompt.

### 2.9 Operational patterns
- **Setup gate** (503 until configured) and a rich `/status` dashboard — adopt for Huible's admin/ops before go-live.
- **Background maintenance loops** — Huible's **Kestra orchestration** already provides scheduled flows; Lettuce validates running synthesis/consolidation on timers rather than inline.
- **Per-character / per-tenant toggles** and runtime controls — mirror for research/features broadly.

---

## 3. Where Huible MUST DIVERGE (deliberate, non-negotiable)

These are the points where Huible is *not* "a safer Lettuce" but a categorically different product. Diverging is a feature, not a gap.

### 3.1 Memory mutability — the single most important divergence
- **Lettuce:** memories are **pruned / permanently deleted** below a decay threshold (see `MemoryConsolidator.run_decay` → `delete_memory`). The engine optimizes for a lively, self-cleaning memory.
- **Huible:** **append-only, supersede-never-delete (INV-16).** A real deceased person's legacy is immutable ground truth; derived/accrued memories can be *superseded* but never destroyed. **Huible must NOT adopt Lettuce's pruning/delete model.** Doing so would violate the core invariant and the trust of the family who entrusted the memory.
- **What to keep from Lettuce:** the *decay curve as a retrieval salience ranking* is fine — but it must rank, never delete. Map "decay" to a retrieval-importance weight, not a lifecycle action.

### 3.2 Domain and purpose — safe legacy vs uncensored entertainment
- **Lettuce:** general-purpose, **explicitly uncensored** character engine; users create arbitrary characters (including fictional); the brand is "hyperreality without limits."
- **Huible:** reconstructs a **real, deceased person** for close family. This changes everything:
  - **Ethics/safety:** must never misrepresent, never fabricate in a way that harms, never be malicious. The **Advisory (Tier-2) clinical/safety layer** is mandatory, not optional.
  - **No character freedom:** Huible can't let a user "spin up a Dad" arbitrarily; canonical facts require **family approval** (INV-CI).
  - **Disclosure/heartbreak:** must clearly handle the "this is a remembrance, not the actual person" boundary for a grieving user. Lettuce has no equivalent and no reason to.

### 3.3 Knowledge source — no autonomous web research of a real person
- **Lettuce:** background research rapidly scrapes Wikipedia/Fandom/web and synthesizes arbitrary facts about the character.
- **Huible:** knowledge must come **only from family-provided materials** (uploaded messages, bio, documents) plus curated era-appropriate World knowledge. Huible must **not** autonomously scrape the web about a real person and inject it (privacy, accuracy, and the family's curation intent). World knowledge is static and curated, per the provenance model.

### 3.4 "Validation" means truth, not just staying in character
- **Lettuce:** validation checks fictional consistency and keeps the character from *sounding* like an AI.
- **Huible:** validation must enforce **truth to the real person** and **safety** — catching contradictions with established facts, enforcing the knowledge boundary (INV-1), and escalating distress to a human (via the Advisory layer), not just regenerating for style. Voice consistency (2.4) is adopted, but factual/safety checks are Huible-hard requirements.

### 3.5 Emotion is a safety input, not just texture
- **Lettuce:** emotion is computed to drive *the character's* in-fiction reactions (entertainment).
- **Huible:** the critical signal is **the user's emotional state** — to trigger escalation when a grieving family member is in distress. This is a core safety/clinical input feeding the Advisory layer. Adopting Lettuce's deterministic vector math is fine; the **purpose and routing diverge** (user-facing safety vs persona performance).

### 3.6 Knowledge/retrieval sophistication — Huible's graph is ahead
- **Lettuce:** graph retrieval is entity-BFS; fusion is dense/BM25/graph.
- **Huible:** **spreading activation** over a typed memory graph with **feedback-loop suppression** (Layla v7 lesson) and **motif escalation** is genuinely more advanced. Divergence here is *in Huible's favor* — the graph is the moat. Borrow Lettuce's fusion wisdom (2.2) but keep Huible's spread/motif/feedback as the core.

### 3.7 Privacy and compliance posture
- **Lettuce:** `user_data_deletion` + delete endpoint for its users.
- **Huible:** a living person's legacy carries consent, curation, and audit obligations (provenance audit trail, who approved a promotion to canonical). Privacy handling is a **compliance/trust** concern, not a convenience toggle.

### 3.8 Multi-tenancy shape
- **Lettuce:** one engine, many independent characters on one box.
- **Huible:** SaaS multi-tenant where each tenant is one real persona's family, each persona high-stakes and safety-governed. Isolation and per-tenant safety policy are first-class.

---

## 4. Gaps the Analysis Exposes (what Huible is missing)

Cross-referencing Lettuce's maturity against Huible's current `src/huible/`:

| Area | Lettuce has | Huible today | Verdict |
|------|-------------|--------------|---------|
| Persona generation (voice) | Full pipeline | `persona/` empty | **Gap — entire Tier-1 missing** |
| LLM integration | 4 backends, Protocol | none | **Gap** |
| API server / SMS | REST+WS API | `api/adjudication.py` is a class, not HTTP | **Gap** |
| Generation validation | 4-signal validator | none (nothing to validate yet) | Build with voice (2.4) |
| Emotion intelligence | Plutchik vector | affect field = hash fallback | Upgrade to real signal (2.3) |
| Distress/safety escalation | n/a (entertainment) | Advisory module empty | **Gap — must exist before voice** |
| Conversational memory synthesis | Consolidate loop | extractor into gates | Align (2.5) |
| Retrieval fusion | RRF dense+BM25+graph | spreading activation only | Add dense/BM25 fusion (2.2) |
| Memory lifecycle | decay/merge/prune | append-only, no decay ranking | Keep append-only; add decay-as-ranking only (3.1) |

**Highest-priority next steps toward the "first real conversation" roadmap (see brain `/01-projects/huible.md`):**
1. Build the **API server + LLM integration (Tier-1 voice)** — the engine is a "brain with no mouth."
2. Stand up the **Advisory/safety + distress escalation layer** *before* or *with* the voice. Lettuce needs none of this; Huible must not ship without it.
3. Wire **real emotion detection** (structured vector, user-facing for safety, persona-facing for texture).
4. Add **retrieval fusion** (dense + BM25) to the spreading-activation core.
5. Implement **voice/consistency + knowledge-boundary validation** when generation lands.

---

## 5. Bottom Line

**From Lettuce, borrow:** declarative first-person identity, hybrid retrieval fusion, deterministic emotion-vector math, the 4-signal validation scaffold, conversation→memory synthesis, per-user relationship state, Protocol-based swappable backends, era-aware time, and the operational setup-gate/dashboard patterns.

**From Lettuce, diverge (hard):** never delete/supersede-only memory (INV), no autonomous research of a real person, family-approved canonical facts, truth-and-safety validation over in-fiction consistency, emotion read as a safety input, and a privacy/audit posture befitting a real person's legacy.

**Huible is not "a safer Lettuce."** Lettuce optimizes a lively, unconstrained, entertainment character. Huible safeguards and voices a real, trusted legacy. Where they share machinery **cautiously adopt**; where they differ, **Lettuce's way is the wrong way for Huible** — most importantly in the delete-never memory model.

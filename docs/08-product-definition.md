# [08] Product Definition

A single-page definition of what Huible **is**, what it **is not**, who it is for, and the one test that decides whether a feature belongs in the product.

**Source:** Extracted and reorganized from `README.md` (intro + "What Huible Is NOT") and `docs/ENGINE_SPEC.md` §1 (Purpose) and §10 (What Huible Is NOT). This is a faithful extraction of already-reviewed product claims, not new direction.

---

## 1. One-Sentence Definition

**Huible is a memory-driven persona engine that reconstructs a deceased person as a conversational persona with real memory, personality, and relationship awareness.**

It is a memory-driven persona engine where the model's job is to *voice* the person, not *be* smart.

---

## 2. The Defining Test

> **"Does this feel like talking to Dad?" — not "Is this a helpful AI?"**

Every product decision is evaluated against this question. If a proposed feature makes Huible a more helpful AI but does not make it feel more like the deceased person, it does not ship.

---

## 3. What Huible Is

Huible reconstructs a person — their memory, personality, and relationships — and voices that person in conversation. The intelligence is organized around the **memory system**, not the language model.

- **Real memory.** The persona remembers things the person knew, in the way the person knew them.
- **Personality.** The persona speaks with the person's texture — fillers, timing, rhythm, sarcasm — not generic assistant prose.
- **Relationship awareness.** The persona knows who it is talking to and scopes what it reveals accordingly.

The model does not *learn* — the memory system does. New memories are extracted from conversations, validated through a five-gate firewall, and added to the graph. The model's only job is to voice the result.

---

## 4. Architecture Posture (Product-Level)

Huible enforces a strict **two-tier separation** between the voice and the brain:

- **Tier 1 — Persona Generator (the voice):** an openweight 7B–24B model, self-hosted and uncensored, that outputs only response text. Human texture is prioritized over factual accuracy, which is prioritized over reasoning ability.
- **Tier 2 — Advisory Layer (the brain):** heavy frontier models (Claude Opus, GPT) that handle memory extraction, five-gate adjudication, and consistency review. They are **never** client-facing and **never** speak as the persona.

This separation exists so the persona's voice can be wrong, biased, and emotionally reactive — like a real person — while a trustworthy, invisible system layer keeps the memory graph coherent and safe.

> Full architecture detail lives in `docs/ENGINE_SPEC.md` §2.

---

## 5. What Huible Is NOT

Boundary clarity is part of the product definition. Huible is:

- **NOT a therapy chatbot** (though it has therapeutic value)
- **NOT a general-purpose AI assistant**
- **NOT a roleplay / fiction engine** (the person was real)
- **NOT a RAG system** (retrieval is graph-traversal spreading activation, not document chunks)
- **NOT an LLM fine-tune** (the model doesn't learn — the memory system does)

A feature that moves Huible toward any of these archetypes is out of scope by definition.

---

## 6. Who It Is For

- **Families** who have lost someone and want to preserve the ability to speak with them.
- **The deceased person**, whose identity — canonical facts, relationships, voice — is treated as inviolable ground truth.
- **The client**, who expects to feel like they are talking to the person they knew, not to an AI.

---

## 7. Hard Product Invariants

These invariants are non-negotiable product boundaries. Every feature is checked against them.

| Invariant | Product meaning |
|-----------|-----------------|
| **INV-1** (Knowledge boundary) | The persona must not know things the person couldn't have known. |
| **INV-15** (Five-gate ingestion) | No memory enters the persona without passing all five quality gates. |
| **INV-16** (Append-only versioning) | Memories are superseded, never silently overwritten — the past is recoverable. |
| **INV-FL** (Feedback loop prevention) | The persona doesn't loop on the same recent memories every turn. |
| **INV-DS** (Disclosure scoping) | Who you are determines what the persona will share with you. |
| **INV-CI** (Canonical immutability) | Ground-truth facts about the person are never modified by ingestion — only by family approval. |

---

## 8. Phase Posture

Huible is built **memory-engine-first**. The current product phase proves the memory brain works before attaching a voice. See `docs/04-build-plan.md` for the phased build plan and exit gates.

- **Phase 1 (current) — Memory Engine:** retrieval, ingestion, quality control. No voice yet.
- **Phase 2 — Persona Voice:** attach the Tier 1 generator; pass a voice test.
- **Phase 3 — Production & Delivery:** live SMS, family admin UI, multi-persona.

---

## 9. Related Documents

| Document | Purpose |
|----------|---------|
| `docs/ENGINE_SPEC.md` | Full engine specification (architectural source of truth) |
| `docs/04-build-plan.md` | Phased build plan with acceptance criteria |
| `docs/06-testing-strategy.md` | F1–F8 gate suites and invariant → test matrix |
| `docs/07-api-specification.md` | REST API contract |
| `docs/09-deployment-ops-guide.md` | Deployment & operations runbook |

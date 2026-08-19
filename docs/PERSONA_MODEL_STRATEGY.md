# Persona Model Strategy for Huible

**Date**: 2026-07-29  
**Core Principle**: Personas are **human simulators**, not general intelligence engines.

## The Fundamental Mismatch

Most humans:
- Cannot do collegiate math or particle physics at any meaningful level.
- Have uneven, patchy, often superficial knowledge of current events, pop culture, sports, music, TV, gossip, and "life experience".
- Excel (or at least function) at: conversation flow, emotional signaling, sarcasm, small talk, shared memory, relationship nuance, humor that lands in the moment.

Traditional LLM training heavily optimizes for reasoning, broad factual recall, code, science, and long chains of logic. A model trained to "be smart" will almost always feel *alien* when asked to be a specific person.

The persona that knows too much or thinks too hard stops feeling like a person.

## Recommended Architecture: Two-Tier Separation

### 1. Persona Generator (the speaking voice)
This is the model that *is* the person in conversation.

Priorities for this layer:
- Strong conversational texture (timing, fillers, deflection, interruptions, sarcasm delivery, natural speech rhythm).
- Good at pop culture, sports, music, TV, slang, and current events **from the person's lived era**.
- Natural willingness to be wrong, uncertain, biased, or emotionally reactive (like real humans).
- Excellent at maintaining relationship-specific tone and private references.
- **Smaller/medium size is often actively better** (strong 7B–24B class candidates are worth serious attention).
- Edge / small / efficient models are desirable, not just "good enough if we can't afford bigger".
- Must be fully openweight + genuinely uncensored.

**Key point**: Strong performance on math, code, science, or academic reasoning is mostly irrelevant or even *negative* signal for a generator model.

### 2. Advisory / Judge / Extraction Layer (heavy models)
These models never speak *as* the persona. They support the system.

Primary uses:
- Structured memory extraction from raw conversation.
- Adjudication inside the 5-gate ingestion firewall.
- Consistency and drift checking against the original corpus.
- Clinical or relationship-sensitivity review (e.g. the psychology advisor).
- Fact grounding when the actual persona would reasonably look something up or be uncertain.

Allowed in this layer: Claude-class, Gemini, GPT-4o, large Qwen, Llama-70B+, etc. Closed or heavily-aligned models are acceptable here because they are not the voice.

## Model Evaluation Has to Be Split

When researching and testing candidates (Hugging Face, Reddit /r/LocalLLaMA, etc.), we must score on two different axes:

- **Generator Score**: How convincingly human does this model sound as Chandler (or the target person)? Does the voice, rhythm, knowledge level, and emotional tone feel right?
- **Advisory Score**: How reliable, structured, and non-hallucinated is its judgment when used for extraction, consistency checks, or memory adjudication?

A model can be excellent in one role and poor in the other. We should not force the same family to serve both.

## Edge and Small Model Opportunity

We should deliberately hunt and test strong small/edge-class uncensored models for the generator role because:
- Lower latency for real SMS/WhatsApp delivery.
- Much lower serving cost when running many personas.
- Less "overthinking" or sounding like a knowledgeable AI — which is *more* human.
- Easier to self-host multiple concurrent personas.
- Philosophically aligned with the goal ("human, not genius").

If a well-trained 7B–9B model can carry a convincing Chandler voice with proper memory injection, that is a major win, not a compromise.

## Relationship to Other Documents

- `OPENWEIGHT_UNCENSORED_MODELS.md` — living candidate list and evaluation notes.
- `MODEL_PHILOSOPHY.md` — the uncensored / openweight mandate.
- This document — the strategic separation between "human voice" and "advisory intelligence".

When doing research, lead with the question:  
**"How much like a real, imperfect, specific human does this feel in normal conversation?"**  
Not: "How smart or broadly knowledgeable is it?"

## Near-term R&D Implication

As we go through Hugging Face and Reddit candidates, we should be open to (and actively collect) strong small models for generator use, while treating larger/smarter models primarily as candidates for the advisory tier or for heavy extraction work during ingestion.

This is a deliberate philosophical and architectural choice, not a cost compromise.

---

## Day-1 Provider Decision (2026-08-19, HU-1910 executing HU-1461)

Board approval granted (Pat, WhatsApp 2026-08-19): Chandler onboarding starts
this week with a chat-ready day-1 voice on an existing subscription. The
LLM-vs-SLM-vs-finetune decision is deferred until real conversation data
exists (Stage-A dogfood / HU-1911 output feeds it).

**Day-1 voice: zai glm-5.3** on the existing GLM coding subscription
(`https://api.z.ai/api/coding/paas/v4`, OpenAI-compatible, `$0` incremental
metered spend). Deployed posture on .245 (`huible-app`):

| Knob | Value | Consumed by |
| --- | --- | --- |
| `LLM_PROVIDER=zai` (+ `ZAI_*`) | glm-5.3, `ZAI_THINKING=disabled`, 200k tokens/day ceiling | `POST /api/v1/chat/{persona_id}` (`ZaiLLMClient`, `src/huible/llm/client.py`) |
| `GENERATOR_PROVIDER=openai_compatible` (+ `GENERATOR_*`) | same zai endpoint via `GENERATOR_EXTRA_JSON={"thinking":{"type":"disabled"}}` | `POST /api/v1/chat` (persona generator, `src/huible/persona/generator.py`) |

Guardrails: durable per-UTC-day token ledger (`/var/lib/huible/zai-tokens.json`,
blocks *before* the network call, resets daily), one structured `zai.usage`
cost log line per conversation turn, and a one-knob abort
(`LLM_PROVIDER=fake` + `GENERATOR_PROVIDER=mock`, restart).

**Why `thinking: disabled`**: glm-5.3 defaults to reasoning-on and its
reasoning tokens share the `max_tokens` budget — persona turns can burn the
entire budget on hidden chain-of-thought and surface as empty content
(observed live on .245). The persona voice needs no reasoning
(this doc's core principle), so both paths opt out.

## Swap-Out Contract: what an SLM / edge candidate must implement

The day-1 provider is a **deliberately replaceable stopgap**. A self-hosted
openweight model (the strategy-preferred end state) drops in by satisfying
either of two integration points — no chat-handler changes required:

1. **LLM client path** — implement the `LLMClient` protocol
   (`async generate(prompt, *, system_prompt=None, **kwargs) -> str`):
   - Serve an **OpenAI-compatible `/chat/completions`** route and point
     `GENERATOR_BASE_URL` at it (zero code change), **or**
   - Add a provider enum value + client class in `src/huible/llm/client.py`
     following `ZaiLLMClient` (config fields, `from_env` parsing, factory
     branch, key gate that raises `LLMConfigError`).
2. **Behavioral requirements** (the parts callers rely on):
   - Accept `system`-role persona instructions and honor them verbatim
     (reality framing must not be paraphrased away).
   - Return non-empty string content on the persona prompt, within
     `max_tokens` (no hidden-reasoning budget burn — cap or disable
     chain-of-thought).
   - Report OpenAI-style `usage` token counts if a usage ceiling applies;
     otherwise wire an equivalent pre-call guard in the client class.
   - Keep the failure modes loud: transport/HTTP errors raise `LLMError`
     subclasses so monitoring sees them; budget/ceiling exhaustion raises
     `LLMBudgetExceededError` so the approved fake-voice degraded posture
     serves the turn instead of erroring.
3. **Swap procedure**: flip `LLM_PROVIDER`/`GENERATOR_*` env values (one
   change per surface), restart the container, run
   `scripts/verify_voice_dogfood.py --key-prefix chandler-` (exit 0 proves
   real-voice round-trips on the live deployment).

Candidate evaluation should reuse the Stage-A dogfood battery + fidelity
questions from HU-1911 rather than ad-hoc vibes checks.

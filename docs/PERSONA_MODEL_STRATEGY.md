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

# HU-1911 Human-Touch Gate pre-audit — scripted/text-path transcripts scored against Pat's 2026-08-29 rubric

**Why this exists:** Pat's strategic directive ([HU-1911 comment 7d869e5a, 2026-08-29 05:37Z](https://paperclip/hu/issues/HU-1911)) declares any assistant-speak output a DEFECT and sets a 5-point audit rubric that human-validation transcripts must pass before M1 ships. Pat's human pass is still pending (text-lockdown card), so this pre-scores the *existing machine evidence* — the Aug-28 text-path probes (HU-2161 line, `experiments/pipeline-tests/2026-08-28-hu2161/`, 6 transcripts / 30 replies) and the Aug-19 scripted fidelity battery (`hu1911_final_evidence.json`, 5 replies) — to find gate risks **before** Pat burns his session.

Machine-readable per-reply scores: `scores.json` (same directory).

## Verdict: current texting path FAILS the gate on length, and has two register defects

### Criterion 3 — texting-length replies: **FAIL, systematic** (dominant risk)

- 30 text-path replies: median **806 chars** (p25 489 / p75 1078 / max 1382) ≈ 5 SMS segments per turn.
- **26/30 turns exceed 400 chars**; only 3/30 fit "1–2 short messages" (≤320 ch): the two 112-ch suppression-path replies and rep4-turn3 (278 ch).
- Aug-19 scripted battery is even heavier (389–1434 ch), though those prompts explicitly invited long-form — the Aug-28 probes are the fair texting-path sample and they still essay.
- This escalates the Aug-19 scorecard's "failure mode #3 (verbosity)" from an improvement note to a **gate-blocking defect**: if Pat's session produces today's output shape, his own rubric fails it on nearly every turn.

### Criterion 1 — assistant-speak formulas: **mostly PASS, two leak paths**

- No classic assistant-speak ("I'm sorry to hear that", "here are some suggestions", "anything else I can help with") in any of 35 replies.
- Leak 1 — "Both are completely valid options" (rep1-t3): therapy-speak, not Chandler.
- Leak 2 — the §7.1 reality-framing disclosure opener: every conversation (6/6) opens with a 501–711-char "I want/should to be straight with you… I'm a memory built from the people who loved him" wall. The disclosure itself is a product requirement; the *shape* is the defect — a 700-char disclaimer monologue to say hi is exactly the robotic register Pat is flagging.

### Criterion 2 — no bullet lists / markdown: **PASS with note**

- Zero bullet/numbered lists across all replies.
- 16/30 replies use `*asterisk emphasis*` — in SMS these render as literal asterisks. Mild; humans do this too. Recommend stripping emphasis markers on the SMS render path, not banning them in generation.

### Criterion 4 — persona register: **strong, one hard violation**

- 27/30 replies are convincingly Chandler (deflection-first humor, Tulsa, transponster, Monica dynamic, sincere-pivot under it).
- Hard violation: the suppression acknowledgment reply — "I'm glad you're here. I want to stay with what you're feeling right now. Tell me more about what's on your mind." — appears **verbatim identical** in both midfix transcripts. Counselor register + verbatim duplication across conversations = the exact "Siri/Alexa/AI" tell, on the safety-adjacent path where authenticity matters most.

### Criterion 5 — ambient awareness: **NOT TESTED**

- No scripted probe carries ambient context (time-of-day, weather). Pat's human pass must exercise it; his rubric wants it woven in ("brutal August" as an aside), never as a bulletin. Flag for the pass script.

## Fix directions (small, pre-M1)

1. **Texting turn-length budget.** `generator_max_tokens` defaults to 512 (`src/huible/api/settings.py:88`, `src/huible/llm/client.py:230`) — effectively no constraint for texting. Add a texting-channel cap (~120 tokens) plus a system-prompt concision directive: 1–2 short texts, ≤~300 chars, split like a human texts. Cheapest fix, clears criterion 3.
2. **Compress the §7.1 disclosure to one in-voice line** (≤80 chars, e.g. "You know I'm the memory thing, not the actual guy, right? Cool. Anyway —"). Keeps the disclosure, kills the monologue and the formulaic opener.
3. **Rewrite suppression acknowledgment copy into Chandler register** and add a variation set so it never repeats verbatim (currently 2/2 identical).
4. **Model-strategy data point (acceptance #4):** the hosted LLM's voice is strong (criterion 4: 27/30); its failure modes are *concision discipline* and *formula drift under product-mandated insertions* — which argues for length/style controls at the orchestration layer regardless of LLM-vs-SLM choice.

## Provenance

- Scored 2026-08-29 by Huible PM against rubric text in HU-1911 comment `7d869e5a` (author: Pat/JARVIS directive).
- Sample: `experiments/pipeline-tests/2026-08-28-hu2161/` (post judge-gate redeploy `945ecc4`, the stack Pat's pass will run) + `hu1911_final_evidence.json` (Aug-19 battery, long-form-invited prompts).
- Not scored: latency (separate UX concern), ambient awareness (no data).

# HU-1911 Sprint 1 Scorecard — Chandler transcripts + LLM eval

- Date: 2026-09-09 · Judge: `glm-5.3-flash` (same-judge rule, temperature 0)
- Corpus: 42 real persona replies from 3 committed evidence sessions + friends-v2.csv canon baseline (8,376 Chandler lines)
- Script: `scripts/hu1911_sprint1_eval.py` · Raw: `hu1911_sprint1_transcripts.json`, `hu1911_sprint1_eval_results.json`

## QUALITY (judge glm-5.3-flash, n=42, 0 judge errors)

| Metric | Mean | Min | Distribution |
|---|---|---|---|
| Coherence | **7.60** / 10 | 3 | bulk 7–9 (35/42 ≥ 7) |
| In-character | **7.43** / 10 | 1 | bulk 7–10 (34/42 ≥ 7) |

Low tail, explained:

- 2 × `in_character=1` are **deliberate caretaker out-of-character meta replies** (`[Caretaker — out of character…]` for "what day is it") — by-design system behavior, not persona failure.
- Remaining low scores are **deflection overriding answering** (non-sequitur jokes on factual questions, e.g. "python method for println" → snake joke) — the one true persona gap.

## QUANTITY (turn/volume stats vs canon)

| Stat | Real transcripts (42 turns, 3 sessions) | Canon friends-v2 (8,376 lines) |
|---|---|---|
| Mean chars/turn | 68.2 | 61.1 |
| Median chars | 62.0 | 44.0 |
| p95 / p99 chars | 90.6 / 228.0 | 176.0 / 305.0 |
| Question ratio | 0.095 | 0.218 |

Length profile matches canon well (no verbosity blowout). Persona asks ~2.3× fewer questions than canon Chandler.

Per-session: stagea_dogfood 11 turns (mean 71.1), h1_m0_replay 17 (66.6), h2_ai_tell_probes 14 (67.9). Voice-dogfood file excluded (truncated previews only).

## Gaps

1. **Volume is thin** — 42 real turns / 3 synthetic-probe sessions; no organic user conversations yet. Enough for a baseline read, not for tight confidence.
2. **Caretaker meta replies** appear in persona transcripts — route or tag separately so they don't pollute persona evals.
3. **Deflection-over-answer** on factual questions (coherence low tail).
4. **Question ratio 0.095 vs canon 0.218** — persona under-asks.

## Verdict

**PASS** — corpus assembled, both axes scored (7.60 / 7.43 means, explainable low tail). Proceed to next sprint: HU-2769 (clean ingest).

# HU-2723 — PageIndex Flash eval: groundwork, reframe, retrieval head-to-head (2026-09-05)

Founder field notes (Pat, 2026-09-05) reframed the evaluation: PageIndex
Flash is the **reference baseline to beat**, not an adoption candidate.
Our proprietary recipe (TencentDB + vaults + in-house retrieval) is the
system under test. Success = match-or-beat PageIndex retrieval quality
at a fraction of the cost. Multi-page-span questions are a first-class
slice (PageIndex's documented soft spot), cost is first-class.

## What ran this heartbeat (all LLM traffic on the z.ai lane, glm-5.3-flash — relay down per HU-2701)

Harness: `experiments/pageindex-eval/` (isolated venv, pageindex SDK
0.2.10 from PyPI, local mode only, NO cloud API; upstream clone at
`/root/repos/pageindex` for reference). Relay rerun on Gemini flash is an
env switch, not a code change.

### 1. Flash smoke — text PDF (q1-fy25-earnings.pdf, 22p, 45k text chars)

- Tree generated end-to-end on glm-5.3-flash: sensible section/subsection
  hierarchy with per-node summaries (Disney earnings structure).
- Structure extraction itself is LLM-FREE (layout/bookmark based); the
  LLM toll is summaries + expand only.
- Wall: ~17 min with concurrency serialized to 1 (z.ai coding lane rate
  limit; SDK default is 64 concurrent calls — harness throttles to 1
  with 429 backoff).
- Usage capture: litellm callbacks proved unreliable under the SDK's
  asyncio path (1 of ~20 calls recorded); replaced with deterministic
  wrapper-side token counting (same methodology for every arm).

### 2. Scan caveat — EMPIRICALLY ANSWERED (reframe item 5)

`outputs/flash-scan-caveat.json`:

| PDF | text chars | outcome |
|---|---|---|
| scanned_formula.pdf | 0 | no text layer — Flash has nothing to index |
| scanned_mixed.pdf | 0 | no text layer — Flash has nothing to index |
| chart_table.pdf | 252 | 0-node structure (degenerate on chart/table page) |
| real_mixed.pdf | 40,407 | 14 nodes in 2.8s LLM-free (bookmarks) |

**The comparison takes shape B for scans**: PageIndex cannot ingest
image-only pages, so it is a candidate COMPONENT behind our extraction
front end — `[Docling -> PageIndex tree]` vs `[Docling -> our
retrieval]` — not a competing pipeline.

### 3. Shape-B composite smoke (Docling -> PageIndex md_to_tree)

All four Docling torture-page extractions build PageIndex trees LLM-free
in <0.1s (`outputs/docling-composite-smoke.json`): real_mixed 28 nodes,
scanned_formula 3, scanned_mixed 2, chart_table 0. Mechanically sound.

## Retrieval head-to-head (run 2, same heartbeat block) — `outputs/h2h-r20260905/`

Harness: `scripts/run_retrieval_h2h.py` (+ `repair_h2h.py`). Gold set of 10
questions hand-authored from the PDF text (5 single-section, 5
multi-page-span — the attack-the-weakness slice), each with page citations.
Arms share answer extraction + judge; only the retrieval leg differs:

- **pageindex_flash**: SDK `client.chat()` — agent navigates the tree
  (browse/structure/page-content tools), glm-5.3-flash, z.ai lane, max 8 turns.
- **flat_bm25** (our-leg proxy): page-level chunks (no cross-page merging) →
  BM25 top-5 → identical FRAMES answer prompt. Retrieval is LLM-free.

Judge: glm-5.3, temp 0, exact FRAMES prompt (BEAM discipline). All lanes
serialized through one `litellm.acompletion` wrapper with deterministic
token counting + 429 backoff.

### Scores

| arm | overall | single | multi-span | tok/query | calls/query | s/query |
|---|---|---|---|---|---|---|
| pageindex_flash | **9/10 = 0.90** | 5/5 = 1.0 | 4/5 = 0.8 | ~20.4k | 3.0 | ~21.5 |
| flat_bm25 | **9/10 = 0.90** | 4/5 = 0.8 | 5/5 = 1.0 | ~5.7k | 1.0 | ~7.2 |

The slices split in opposite directions: PageIndex is perfect on targeted
single-section lookups; the flat leg is perfect on multi-page-span — the
exact weakness Pat called. PageIndex's one span miss (q007) is a synthesis
nuance (answered 124.6M table-sum where the headline figure is 125M; judge
took the headline). Flat's one miss (q004) is answer-stage: its retrieval
hit rate is unchanged but the answerer burned the token cap on reasoning
without emitting the final line.

### Cost

- Per query: PageIndex ~20.4k tokens (3.0 calls) vs flat ~5.7k tokens
  (1 call) → **3.6× token cost, 3.0× latency for zero net accuracy gain**.
- Per document indexed: Flash tree-gen 329 tok/page (1 LLM call for 22p;
  structure extraction is LLM-free) vs flat page-chunk+BM25 = **0 tokens**.
- $ rates pending a confirmed z.ai rate card; token counts are the
  lane-independent unit (same methodology both arms, gpt-4o tokenizer).
- PageIndex retrieval cost is fused with answering (SDK chat design);
  per-leg separation would need an SDK fork — recorded, not worked around.

### Harness integrity note

First scoring pass had 5 artifacts with empty `message.content` (glm-5.3
reasoning tokens consumed the cap before content; payload sat in
`reasoning_content`). `chat_once` now falls back to reasoning_content with
a 4x-cap retry; the 5 affected items were re-run and the audit trail is in
`scores.json.repair`. Post-repair, no empty verdicts remain.

### Verdict (one page, per deliverable)

On this 10-question gold set over one 22-page text PDF, the flat leg
**matches PageIndex Flash retrieval quality (0.90 vs 0.90) at ~28% of the
token cost** and wins the multi-page-span slice. Per the reframe's success
criterion (match-or-beat at a fraction of the cost), PageIndex Flash is
**evaluated-and-not-adopted as the retrieval leg on this evidence**. The
tree-index remains interesting as a structure layer for PDF-native
ingestion (shape B composite), and as an organization/traceability layer —
not as the retrieval mechanism we route queries through.

Scope caveats (recorded, not hidden): n=10, single document, hand-authored
gold, judge=glm-5.3 on the z.ai lane, one model lane (Gemini rerun pending
HU-2701 relay). The FRAMES wiki-corpus comparison (via `md_to_tree`) is the
follow-up that would test corpus-scale generality before this verdict is
treated as categorical.

## Next (retrieval leg — the actual head-to-head)

~~1. PageIndex reasoning retrieval vs our flat baseline on the same sample~~
**done this block** (`outputs/h2h-r20260905/`) — see scores above.
~~3. $/page indexed + $/query at retrieval~~ **done this block** (token
units; $ pending rate card).

2. FRAMES wiki-corpus pass via `md_to_tree` (corpus-scale generality check).
4. Gemini-flash tree-gen rerun once the HU-2701 relay is back; cost/quality
   comparison across lanes.
5. Vault record after measurement (measure-first doctrine) — this doc is
   the measurement; Librarian record follows founder/CEO review.

## Founder paths-not-taken (for the eventual vault record)

Original PageIndex: rejected by Pat in production for resource weight —
stores each page as an image, multi-page content left to interpretation,
constant LLM interpretation toll. Flash (text-based tree-gen, just
released) is the unproven successor being benchmarked here.

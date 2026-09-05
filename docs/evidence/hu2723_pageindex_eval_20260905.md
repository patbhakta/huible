# HU-2723 — PageIndex Flash eval, groundwork + reframe (2026-09-05)

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

## Next (retrieval leg — the actual head-to-head)

1. PageIndex reasoning retrieval (tree navigation via SDK agent tools)
   vs our flat baseline (r20260905b: 12/20 = 0.60, provenance 20/20) on
   the same FRAMES sample.
2. Multi-page-span question slice (answers straddling 2+ pages/sections)
   — the attack-the-weakness slice.
3. $/page indexed (rerun smoke with fixed ledger) + $/query at retrieval.
4. Gemini-flash tree-gen rerun once the HU-2701 relay is back;
   cost/quality comparison across lanes.
5. One-page verdict; vault record afterwards (measure-first doctrine).

## Founder paths-not-taken (for the eventual vault record)

Original PageIndex: rejected by Pat in production for resource weight —
stores each page as an image, multi-page content left to interpretation,
constant LLM interpretation toll. Flash (text-based tree-gen, just
released) is the unproven successor being benchmarked here.

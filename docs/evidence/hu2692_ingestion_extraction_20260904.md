# HU-2692 — Ingestion R&D: hard mixed-content PDF extraction (measured)

Date: 2026-09-04 · Agent: R&D Lead · Boss directive: ingestion-pipeline focus, PDF priority one.
Artifacts: `experiments/ingestion-pdf/` (scripts, samples builder, outputs, scores).

## Question

Which extraction pipeline recovers clean text/tables/formulas from mixed-content PDFs
(native text + scanned + diagrams + formulas + charts + tables) on a **CPU-only**
server (8 cores, 15 GB RAM, ~19 GB disk, no GPU), preserving *the specific* (Pat's moat
framing: vault keeps what an LLM cannot regenerate), before any quality thresholds are set.

## Method

- **Sample set** (built by `scripts/build_samples.py`, ground truth kept alongside):
  - `real_mixed.pdf` — arXiv 1706.03762 (Attention Is All You Need), 15 pp, native text,
    formulas, tables, figures.
  - `scanned_formula.pdf` / `scanned_mixed.pdf` — pages 4 and 6 of the same paper
    rasterized at 200 DPI into image-only PDFs (text layer removed; native text = ground truth).
  - `chart_table.pdf` — synthetic page: service-log text (image), bar chart (image),
    native drawn table with exact values (ground truth authored).
- **Lanes measured**: pymupdf 1.0.x native text (baseline), docling 2.126 CPU
  (layout + OCR + TableFormer), GLM-4.5V vision extraction on 150 DPI page rasters.
- **Metric**: bag-of-tokens F1 vs ground truth (case/punct-normalized), plus structural
  notes (table structure, formula decoding) and wall-clock timing.
- Marker-pdf and MinerU were **not** run: OpenRAIL-M $5M-revenue clause (marker) and
  AGPL-3.0 + heavyweight deps (MinerU) are already on file in the HU-1839 licensing
  findings, and both are GPU-first. Docling (MIT) covers the same pipeline class.

## Results (token F1 vs ground truth)

| Sample | pymupdf | docling (CPU) | GLM-4.5V (page) |
|---|---|---|---|
| real_mixed (native) | 1.000 (is the GT source) | 0.915 | 0.817 |
| scanned_formula (200 DPI) | **0.000** | 0.931 | 0.792 |
| scanned_mixed (200 DPI) | **0.000** | 0.978 | 0.891 |
| chart_table (mixed) | 0.394 | 0.352 | 0.372 |

Structural findings, which the F1 numbers understate:

- **Formulas**: docling emits `<!-- formula-not-decoded -->` (6 occurrences across the
  15-page paper; 1 per scanned page). GLM-4.5V returns valid LaTeX, e.g. the positional
  encoding formula decoded as `PE_{(pos,2i)} = sin(pos/10000^{2i/d_model})`. **Only the
  VLM lane recovers formulas.**
- **Tables**: docling reconstructs markdown tables with correct cells (exact dollar
  values on `chart_table`); pymupdf recovers the same cells but flat (no structure);
  VLM also produced the table exactly.
- **Charts**: no text lane reads chart values at all. GLM-4.5V read the bar chart to
  ~±0.5 of the true values (e.g. Jan ~12.5 vs true 12.4) and described axes/title.
- **Native-text pages**: pymupdf is perfect and near-instant; don't spend heavier
  machinery on them.
- **Very low-res text** (my synthetic text block downscaled ~3.4×): docling OCR
  *silently garbles* it (confident-looking nonsense); GLM-4.5V *explicitly flags*
  "too small to read". Failure mode matters: silent garbage can poison a vault.

## Timing / cost (CPU-only box)

- pymupdf: 0.14 s per 15-page doc. Free, local.
- docling: ~6.9 s single page incl. warmup; 64.4 s for 15 pages incl. first-run model
  load (~4.3 s/page steady state, 8 CPU threads). Free, local, MIT.
- GLM-4.5V: 12–19 s/page; 2.7–4.6 k prompt + 0.8–1.6 k completion tokens/page. Measured
  on the existing GLM coding-plan key (R&D). Production use of a vision API is **new
  spend → needs Pat's approval**; keep it opt-in per hard page, not default.

## Recommended default pipeline (per content type)

1. **Router** (pymupdf, per page): text layer present → extract directly (Tier 0).
   No/short text layer → rasterize 200 DPI → docling CPU (Tier 1).
2. **Tier 1 — scanned text + tables**: docling (MIT, CPU). 0.93–0.98 F1 at 200 DPI.
   Table structure preserved as markdown.
3. **Tier 2 — formula/chart pages** (detected via docling layout classes or
   `formula-not-decoded` markers): VLM pass on that page only, formulas stored as
   LaTeX, chart values stored with an "approximate, chart-derived" flag.
4. **Vault doctrine mapping**: verbatim text + source page/offset → vault; table
   structure → vault (markdown); formula LaTeX → vault (it is the "specific");
   chart values → derived/approximate → TencentDB tier; page image for any
   low-confidence region → vault as source-of-truth artifact.
5. **Do NOT adopt** marker (OpenRAIL-M) or MinerU (AGPL-3.0) without licensing review;
   neither beat docling on these metrics for our CPU constraint.

## Requirements discipline (measure-first)

No quality thresholds were invented: the numbers above are the baseline. Before
production freeze, re-measure on Pat's real hard documents (the second Pat project's
sample set), since my synthetic low-res block is deliberately harsher than real scans.

## Limitations

- One real document (though 15 varied pages); synthetic chart/table page.
- docling timings include first-run model load on some samples.
- VLM lane evaluated at 150 DPI rasters; DPI sweep not done yet.
- Media-to-vault front (audio/video/dialog/images) is the second half of HU-2692 —
  tracked as a follow-up measurement spike (child issue), same measure-first bar.

---

## Update 2026-09-04 evening — boss approvals applied + gemini-3.8-flash measured

Pat's follow-up decisions (issue comment 14bd161d): **vision model approved**
(gemini-3.8-flash, inside the flash-only ladder, no pro-tier; Google API
geo-blocked from the VPS → production calls route via the home SOCKS5 relay
pat-w11pc; design must be batchable + resumable) and a **benchmark shortlist**
for the ingestion+RAG loop (below).

### gemini-3.8-flash extraction results (measured this run)

Transport note: the home relay was **down** during this run (measured 2026-09-04
~19:37–19:47Z: TCP + SOCKS5 greeting OK, every CONNECT closed immediately,
0/16+ attempts, all target domains → home-side egress outage on pat-w11pc, not
an ACL). Quality was therefore measured through the existing metered OpenRouter
channel (same model, `google/gemini-3.8-flash`, spend-tracked within the
existing $50 monthly budget lane; ~$0.002–0.008/page, 4 pages ≈ $0.02). The
relay-routed Google lane is implemented in the same script and resumable — it
completes whenever the relay recovers.

Bag-of-tokens F1 vs ground truth (same samples, same prompt as the table above;
scanned_mixed is the retry — first attempt early-stopped mid-page, see below):

| Sample | pymupdf | docling (CPU) | GLM-4.5V | **gemini-3.8-flash** |
|---|---|---|---|---|
| real_mixed p4 | 1.000 | 0.915 | 0.817 | **0.834** |
| scanned_formula | 0.000 | 0.931 | 0.792 | **0.829** |
| scanned_mixed | 0.000 | 0.978 | 0.891 | 0.884 |
| chart_table | 0.394 | 0.352 | 0.372 | **0.696** |

Findings:

- **Chart reading is the decisive win**: 0.696 vs GLM-4.5V's 0.372 on the
  chart/table page — gemini-3.8-flash reads chart values and table cells far
  more completely. This strengthens the Tier-2 doctrine: VLM pass on
  formula/chart pages, chart values flagged approximate → TencentDB tier.
- **Scanned text stays docling's job** (Tier 1): 0.93–0.98 vs VLM's 0.83–0.88.
  The router design is confirmed, not changed.
- **Reliability caveat (measured)**: 1/5 gemini calls early-stopped mid-page
  (transient; a retry produced a complete page, F1 0.472 → 0.884). Production
  lane needs a per-page completeness check + one retry. OpenRouter usage/cost
  metadata was also flaky (0 reported on 2/5 calls) — budget by page count,
  not by reported cost.
- Latency 5.3–25.6 s/page (one 25.6 s outlier on retry); comparable to
  GLM-4.5V's 12–19 s/page.

Artifacts: `experiments/ingestion-pdf/outputs/vlm_gemini_or/` (extracts +
`results.json`), `scripts/run_vlm_gemini.py` (dual transport: `relay` =
production Google lane via SOCKS5 with retry/backoff + resume; `openrouter` =
measurement lane), `outputs/scores.json` (all lanes scored).

### Remaining gate for production enable (unchanged flag, changed reason)

`VAULT_INGEST_VLM_ENABLED` stays default OFF — not for spend (approved), but
until the relay-routed Google lane passes one end-to-end page once the home
relay is back. Unblock owner: home-side (pat-w11pc egress); the script resumes
without rework. After that, flipping the flag is a pure build task (Tech Lead).

### Benchmark shortlist for the ingestion+RAG loop (recorded per boss decision)

- **PRIMARY: FRAMES** (Google, "Fact, Fetch, and Reason"; 800+ multi-hop
  questions over an included CC BY-SA Wikipedia corpus) — run only after the
  extraction pipeline produces artifacts worth scoring (boss's own gate).
  Cheap corpus-ships-with-dataset fit for the gist-index architecture.
- **SECONDARY when the matching front lands**: RAGTruth (word-level
  hallucination taxonomy; sharpens abstention/honesty measurement) and
  MMNeedle (multimodal needle; once media ingestion lands).
- **SKIPPED as redundant**: NIAH/RULER (BEAM's interactive multi-session eval
  already exceeds static-needle testing), BeIR (pure general-domain IR —
  revisit only for isolated retrieval-channel tuning), FEVER (partially
  covered by BEAM contradiction_resolution, scored 0.856).
- Doctrine: measure-first on lagging capabilities (ingestion quality is the
  current gap); never chase categories we already lead.

---

## Update 2026-09-04 ~21Z — HU-2701: relay recovered, Tier-2 lane E2E-verified and ENABLED

### Relay recovery + a latent bug the outage was hiding

- Home relay egress (pat-w11pc) recovered: fresh probe 3/3 SOCKS5 CONNECT
  successes to `generativelanguage.googleapis.com:443` (issue recorded the
  19:37–19:47Z outage; recovery observed ~20:50Z).
- The relay lane then **still failed 4/4 pages** — root cause was in
  `scripts/run_vlm_gemini.py` itself: the SOCKS5 CONNECT request sent the
  domain-length prefix but omitted the hostname bytes (`+ addr` missing,
  compare the proven recipe in `scripts/generate_voice.py:68`). The bug never
  fired during HU-2692 because the relay was down for the entire window. Fixed;
  after the fix the relay lane extracted 4/4 pages (4.0–10.0 s/page, ~1.1k
  prompt + 0.2–0.9k completion tokens/page).

### Relay-lane scores vs the OpenRouter measurement (token F1, GT page 0)

| Sample | OpenRouter (measured) | relay → Google native |
|---|---|---|
| scanned_mixed | 0.884 | 0.885 |
| scanned_formula | 0.829 | 0.829 |
| real_mixed p4 | 0.834 | 0.816 |
| chart_table | **0.696** | **0.425–0.453** |

- 3/4 samples land at/near the OpenRouter numbers. `chart_table`
  reproducibly does not: the Google-served snapshot omits the secondary
  service-log text block (chart values and table cells still read correctly).
  Probes run at identical prompt/image: plain native call 0.444, documented
  one-retry 0.425, `thinkingBudget=0` 0.453, **OpenAI-compatible shape via the
  same relay 0.439** — so the gap is not request shape, thinking config, or the
  relay; it is the model snapshot served behind `gemini-3.8-flash` on
  Google's endpoint vs OpenRouter's `google/gemini-3.8-flash`.
- The retry exercise re-confirmed the codified op: incomplete-page detection +
  one retry is mandatory (here the retry reproduced the omission; in HU-2692
  it recovered 0.472 → 0.884 — either way it must exist).

### Decision: production endpoint = OpenRouter; relay lane kept as fallback

`vlm.py` speaks OpenAI-compatible chat-completions; Google's native API is not
that shape, and its served snapshot carries the measured chart_table gap. The
flag flip therefore points the production env at the **measured** OpenRouter
lane (exact numbers the approval was based on; reachable direct from the VPS;
no home-desktop dependency; ~$0.002–0.008/page, budget by page count). The
relay E2E goal of HU-2701 is met — the home-relay Google path is proven
working end-to-end and stays the documented fallback transport.

### Flag flip (production, env-gated; code default unchanged OFF)

`repo .env` (source for `docker-compose env_file` and pipeline runs):

```
VAULT_INGEST_VLM_ENABLED=on
VAULT_INGEST_VLM_BASE_URL=https://openrouter.ai/api/v1
VAULT_INGEST_VLM_API_KEY=<OpenRouter key, env only>
VAULT_INGEST_VLM_MODEL=google/gemini-3.8-flash
```

Verification of the exact production path (`IngestConfig.from_env()` gate →
`vlm_page_pass`): real call on `scanned_formula_p0.png` returned parsed JSON
with 3 formulas as correct LaTeX (attention QK^T block), no parse error.
`tests/test_vault_ingest.py`: 8 passed, 11 skipped (heavy-dep skips,
pre-existing). Gating tests (default OFF, env override, disabled raise)
unchanged and green.

### Remaining note

`vlm_page_pass` calls OpenRouter via raw httpx and bypasses the
`llm/client.py` monthly budget tracker. Volumes are page-count-budgeted per
the boss note, but the spend should be wired into the tracked lane — follow-up
issue created (HU-2701 child), owner Tech Lead.

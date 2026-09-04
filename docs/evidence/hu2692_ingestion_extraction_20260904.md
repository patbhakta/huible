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

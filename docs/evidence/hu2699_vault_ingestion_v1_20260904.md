# HU-2699 — Vault ingestion pipeline v1 built (CPU-only PDF router + media atoms)

Date: 2026-09-04 · Agent: Huible Tech Lead · Parent plan: HU-2692 (frozen game plan,
rev 2026-09-04) · Measured bases: `hu2692_ingestion_extraction_20260904.md` (PDF),
`hu2697_media_ingestion_20260904.md` (media).

## What landed

New module `src/huible/vault_ingest/` (seeded from the experiment scripts — no framework
rewrite):

- `pdf.py` — per-page router: pymupdf text-layer check → Tier 0 direct extraction;
  no/short text layer → raster 200 DPI → docling CPU (Tier 1). Formula pages
  (`formula-not-decoded` markers) retain their page image in the vault as
  source-of-truth artifact.
- `vlm.py` — Tier-2 VLM lane **implemented but flag-gated OFF by default**
  (`VAULT_INGEST_VLM_ENABLED`, default off; production enable = new spend → Pat's
  approval). Credentials only ever read from env (`VAULT_INGEST_VLM_BASE_URL/API_KEY/
  MODEL`) — nothing hardcoded. Chart values come back flagged
  `approximate, chart-derived` → TencentDB tier; formula LaTeX → vault tier.
- `audio.py` — faster-whisper `base.en` int8, 8 CPU threads → verbatim segments with
  timestamps + provenance (model/compute/WER context). `no_speech_prob` stored per
  segment; VAD/no-speech gating stays the R&D follow-up (flag
  `vad_gating_followup` on every transcript).
- `video.py` — ffmpeg composition: audio track → 16 kHz wav → whisper lane; 1 fps
  frames → image lane. Original = vault; wav/frames = regenerable (derived).
- `images.py` — image stored as source-of-truth artifact; retrieval rides extracted
  text (no CLIP in v1, per the HU-2697 measurement).
- `atoms.py` — two-tier layout: `vault/{originals,atoms,page_png}` +
  `derived/{atoms,media}` + `manifest.json`. Vault keeps only what an LLM cannot
  regenerate; derived/approximate goes to the TencentDB tier path.
- CLI: `python -m huible.vault_ingest --out DIR FILE...` (auto-dispatch by extension).

Deps: pyproject optional group `ingest = [pymupdf, docling, faster-whisper]`
(fastembed already main). No marker-pdf, no MinerU (licensing on file). No new spend.

## End-to-end run (this box, 8-core CPU)

One CLI run, all 7 samples → `manifest.json` with `vlm_enabled: false`:

| Input | Lane | Result |
|---|---|---|
| real_mixed.pdf (15 pp) | router → Tier 0 ×15 | 0.32 s |
| scanned_formula.pdf | router → Tier 1 (docling) | formula flagged ×1, page image → vault |
| scanned_mixed.pdf | router → Tier 1 (docling) | 10.0 s |
| chart_table.pdf | router → Tier 0 | native table text, `has_images_vlm_pending` |
| LibriSpeech 121-121726-0000.flac | whisper base.en | verbatim atom, 2 segments |
| bbb_720p.mp4 | ffmpeg → wav + 596 frames @1fps → whisper | 98.2 s total |
| real_mixed_p3.png | image artifact | vault, retrieval-rides-text note |

Atoms: 22 total — 21 vault, 1 derived (`media_frames`, regenerable by design).

## Regression smoke tests (baselines pinned)

`tests/test_vault_ingest.py` — 19 passed, 0 failed on the ingest environment
(`experiments/ingestion-pdf/.venv` + `faster-whisper` added additively;
`PYTHONPATH=src` for the huible package). Docling tests skip cleanly in environments
without it (repo venv: 14 passed / 5 skipped — whisper lanes still run there).

Pinned baselines (measured HU-2692/HU-2697 numbers, with margin):

- PDF token-F1 vs ground truth: real_mixed ≥ 0.99 (measured 1.000), scanned_formula
  ≥ 0.90 (0.931), scanned_mixed ≥ 0.93 (0.978), chart_table ≥ 0.30 (0.394).
- Whisper base.en corpus WER over the full 32-utterance manifest ≤ 0.09
  (measured 0.0773); single-file WER ≤ 0.35 (measured 0.1176).
- VLM-off acceptance: default config off; env override works; disabled lane raises
  with the spend-approval reason; formula pages flagged + image retained; no VLM
  atoms can exist while off.
- Router acceptance: native pages Tier 0, scanned pages Tier 1.

Test-caught defect: atom filename collisions silently overwrote per-page atoms
(15 in-memory atoms → 1 file). Fixed with explicit per-page slugs; the
disk-based regression test now guards it.

## Verification

- `ruff check` clean on `src/huible/vault_ingest/` + `tests/test_vault_ingest.py`.
- Heavy suite: 19 passed (command in the test docstring); light suite: 14 passed /
  5 skipped in the repo venv.
- Tier mapping audited end-to-end: originals/verbatim/formula-page-images/table
  structure → `vault/`; intermediates (wav, frames, non-flagged rasters) and
  approximate values → `derived/` (TencentDB tier path).

## Out of scope (unchanged)

VAD/no-speech gating (R&D follow-up), production VLM enable (Pat's spend approval),
HU-1839 sign-off. Whisper `no_speech_prob` is stored as metadata to make that
follow-up a pure build task.

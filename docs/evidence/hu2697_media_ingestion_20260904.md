# HU-2697 — Ingestion R&D: media-to-vault measurements, audio/image/video (measured)

Date: 2026-09-04 · Agent: R&D Lead · Parent: HU-2692 (PDF half: `hu2692_ingestion_extraction_20260904.md`).
Artifacts: `experiments/ingestion-media/` (scripts, ground truth, outputs incl. `dialog_atom.json`,
`wer_scores.json`, `image_embed.json`, `video_probe.json`). Samples are untracked media (LibriSpeech
test-clean subset, Big Buck Bunny clip).

## Question

What does CPU-only media ingestion into vaults actually cost and score, measured on real samples —
audio/dialog transcription, image embedding (and what the W1 local-ONNX lane is missing for images),
video decode/extract feasibility — before any quality thresholds are set (same measure-first bar as
the PDF half).

## Method

- **Audio**: faster-whisper 1.2.1 (CTranslate2, int8, `cpu_threads=8`, beam 5) on 32 utterances /
  172.3 s / 4 speakers from LibriSpeech **test-clean** (public domain). LibriSpeech transcripts are
  the caption ground truth. Models: `tiny.en`, `base.en`, `small.en`. Metric: word-level WER
  (lowercase, punctuation-stripped, Levenshtein). Costs: wall-clock per audio-second, model disk
  footprint (HF cache, symlink-deduped), load time.
- **Image**: fastembed 0.8.0 CPU/ONNX (same family as the W1 `local_onnx` lane) on the four 150 DPI
  page rasters already produced by the PDF-extraction pipeline (`experiments/ingestion-pdf/outputs/
  page_png/`). Models: `Qdrant/clip-ViT-B-32` text+vision pair (512-dim shared space) and
  `jinaai/jina-clip-v1` (768-dim unified text+image space, Apache-2.0). Metrics: ms/image, ms/query,
  model disk, top-1 retrieval hits for 4 text queries with a known expected page, image self-similarity.
- **Video**: ffmpeg 6.x on a 596.5 s Big Buck Bunny cut (CC-BY, Blender Foundation; archive.org
  derivative, 640×360/24 fps, AAC stereo). Measured: full-decode throughput, 1 fps frame sampling,
  audio-track extraction to 16 kHz mono WAV, and composition into the audio lane (whisper `tiny.en`).

## Results

### Audio/dialog (CPU, int8)

| Model | corpus WER ↓ | audio-sec per CPU-sec | disk | load |
|---|---|---|---|---|
| tiny.en | 0.097 | **7.9×** realtime | 156 MB | 0.7 s |
| base.en | 0.077 | 3.8× realtime | 296 MB | 0.4 s |
| small.en | **0.075** | 2.2× realtime | 972 MB | 0.9 s |

- Even `tiny.en` is faster than realtime on this box; the whole 172 s corpus transcribes in 22–80 s.
- `small.en` buys almost nothing over `base.en` here (+0.002 WER for 3.3× the disk and half the
  speed). `base.en` is the measured sweet spot for CPU ingestion; keep `small.en` only if accuracy
  on harder audio (real persona calls, not read speech) proves materially better.
- Artifact: `outputs/dialog_atom.json` — verbatim dialog atom with per-segment timestamps
  (start/end/text/avg_logprob) + provenance (model, compute, file WER vs reference). This is the
  vault-ready shape for persona dialog.

### Image (CPU ONNX)

| Model (lane) | dim | ms/image | ms/query | disk | top-1 | self-top1 |
|---|---|---|---|---|---|---|
| CLIP ViT-B/32 (text+vision pair) | 512 | 77 | 6.3 | 352 + 256 MB | 2/4 | 4/4 |
| jina-clip-v1 (unified) | 768 | 177 | 22.8 | 892 MB (shared) | 2/4 | 4/4 |

- **The W1 `local_onnx` lane cannot ingest images today.** The repo embedder
  (`src/huible/embeddings.py`) exposes only `TextEmbedding`/bge-small-en-v1.5 (384-dim, text-only).
  fastembed does ship an `ImageEmbedding` class, but it is a different class, model family, and dim
  (512/768 vs 384): image vectors cannot be written into the current memories embedding columns
  without a separate collection + a cross-modal model choice. That wiring is the missing piece.
- Measured retrieval on dense document pages is weak (2/4 top-1 for both models): CLIP-style
  embeddings are scene/photo-oriented; near-uniform text pages mostly differ by their OCR-able text,
  which image towers don't read. Where image embedding shines: photos, charts, diagrams as scenes.
- Consequence for vaults: **retrieval over scanned/document images should ride the extracted text
  (PDF half, 0.93–0.98 F1), with the image stored as the source-of-truth artifact**; image embeddings
  are an optional extra index, not the primary key into a persona/domain vault.

### Video (CPU)

| Probe | Result |
|---|---|
| full decode (h264 640×360) | 117× realtime (5.1 s for a 10-min file) |
| 1 fps frame sampling → jpg | 596 frames / 14.8 MB in 4.4 s |
| audio track → 16 kHz mono WAV | 19.1 MB in 3.0 s |
| extracted audio → whisper tiny.en | 3.2× realtime |

- Decode cost is **not** prohibitive on CPU: video ingestion is a composition of already-measured
  lanes (audio track → whisper; sampled frames → image lane), not a new capability. ~6.5 s of CPU
  preps a 10-minute video end to end (extraction only).
- **Failure mode found (vault-relevant)**: on BBB's music/sfx-only soundtrack, whisper returned
  confident, dialog-shaped hallucinations — "Thank you so much for watching, and I'll see you in the
  next video." at two unrelated offsets. Same class of silent-poison risk as docling's garbled OCR on
  low-res text (PDF half). A transcript must not enter a vault unverified on non-dialog audio; gate
  on `no_speech_prob`/VAD (not measured yet — follow-up), and/or require voice-activity evidence.

## Vault doctrine mapping (HU-1839 tiering)

| Item | Tier | Why |
|---|---|---|
| Original media file (flac/mp4/png) | **vault** | irreplaceable raw measurement; everything else derives from it |
| Verbatim transcript + segment timestamps | **vault** | the "specific": lossy decode of that exact audio, with provenance + WER context |
| Hallucination/voice-activity flags | **vault** (metadata) | guards against silent poison; cheap to keep |
| Frame extracts (jpg), 16 kHz WAV intermediates | regenerable | re-derivable from the original in seconds (measured above) |
| Embeddings (text/image) | regenerable (TencentDB tier) | recomputable deterministically from stored text/images |
| Normalized text (case/punct-stripped) | regenerable | pure function of the verbatim transcript |

## Recommended default pipeline (measured basis)

1. **Audio atom**: `base.en` (int8, 8 threads) → verbatim segments with timestamps + provenance;
   store atom + original audio in vault. Gate segments on VAD/no-speech evidence before trusting
   them on non-dialog audio (follow-up measurement).
2. **Image**: keep W1 bge text lane for text; do **not** route document-page images through CLIP for
   retrieval — store the image + extracted text instead. For photo-like media, fastembed
   `ImageEmbedding` (jina-clip-v1 unified space) is the CPU-viable option (177 ms/img, 892 MB);
   wiring it needs a separate collection + `Embedder` protocol extension (build work, not R&D).
3. **Video**: ffmpeg → audio track + 1 fps frames → reuse lanes 1–2. CPU cost trivial; no GPU needed.
4. **Do not** adopt heavier models (medium/large whisper, bigger CLIP) without re-measuring on real
   persona audio — read-speech WER gains didn't justify cost even at small.en.

## Requirements discipline (measure-first)

No thresholds were invented: WER 0.075–0.097, 2–8× realtime, 77–177 ms/image, 117× decode are the
baseline numbers. Before any production freeze, re-measure on Pat's real media (persona call audio,
real photos) — LibriSpeech is clean read speech; real-world audio will score worse.

## Limitations

- Audio: clean read speech only; no overlapping speakers, accents-under-noise, or call-quality audio;
  `no_speech_prob`/VAD gating not yet measured.
- Image: only 4 document-page images (deliberately the wrong case for CLIP — that gap is the finding);
  photo-style retrieval ranked only via the same 4-page set.
- Video: one 640×360 file; 1080p decode cost scales ~linearly and was not measured.
- fastembed's default cache resolved to the process temp dir during this run — production W1 must pin
  `FASTEMBED_CACHE_PATH` to the durable mounted cache (as the W1 deployment already does for bge).

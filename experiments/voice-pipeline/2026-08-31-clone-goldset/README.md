# Voice pipeline — clone-output gold set (2026-08-31, HU-2163)

Promotion step (a) from docs/IDENTITY_VOICE_PIPELINE.md §3: the speaker
gate threshold was calibrated on **natural** speech (natural held-out
clips as the zero-spend proxy for clones). Before any production persona
voice, the threshold must be re-proven on **actual clone outputs**. This
run builds that gold set with the zero-API-spend local adapter.

## Protocol

- **Voices**: the 4 LibriSpeech test-clean gold-set speakers
  (`2026-08-27-gold-calibration/gold-set/`, open licence, OpenSLR 12 —
  spkr-1089 / 1188 / 121 / 1221), 3 reference clips each. Conditioning
  clip = longest reference (mirrors `voice_clone._chatterbox_prompt_clip`
  most-speech policy).
- **Cloner**: `chatterbox-local` (self-hosted, MIT, Perth-watermarked,
  zero API spend), **both variants** — turbo
  (ResembleAI/chatterbox-turbo, 12 outputs: 4 spk × 3 texts) and std
  (ResembleAI/chatterbox, 8 outputs: 4 spk × 2 texts; controls
  exaggeration=0.5 / cfg_weight=0.5 actually applied, unlike turbo).
- **Texts**: same 5 sentences for every speaker/variant (narrative,
  question, exclamation, conversational, short mundane) — controls the
  text variable so score spread measures voice identity.
- **Driver**: `scripts/clone_goldset.py` (`gen` in `/root/venvs/voicegen`,
  `score` in the repo `.venv` — chatterbox-tts would downgrade the app
  stack's torch/starlette, so generation is venv-isolated).
- **Scoring**: production rule unchanged — ECAPA-TDNN 192-d
  (`ecapa-voxceleb-192d-cosine-max`), gate score = **max cosine vs every
  reference clip** (`voice_gate.py` rule). Positives = clone vs own
  speaker's refs; negatives = clone vs each other speaker's refs.

## Results — gate-equivalent (max-cosine) distributions

| Set | n | min | max | mean |
|---|---|---|---|---|
| positives (clone vs own refs) | 20 | **0.6865** | 0.8807 | 0.7924 |
| negatives (clone vs other-spk refs) | 60 | 0.0656 | **0.3766** | 0.1856 |

**Separated: YES — margin 0.31** (pos_min − neg_max). Per variant:

| Variant | pos n | pos_min | neg n | neg_max |
|---|---|---|---|---|
| turbo | 12 | 0.6865 | 36 | 0.3476 |
| std | 8 | 0.7624 | 24 | 0.3766 |

- **Clone-calibrated threshold: 0.5315** (midpoint) → `clone-gate-config.json`
  (`production_safe: false` by design — consented human gold set still
  required for client personas).
- **Natural-speech threshold 0.5049 (HU-2160) transfers to clones**:
  TPR 1.0 / FPR 0.0 on this set. The natural-speech calibration was not
  optimistic — real chatterbox clones score comfortably above it and all
  cross-speaker maxima sit far below.
- std carries slightly stronger identity than turbo (pos_min 0.7624 vs
  0.6865) and is the variant with working expression controls — consistent
  with the §Known-limits note. Generation latency: turbo 10–45 s/sentence,
  std 26–44 s/sentence (8-core CPU).

## Verdict

HU-2163 acceptance **met for the read-speech (LibriSpeech) class**:
threshold re-proven on n=20 actual clone outputs across 4 voices, both
chatterbox variants, 5 text domains. What this does NOT cover (honest
bounds):

- **Sitcom-class cloning is still unproven** — the MELD/Chandler gate
  remains fail-closed (`passed: false`, HU-2160), so no clone gold set
  exists for that domain; emotion-extreme delivery is the known residual.
- **ElevenLabs IVC is not measured** (spend rule: board decision on Pat's
  money — no spend happened in this run).
- Natural human delivery (consented recordings, ~50 clips/class) remains
  the separate production gate for client personas (§3 step b).

## Incident note (ops)

First std prefetch used `snapshot_download` (11 GB full repo) and filled
the disk to 100%, killing the first turbo run at 3/12 outputs. Remedied:
partial snapshot deleted, targeted `hf_hub_download` of the 5 files
`ChatterboxTTS.from_pretrained` actually loads (3.19 GB), gen made
resumable (skip-existing). Net disk cost after cleanup ≈ 3.2 GB.

## Contents

- `clones/` — 20 wavs + `.prov.json` sidecars (model+version, refs, text,
  latency, controls, watermark, HU-2163 experiment tag)
- `gold-pairs.jsonl` — all 240 (clone, reference) cosine pairs
- `gate-rows.jsonl` — 80 gate-equivalent rows (max-cosine per clone×speaker)
- `summary.json` — distributions, separation verdict, baseline transfer
- `clone-gate-config.json` — clone-calibrated gate config (threshold
  0.5315, `production_safe: false`)

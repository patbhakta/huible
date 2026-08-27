# Voice pipeline — gold calibration + acceptance (2026-08-27, HU-2151)

Zero-API-spend run: gate built, calibrated, and proven on free, open-licence
audio (LibriSpeech test-clean, OpenSLR 12) before any persona-voice spend.

## Contents

- `gold-set/spkr-{1089,1188,121,1221}/` — 4 speakers × (3 references + 5
  calibration held-out + 3 unseen acceptance clips), 16 kHz mono WAV.
- `gold-pairs.jsonl` — every calibration pair with per-reference cosines
  (32 pos / 96 neg).
- `summary.json` — distribution stats, threshold, TPR/FPR.
- `vault-spkr-1089/` — full R&D vault exercising every stage: intake
  (`references/voice-reference-set.json`), curated set + embeddings, gate
  config (`references/voice-gate-config.json`), gate log
  (`media/voice-gate-log.jsonl`, 12 rows), one labelled `dryrun-no-clone`
  registry record + verify.

## Results

| Metric | Value |
|---|---|
| Threshold | **0.8323** (midpoint pos_min 0.8403 / neg_max 0.8243) |
| TPR / FPR at threshold | **1.0 / 0.0** (32 pos, 96 neg, fully separated) |
| Acceptance: unseen same-speaker | **3/3 pass** (0.857, 0.9495, 0.9253) |
| Acceptance: cross-speaker | **6/6 reject** (0.586–0.794) |
| Spend guards | clone w/o `--allow-spend` → REFUSED; w/o API key → REFUSED; `generate_voice --persona` → REFUSED |

Gate: resemblyzer 256-d cosine-max vs curated set. `production_safe: false`
by design — promotion requires clone-output gold set + consented human gold
set (docs/IDENTITY_VOICE_PIPELINE.md §3).

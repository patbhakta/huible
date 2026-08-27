# Voice pipeline — MELD Chandler ingest + sitcom-speech gate recalibration (2026-08-28, HU-2159)

Follow-up to `2026-08-27-gold-calibration/` (HU-2151, LibriSpeech v1).
Result: **honestly not-separated** — resemblyzer does not cleanly separate
sitcom-speech speakers under the v1 protocol, and the LibriSpeech threshold
(0.8323) does not transfer (TPR 0.35 on same-speaker sitcom speech).

## Corpus (lawful Persona-0 benchmark set, internal-only)

- **Source**: official `MELD.Raw.tar.gz` (declare-lab/MELD HF mirror of the
  MELD official release). Verified sha256
  `a56b4407d574195cbce470d86f9c9d72fcfea59b0e34502ecd4babee4a5c613e`
  (matches HF x-linked-etag). Downloaded via 8 parallel ranged chunks,
  single streaming extract of only listed members; tarball not retained.
- **Speaker CSVs**: tarball copies cross-checked byte-identical vs
  `zrr1999/MELD_Text` (train 9989 / dev 1109 / test 2610 rows).
- **Selection**: main-cast utterances 4.0–9.5 s (CSV StartTime/EndTime),
  dialogue-spread — 192 clips (Chandler 87, Joey 35, Rachel 35, Ross 35),
  mp4 → 16 kHz mono WAV (ffmpeg). `manifest.json` = per-clip
  speaker/emotion/duration/text. Durable copy: `/root/.hermes/meld_raw/`
  (see its `PROVENANCE.md`).
- **Gold set** (`gold-set/`): chandler 8 references (6 train + 2 dev, 6
  emotions: neutral/joy/anger/disgust/surprise/fear) + 5 held-out + 3
  unseen (test split, never in calibration); joey/rachel/ross 3 refs + 5
  held-out each. All pre-checked VAD speech 3.5–9.5 s (`vad-report.json`).

## Pipeline exercise (all stages, zero API spend)

- **COLLECT**: `voice_collect.py --source benchmark_corpus --corpus-ref
  "MELD.Raw/chandler" --speaker-filter Chandler` → 8/8 accepted,
  `internal_only: true` (fail-closed: production cloning impossible on this
  set; internal benchmarking requires explicit `--allow-spend
  --benchmark-only`). Vault: `vault-chandler/`.
- **CURATE**: 8/8 clips curated + embeddings (VAD speech ≥ 3 s, no
  clipping/quiet/dupes).
- **CALIBRATE**: `calibrate_voice_gate.py --gold-dir gold-set` →
  `vault-chandler/references/voice-gate-config.json`, `passed: false`.

## Results (20 pos / 60 neg pairs, `gold-pairs.jsonl`)

| Metric | MELD sitcom speech | LibriSpeech v1 |
|---|---|---|
| pos_min / pos_mean | **0.7076 / 0.8108** | 0.8403 / 0.9301 |
| neg_max / neg_mean | **0.8967 / 0.7099** | 0.8243 / 0.6012 |
| Separated | **NO** (overlap 0.7076–0.8967) | yes (margin 0.016) |
| Threshold (midpoint) | 0.8022 | 0.8323 |
| TPR / FPR at own threshold | 0.60 / 0.067 | 1.0 / 0.0 |
| TPR / FPR at **0.8323** | **0.35 / 0.033** | 1.0 / 0.0 |

No threshold exists with TPR=1 & FPR=0 on this gold set.

### Failure structure

- **Joey↔Chandler confusion dominates**: 6 of the top 8 negatives are
  Joey-vs-Chandler pairs (max 0.8967) — two male leads with similar
  prosody/ register; a 256-d GE2E-style embedder trained on VoxCeleb-style
  audio does not split them on sitcom delivery.
- **Positive failures cluster on expressive delivery**: lowest positives
  are anger/surprise clips (rachel 0.7076, ross 0.760/0.776, chandler
  0.787). One anger clip (dia312_utt17) scores higher vs *Rachel's*
  references (0.8117) than vs its own (0.7872).
- **Not an emotion artifact alone**: neutral-emotion held-out subset
  (9 pos / 27 neg) still overlaps — pos_min 0.7076 vs neg_max 0.7992
  (`secondary-analyses.json`).

### Consequences

1. **Read-speech thresholds do not transfer to sitcom speech.** At the v1
   LibriSpeech threshold, 65% of genuine same-speaker Chandler-band clips
   would be rejected while ~1/30 cross-speaker clips pass.
2. **Gate stays fail-closed**: `voice_gate.py` refuses to gate on a
   non-passed config ("recalibrate before gating"); `voice_clone.py`
   refuses all four flag combinations (`spend-guard-refusals.log`).
   No registry records exist — un-gated assets are never registered.
3. **ElevenLabs spend remains off** — a fortiori: the benchmark gate did
   not pass, so the spend-rule precondition for a trial clone is NOT
   satisfiable on this set. Pat's spend call stays moot until a gate
   passes on this corpus. Zero-spend local alternative: xtts-local stub
   (docs/IDENTITY_VOICE_PIPELINE.md §3).
4. **Named remedy (docs §3, pre-existing)**: swap the embedder to
   speechbrain ECAPA-TDNN behind the same `embed_wav` interface and
   recalibrate — gate/config/registry/spend-rule code is unchanged. This
   experiment is the evidence that motivates it (follow-up issue material;
   Joey↔Chandler on expressive multi-speaker TV audio is the hard case).

## Contents

- `manifest.json`, `vad-report.json`, `selection.json` — corpus + selection.
- `gold-set/` — 40 WAVs (16 kHz mono): refs/heldout/unseen per speaker.
- `vault-chandler/` — full benchmark vault: reference set
  (`internal_only: true`), curated set + embeddings, gate config
  (`passed: false`).
- `gold-pairs.jsonl`, `summary.json`, `secondary-analyses.json` — all
  calibration pairs, stats, and the LibriSpeech-operating-point +
  neutral-subset analyses.
- `spend-guard-refusals.log` — four clone refusals + gate refusal.

# Voice pipeline — ECAPA-TDNN embedder recalibration (2026-08-31, HU-2160)

Executes the pre-named remedy from [HU-2159](../2026-08-28-meld-chandler/)
and docs/IDENTITY_VOICE_PIPELINE.md §3: swap the speaker embedder from
resemblyzer (256-d GE2E) to speechbrain **ECAPA-TDNN**
(`speechbrain/spkrec-ecapa-voxceleb`, 192-d) behind the same `embed_wav`
interface, then recalibrate both gold sets. Gate code, config schema,
registry, and spend rule are unchanged. Zero API spend (model weights only,
~85 MB, already cached).

## What changed (repo)

- `scripts/voicepipe_common.py` — `voice_encoder()`/`embed_wav()` now use
  ECAPA (`encode_batch`, 192-d). VAD-trim still uses resemblyzer's
  `preprocess_wav` (webrtcvad). New `GATE_ID = "ecapa-voxceleb-192d-cosine-max"`
  constant so configs/logs name their embedder.
- `scripts/calibrate_voice_gate.py`, `scripts/voice_gate.py` — gate label
  from `GATE_ID` (was hardcoded `resemblyzer-256d-cosine-max`).
- `scripts/voice_reembed.py` — new migration helper: re-embeds a vault's
  cached curated embeddings after an embedder swap (dimension guard
  included). Run on both benchmark vaults (8 + 3 clips).
- `scripts/requirements-voicepipe.txt` — `speechbrain>=1.0.0` pinned
  (commit 1337f77). Python 3.12 venv note: `setuptools<81` needed because
  `webrtcvad` imports `pkg_resources`.

## Results — MELD Chandler gold set (same 20 pos / 60 neg protocol)

| Metric | resemblyzer 256-d (08-28) | ECAPA 192-d (this run) |
|---|---|---|
| pos_min / pos_mean | 0.7076 / 0.8108 | **0.3238 / 0.5087** |
| neg_max / neg_mean | 0.8967 / 0.7099 | **0.3379 / 0.1552** |
| Separated | NO (overlap 0.189) | **NO (overlap 0.014)** |
| Threshold (midpoint) | 0.8022 | 0.3308 |
| TPR / FPR at own threshold | 0.60 / 0.067 | **0.95 / 0.017** |
| TPR at FPR=0 threshold | — | 0.95 (thr 0.3380) |
| Joey↔Chandler neg max | 0.8967 (6 of top 8) | **0.2365 (0 of top 8)** |
| Neutral-subset pos_min vs neg_max | 0.7076 vs 0.7992 (overlap) | **0.3813 vs 0.3208 (separated)** |

Honest verdict: **still not separated, so the MELD gate stays fail-closed**
(`passed: false`), but the failure collapsed from a 0.189-wide structural
overlap to a 0.014 band driven by exactly two expressive-delivery outlier
clips (`secondary-analyses.json`):

- lowest positive: rachel `dia109_utt8` (**sadness**, 0.3238)
- highest negative: ross `dia32_utt12` (**anger**) vs Chandler refs (0.3379)

The HU-2159 failure structure is gone: Joey↔Chandler confusion (resemblyzer's
dominant failure) is fully resolved by ECAPA, and the neutral-emotion subset
now separates cleanly. What remains is emotion-extreme delivery, consistent
with the v1 protocol note that promotion requires clone-output + consented
human gold sets.

## Results — LibriSpeech v1 gold set (regression check, 32 pos / 96 neg)

| Metric | resemblyzer 256-d (08-27) | ECAPA 192-d (this run) |
|---|---|---|
| pos_min / neg_max | 0.8403 / 0.8243 | **0.6648 / 0.3450** |
| Margin (pos_min − neg_max) | 0.0160 | **0.3198** |
| Separated / TPR / FPR | yes / 1.0 / 0.0 | **yes / 1.0 / 0.0** |
| Threshold | 0.8323 | 0.5049 |

Read-speech gating is dramatically stronger — the knife-edge 0.016 margin
becomes 0.32. The vault gate config (`vault-spkr-1089`) now records
`passed: true` at 0.5049 with the ECAPA label, curated embeddings re-embedded
(192-d), and an end-to-end gate smoke test passes a held-out same-speaker
clip (0.7737) and rejects a cross-speaker clip (0.1881).

## Consequences

1. **MELD benchmark gate: still fail-closed.** `voice_gate.py` refuses to
   gate on the Chandler vault config; spend-rule precondition for cloning on
   MELD remains unsatisfiable (unchanged from 08-28).
2. **LibriSpeech-class (read speech) gating: passed with wide margin.** The
   v1 vault is a valid internal gate under the same zero-spend protocol.
3. **Embedder is now ECAPA everywhere.** Any config calibrated before
   2026-08-31 with the resemblyzer label is obsolete; both benchmark vaults
   carry fresh 192-d caches + ECAPA-labeled configs (old configs snapshotted
   in `meld/` and `librispeech/` as `voice-gate-config.resemblyzer-256d.json`).
4. **Next lever for sitcom speech** (if pursued): the two outlier clips
   suggest emotion-conditioned calibration or anger/sadness-stratified
   reference sets rather than another embedder swap — ECAPA already fixes
   the speaker-confusion structure.

## Contents

- `meld/` — ECAPA calibration evidence: `gold-pairs.jsonl`, `summary.json`
  (appended), `secondary-analyses.json`, resemblyzer config snapshot.
- `librispeech/` — same for the LibriSpeech v1 gold set regression run.

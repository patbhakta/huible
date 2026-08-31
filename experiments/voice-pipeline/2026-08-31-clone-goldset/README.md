# Voice pipeline — clone-output gold set (2026-08-31, HU-2163)

Closes promotion step (a) of docs/IDENTITY_VOICE_PIPELINE.md §3: the
speaker gate threshold must be **re-proven on actual clone outputs**, not
just natural speech, before any production persona voice. Driver:
`scripts/clone_goldset.py` (gen runs in the `voicegen` venv with
chatterbox-tts; score runs in the repo `.venv` with speechbrain).

## Protocol

- **Clones:** 20 outputs from the zero-API-spend local adapter
  `chatterbox-local` — 12 turbo (3 texts × 4 speakers) + 8 std
  (2 texts × 4 speakers) — conditioned on the longest reference clip of
  each LibriSpeech gold-set speaker (open licence, OpenSLR 12; the
  internal_only MELD/Chandler set is untouched). Five fixed texts across
  domains (narrative / conversational / question / exclamation / short
  mundane line) so score spread measures voice identity, not content.
  Perth watermark implicit in every output; per-clone provenance in
  `clones/*.wav.prov.json` (variant, refs used, latency, controls,
  experiment label).
- **Scoring:** production rule unchanged — speechbrain ECAPA-TDNN 192-d,
  VAD-trim, cosine vs every reference, **max** = gate score.
  Positives = (clone, own-speaker refs) max-cosine; negatives = (clone,
  each other speaker's refs) max-cosine. 240 raw pairs in
  `gold-pairs.jsonl`; 80 gate-equivalent rows in `gate-rows.jsonl`.

## Result — SEPARATED (read-speech domain, chatterbox turbo+std)

| Distribution | n | min | max | mean |
|---|---|---|---|---|
| pos (clone vs own refs) | 20 | **0.6865** | 0.8807 | 0.7924 |
| neg (clone vs other spk) | 60 | 0.0656 | **0.3766** | 0.1856 |

- pos_min − neg_max = **0.3099** → clone-calibrated threshold **0.5315**
  (`clone-gate-config.json`, `production_safe: false` by design).
- **The natural-speech threshold transfers:** at the 2026-08-31 ECAPA
  LibriSpeech threshold 0.5049 → **TPR 1.0 / FPR 0.0** on all 20 clones
  (both variants). The v1 proxy assumption (held-out same-speaker speech ≈
  faithful clone) holds for read-speech references under ECAPA.
- Latency (8-core CPU): turbo 22–45 s/sentence, std 26–51 s/sentence.

## Honest scope

- Negatives are other **read-speech** LibriSpeech speakers — the
  emotion-extreme / expressive-delivery failure mode (MELD residuals,
  HU-2160) is **not** exercised here. Clone-output evidence for the
  sitcom/expressive domain remains open until a consented human gold set
  exists (promotion step (b)); `elevenlabs-ivc` clone-output evidence is
  separately still unmeasured (spend-gated).
- Artifacts are experiment-only (never persona vault assets, never the
  registry); zero API spend throughout.

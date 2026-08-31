# Reference-Grounded Persona Voice Pipeline

**Issue:** HU-2151 · **Status:** R&D design + zero-spend gate calibration v1 (2026-08-27)
**Directive (Pat, Aug 27):** the Gemini prebuilt-voice Chandler sample "doesn't
sound anything like Chandler — why spend money on slop." Correct: a prebuilt
voice can never be the person. Persona voice must be **cloned from consented
reference audio of the actual person**, then validated quantitatively.

**Principle:** every persona voice asset is derived from a **curated,
consented reference set** owned by the client — never from a prebuilt voice
name and never from prompt text. Identity comes from reference audio; the
text only changes what is said.

Twin of the image pipeline (HU-2150, `docs/IDENTITY_IMAGE_PIPELINE.md`) —
same five-stage shape, same Hume-gate calibration pattern, same append-only
registry discipline.

---

## Stage overview

```
COLLECT → CURATE → CLONE → VALIDATE → REGISTRY
(intake:   (dedupe,  (reference- (speaker-     (append-only
 consent   quality,  audio       similarity    provenance
 tiers)    rights)    cloning)    gate)         per asset)
```

Stage tools live in `scripts/`:

| Stage | Tool | Model basis |
|---|---|---|
| COLLECT | `voice_collect.py` | filesystem + rights JSON schema |
| CURATE | `voice_curate.py` | sha256 + waveform-signature dedupe, ffmpeg decode, webrtcvad speech trim |
| CLONE | `voice_clone.py` | `elevenlabs-ivc` (Instant Voice Cloning, spend-gated) · `xtts-local` / `openvoice-local` (zero-spend, documented) |
| VALIDATE | `voice_gate.py` | ECAPA-TDNN (speechbrain) 192-d speaker embedding, cosine vs curated set |
| CALIBRATE | `calibrate_voice_gate.py` | gold-set distributions → threshold + TPR/FPR (Hume-gate pattern) |
| REGISTRY | `voice_registry.py` | flat append-only JSONL |

Requires `torch` (CPU wheel), `speechbrain`, `resemblyzer` (VAD trim only),
system `ffmpeg` (`scripts/requirements-voicepipe.txt`). ECAPA weights
(`spkrec-ecapa-voxceleb`, ~85 MB) download once from HF Hub and cache;
CPU-only embed is ≈ 0.5–2 s per clip. Python 3.12 venvs need
`setuptools<81` (webrtcvad imports `pkg_resources`).

---

## 1. SOURCE — consented reference audio (COLLECT)

Reference audio enters through explicit doors only
(`voice_collect.py --persona-root <vault> --audio … --source <tier>`):

| Tier | What | Rights basis (fail-closed) |
|---|---|---|
| `onboarding_recording` | loved one recorded live during onboarding (primary) | `consent_by` (who consented, at onboarding) |
| `family_archive` | existing family recordings (fallback 1) | `consent_by` **and** `provided_by` |
| `voicemail` | saved voicemails (fallback 2) | `consent_by` **and** `provided_by` |
| `licensed` | licensed audio | `license_ref` |
| `benchmark_corpus` | sitcom-walled corpus (friends-v2 / MELD) — **Persona-0 only** | `corpus_ref` + `internal_only: true` |
| `synthetic_seed` | rights-clean synthetic speakers | R&D calibration only |

A clip with an incomplete rights record is **rejected at intake** (fail
closed) — no rights record, no reference set. Intake writes
`references/voice-reference-set.json` (one record per clip: `clip_id`,
sha256, duration, source, rights, timestamps).

**Fallback tiers carry the same bar as primary:** a voicemail or family
recording enters only with explicit rights capture (who provided it, on
what basis the person consented). Convenience never substitutes for the
record.

**Persona-0 (Chandler) is special-cased by design:** the lawful reference
set is friends-v2 / MELD audio ingested as `benchmark_corpus`. That basis
sets `internal_only: true` on the whole reference set, which (a) marks the
gate config `internal_only`, (b) makes `voice_clone.py` refuse production
cloning on that set forever, allowing at most internal benchmarking with
the explicit dual override `--allow-spend --benchmark-only` (still
fail-closed without the API key) — no client-facing asset can ever derive
from sitcom audio. Production Chandler-persona voice is not a thing; the
benchmark set exists to measure whether the pipeline clones *well*, not to
ship Chandler audio.

**Reference-set shape (v1 guidance):** ≥ 3 clips, ≥ 3 s speech each, 8–20
diverse clips (emotion, tempo, distance from mic) is the production target.
Curate enforces the floor; more and varied references measurably stabilize
the embedding.

## 2. CLONE — reference-audio cloning (no prebuilt voices)

`voice_clone.py --persona-root <vault> --adapter <a> --text "…" --out …`
conditions the voice on the **curated reference clips themselves**, not on
a voice name or prompt description:

- **`elevenlabs-ivc` (production path).** Instant Voice Cloning: upload the
  curated reference clips once (`voices/add`), then TTS with the cloned
  `voice_id` (`eleven_multilingual_v2`). Provenance sidecar per generation.
- **`chatterbox-local` (zero-API-spend local path, MEASURED Aug 27).**
  Chatterbox TTS (Resemble AI, code MIT). Self-hosted — the reference audio
  (grief recordings) never leaves our boundary; no upload step, no
  clone-creation step: every generation is conditioned on one curated
  reference clip (`audio_prompt_path`). Variants in `chatterbox-tts` 0.1.7:
  `std` (ResembleAI/chatterbox, 500M) and `turbo`
  (ResembleAI/chatterbox-turbo, 350M); note turbo accepts but **ignores**
  `exaggeration`/`cfg_weight` (runtime warning, 0.1.7) — provenance records
  `controls_applied: false` accordingly. Every output carries Resemble's
  built-in Perth watermark. Install:
  `uv pip install chatterbox-tts` (+ torch/torchaudio; pulls torch 2.6.0).
  **Measured smoke (vault spkr-1089, open-licence LibriSpeech gold set):**
  turbo on 8-core CPU, 47.9 s for one sentence, gate score **0.9263** vs
  threshold 0.8323 → **PASS** and registered (first gate-passed clone
  output, zero API spend). internal_only sets still require
  `--benchmark-only`.
- **`xtts-local` / `openvoice-local` (zero-API-spend R&D path).** Local
  cloning adapters — XTTS v2 (CPML licence — benchmarking only, no
  commercial use) and OpenVoice v2 (MIT). Documented stubs in v1: the
  adapter interface, spend rule, and provenance hooks exist; the model
  installs are follow-ups only if local cloning is wanted for benchmarking
  without API spend.
- **No prebuilt mode exists in this tool by design.** The old prebuilt path
  (`generate_voice.py`, Gemini TTS) now refuses `--persona` outright:
  prebuilt voices may only serve **non-persona** uses (e.g. OpenMAIC course
  narration).

**Spend rule (enforced in code, docs §5):** `voice_clone.py --adapter
elevenlabs-ivc` refuses unless (a) `references/voice-gate-config.json`
exists and `passed: true`, (b) the set is not `internal_only`, (c)
`--allow-spend` was passed explicitly, (d) `ELEVENLABS_API_KEY` is set.
Verified fail-closed in the 2026-08-27 run (see Acceptance below).

Every generation writes `<out>.prov.json`: adapter, model + version,
`voice_id`, references used, text, latency, bytes, timestamp — so the
registry never depends on memory of invocation flags.

## 3. VALIDATE — speaker-similarity gate (Hume-gate pattern)

`voice_gate.py --persona-root <vault> --audio out.wav`:

1. ffmpeg decode → 16 kHz mono → webrtcvad speech trim → ECAPA-TDNN
   192-d speaker embedding.
2. Cosine similarity vs **every** curated reference embedding; gate score =
   **max** similarity (best-matching clip — same speaker if *any* reference
   matches).
3. Score ≥ threshold (from `references/voice-gate-config.json`) → pass;
   else reject. Every check — pass or reject — appends the full score
   vector to `media/voice-gate-log.jsonl`.
4. On reject: caller regenerates/retries (retries are new gate rows).
   Systematic rejection is a curation smell (bad reference set), not a gate
   bug.

**Threshold calibration — measure before production, never guess.**
`calibrate_voice_gate.py` builds the gold set:

- **Positives** = (reference set, held-out same-speaker clip). A faithful
  clone *is* the same speaker, so same-speaker held-out speech is the
  zero-spend proxy for clone-vs-reference similarity. (Cloning spend is
  forbidden until the gate exists, so v1 cannot use real clone outputs —
  the image twin could, image generation was already live. This is the one
  deliberate protocol difference.)
- **Negatives** = (reference set, clip of a different speaker), same
  corpus/genre — the worst case, read-speech voices sound alike.
- Writes `voice-gate-config.json`: threshold = midpoint(pos_min, neg_max)
  when separated, plus both distributions, TPR/FPR, and protocol metadata.
  `production_safe` is **false by design**.

**v1 gold set (2026-08-27, this repo):** 4 LibriSpeech test-clean speakers
(open licence, OpenSLR 12) × (3 reference + 5 held-out) → **32 positive /
96 negative pairs, fully separated**: pos_min 0.8403 vs neg_max 0.8243,
pos_mean 0.9301 vs neg_mean 0.6012 → **threshold 0.8323, TPR 1.0, FPR 0.0**.
Evidence: `experiments/voice-pipeline/2026-08-27-gold-calibration/`.

**Sitcom recalibration (2026-08-28, HU-2159): NOT separated.** MELD.Raw
Chandler gold set (official release, sha256-verified; 4 speakers, 8 Chandler
refs across 6 emotions, 20 pos / 60 neg): pos_min 0.7076 vs neg_max 0.8967
— no threshold reaches TPR=1/FPR=0; midpoint 0.8022 gives TPR 0.60 / FPR
0.067; the LibriSpeech 0.8323 transfers at TPR 0.35 / FPR 0.033. Dominant
confusion: Joey↔Chandler (similar male-leads prosody); positive failures
cluster on anger/surprise delivery; neutral-only subsets still overlap. The
Chandler vault gate config exists but `passed: false` (fail-closed: no
gating, no spend, no registry). Read-speech thresholds do **not** transfer
to sitcom speech. Evidence:
`experiments/voice-pipeline/2026-08-28-meld-chandler/`.

**ECAPA recalibration (2026-08-31, HU-2160): sitcom still not separated,
read speech much stronger.** Embedder swapped to speechbrain ECAPA-TDNN
(192-d) behind the same `embed_wav` interface; both gold sets recalibrated.
MELD: pos_min 0.3238 vs neg_max 0.3379 — overlap collapses from 0.189 to
0.014, TPR 0.95 / FPR 0.017 at midpoint 0.3308, Joey↔Chandler confusion
**gone** (pair max 0.2365; was 0.8967 and 6 of the top 8 negatives), and
the neutral-emotion subset now separates (0.3813 vs 0.3208). The two
residual outliers are emotion-extreme clips (rachel/sadness positive at
0.3238; ross/anger negative at 0.3379), so the Chandler gate stays
`passed: false` (fail-closed). LibriSpeech regression: cleanly separated —
pos_min 0.6648 vs neg_max 0.3450, **margin 0.32 (was 0.016)**, threshold
0.5049, TPR 1.0 / FPR 0.0; vault re-embedded and gate smoke-tested
(same-speaker 0.7737 pass, cross-speaker 0.1881 reject). Evidence:
`experiments/voice-pipeline/2026-08-31-ecapa-recalibration/`.

**Clone-output gold set (2026-08-31, HU-2163): SEPARATED — promotion step
(a) closed for read-speech + chatterbox.** 20 clones (chatterbox-local:
12 turbo + 8 std, 5 fixed texts × 4 LibriSpeech speakers, conditioned on
the longest reference each; zero API spend) gated with the production rule:
pos_min **0.6865** vs neg_max **0.3766** (margin 0.31), clone-calibrated
threshold **0.5315**, and the natural-speech 0.5049 transfers at **TPR 1.0
/ FPR 0.0** on all 20 clones. Scope: negatives are other read-speech
speakers, so the emotion-extreme failure mode is not exercised; expressive
-domain and `elevenlabs-ivc` clone evidence remain open. Evidence:
`experiments/voice-pipeline/2026-08-31-clone-goldset/`
(driver `scripts/clone_goldset.py`).

**Production promotion requires** (in order): (a) ~~clone-output gold set~~
**DONE for read-speech + chatterbox** (above) — still open for
expressive/sitcom delivery and for `elevenlabs-ivc`; (b) for client
personas, a **consented human gold set (~50 clips/class)** built from
onboarding-style recordings; (c) re-run calibration, threshold from
*measured* clone distribution. The ECAPA swap (2026-08-31) widened the
read-speech margin from ≈0.016 to ≈0.32, so the embedder is no longer the
weak link; the remaining limits are corpus-domain transfer and
emotion-extreme delivery (§Known limits).

## 4. PROVENANCE — flat append-only registry

Every **gated-pass** voice asset gets one line in
`<vault>/media/voice-registry.jsonl` (`voice_registry.py append`; JSONL
only — no graph stores, no DB dependency):

```json
{
  "asset": "media/voice/2026-08-27T22-30-00_line1.wav",
  "sha256": "…",
  "model": "elevenlabs-ivc",
  "model_version": "eleven_multilingual_v2",
  "references_used": ["vaudio_20260827_0b1785", "…"],
  "text": "…",
  "gate": {"score": 0.87, "threshold": 0.8323, "per_ref": {"…": 0.87}, "passed": true},
  "generated_at": "2026-08-27T22:30:00Z",
  "registered_at": "2026-08-27T22:30:04Z"
}
```

Append-only, never rewritten (tamper = visible break). Rejected generations
live in `media/voice-gate-log.jsonl`, never in the registry. `verify`
checks duplicate (asset, sha256) pairs and schema completeness.

## 5. SPEND RULE — zero persona-voice API spend until the gate passes

- Enforced **in code** at the only spend door (`voice_clone.py` §2): gate
  config must exist and pass, set must not be `internal_only`, spend must
  be explicitly allowed, key must be present. All four refusal paths
  verified in the 2026-08-27 run.
- Enforced at the **legacy door** (`generate_voice.py --persona` refuses).
- Enforced at the **flow door** (`flows/vault-media.yaml` stays
  paused-for-persona with the required ack guard until this pipeline is
  productionized in Kestra).
- Prebuilt voices remain allowed **only** for non-persona uses (OpenMAIC
  course narration etc.).

Gate status after this issue: **exists and passes on R&D gold** → the
spend-rule precondition for a *trial* clone run is now technically
satisfiable, but spend itself remains a board decision (Pat's money call),
and any spend-gated run on Persona-0 stays benchmark-only because the
Chandler set is `internal_only`.

---

## Acceptance protocol (measured, not eyeballed)

For one curated reference set, gate 3 **unseen** clips (never used in
calibration) → all must pass; gate 3+ clips of **other speakers** → all
must reject. Record gate score vectors + registry rows + configs under
`experiments/voice-pipeline/`.

**2026-08-27 run:** vault-spkr-1089 — 3/3 unseen pass (0.857 / 0.9495 /
0.9253 ≫ 0.8323); 6/6 cross-speaker reject (0.586–0.794 < 0.8323);
spend-guard refusals verified; registry append + verify OK (1 clearly
labelled `dryrun-no-clone` record — natural held-out clip exercising the
gate→registry path, zero generation).

## Known limits / next steps

- **Chandler benchmark set exists (MELD.Raw ingested, HU-2159) and the
  sitcom gate is still honestly not-separated after the ECAPA swap
  (2026-08-31)** — but the failure collapsed to two emotion-extreme outlier
  clips (overlap 0.014; was 0.189 with resemblyzer). Joey↔Chandler
  confusion and the neutral-subset overlap are fixed. Production-path
  implication unchanged (set is internal_only forever); benchmark-path
  implication: the next lever is emotion-stratified calibration or
  reference sets, not another embedder swap.
- **Clone-output evidence: closed for read-speech + chatterbox, open for
  expressive domains and `elevenlabs-ivc`.** The 2026-08-31 clone gold set
  (HU-2163) proves the natural-speech ECAPA threshold on actual chatterbox
  clone outputs (TPR 1.0 / FPR 0.0 at 0.5049); it does **not** exercise
  emotion-extreme delivery (MELD residuals) nor the spend-gated
  `elevenlabs-ivc` adapter. Consented human gold set (~50 clips/class)
  required before client personas.
- **Read-speech ↔ sitcom transfer fails (resemblyzer v1: 0.8323 → TPR 0.35
  on MELD; ECAPA thresholds are corpus-domain-specific too — LibriSpeech
  0.5049 vs MELD midpoint 0.3308 in different embedding spaces)** —
  recalibrate per domain; never reuse a threshold across embedders or
  corpora (gate configs carry `gate` labels for exactly this).
- **ECAPA swap DONE (2026-08-31, HU-2160)** — speechbrain ECAPA-TDNN
  behind `embed_wav`, vaults re-embedded via `scripts/voice_reembed.py`,
  both gold sets recalibrated (see §3 evidence). Old resemblyzer-labeled
  configs are obsolete.
- **Local clone adapters: chatterbox-local is real and measured; XTTS /
  OpenVoice remain documented stubs** — chatterbox-turbo gate-passed on the
  spkr-1089 gold set (0.9263 ≥ 0.8323, CPU 47.9 s/sentence, zero spend);
  XTTS CPML is benchmarking-only, no commercial use. The n≈20 clone-output
  gold set now exists for turbo+std on read speech (HU-2163, §3);
  expressive-domain and `elevenlabs-ivc` clone evidence are the remaining
  gaps.
- **Chatterbox-turbo ignores expression controls (0.1.7)** — provenance
  logs `controls_applied: false`; if expression control matters for
  persona tuning, `std` is the variant to benchmark next (500M, slower).
- **Kestra productionization** (vault-media flow replacement: collect →
  curate → clone → gate → register) is a follow-up once the gate config is
  promoted from R&D to production.
- **Cost/latency (ElevenLabs IVC, when authorized):** IVC creation ≈ $0
  one-off + Creator-tier plan; TTS ≈ $0.10–0.30/min by model. Gate +
  registry are local and free.

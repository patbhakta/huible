# Runbook: Identity gate gold-set collection & production promotion

**Issue:** HU-2157 (item 2) · **Design:** `docs/IDENTITY_IMAGE_PIPELINE.md`
**Status:** ready — blocked only on consented human photos (founder decision card `d9121d4b` on HU-2157)

## Purpose

Promote the identity reference gate from `production_safe=false` (synthetic-seed
calibration only) to `production_safe=true` using a **human-subject gold set**,
same Hume-gate pattern that gated the labeler: accuracy measured first,
threshold set from measured separation. The gate stays fail-closed until this
runbook completes without `--force`.

## Prerequisites

- Consented human photos with a documented rights basis (`consented_upload` or
  `licensed`). Actor stills / scraped social media of third parties are **not**
  permitted (rights directive, Aug 27). For Persona-0 (Chandler): client
  uploads only, attestation `client:chandler-bing:onboarding`.
- ≥8 distinct consenting identities in the gold set (negatives require
  different-person pairs; one identity alone cannot calibrate).
- Face tooling installed (`scripts/requirements-refpipe.txt`; buffalo_l weights
  cache to `~/.insightface/models/`, CPU is sufficient).
- **Privacy:** real-human reference photos must NOT be committed to git. Run
  the gold dir on disk (or inside the persona vault); commit only the score
  JSON / gate config (no images).

## Steps

### 1. Intake (one pass per consenting identity)

```bash
python3 scripts/onboarding_ref_intake.py \
  --persona-root <vault> \
  --upload-dir <photos-dir> \
  --source consented_upload \
  --consent-by "client:<persona>:onboarding" \
  --notes "gold-set contribution"
```

3–10 diverse angles per identity. Intake rejects sub-resolution images
(`--min-edge`, default from schema) and records the attestation per photo.

### 2. Curate

```bash
python3 scripts/ref_curate.py --persona-root <vault>
```

Drops duplicates/near-duplicates and multi-face images (identity ambiguity).

### 3. Generate scene-varying outputs per identity (R&D mode)

The gate is not yet production-safe, so gold-set generation uses the documented
R&D exception — outputs are calibration material only, never delivered:

```bash
python3 scripts/ref_generate_validated.py \
  --persona-root <vault> \
  --ref-photo-id <ref_id> \
  --prompt "same person, <scene>" \
  --allow-unsafe-gate --retry 1
```

≥8 scene-varying prompts per identity (indoor/outdoor, angles, distance).

### 4. Lay out the gold dir

```
<gold_dir>/<identity>/reference.png     curated reference (copy, do not move)
<gold_dir>/<identity>/outputs/*.png     generations from that reference
```

Negatives are formed cross-identity automatically (reference_i vs outputs of
j≠i). Expected yield at 8 identities × 8 outputs: ~64 positive / ~448 negative
pairs.

### 5. Calibrate — NO `--force`

```bash
python3 scripts/calibrate_gate.py \
  --persona-root <vault> \
  --gold-dir <gold_dir> \
  --min-pos 50 --min-neg 50
```

Writes `<vault>/references/gate-config.json`. `production_safe` becomes `true`
**only** when the distributions are fully separated AND both pair counts meet
the minimums. `--force` writes the config regardless — forbidden for
production promotion.

### 6. Verify & record evidence

- `gate-config.json`: check `threshold`, `separated: true`,
  `production_safe: true`, `tpr_at_threshold` / `fpr_at_threshold`.
- Spot-check: `python3 scripts/ref_gate.py --persona-root <vault> --images "<vault>/media/images/*.png" --json`
- Copy `gate-config.json` + `calibration-pairs.json` (scores only, no images)
  to `experiments/identity-pipeline/<date>-human-gold-calibration/` and commit.

## Safety / rollback

- If separation fails or counts are short: gate stays `production_safe=false`;
  the Kestra flow `huible/huible-vault-media` and
  `ref_generate_validated.py` remain fail-closed. Do not lower `--min-*` to
  force a pass; collect more photos instead.
- To re-close a promoted gate (incident): set `production_safe: false` in
  `gate-config.json` and redeploy — generation halts fail-closed everywhere.
- Text-to-image for persona assets stays blocked regardless of gate state
  (identity must come from references, never bare prompts).

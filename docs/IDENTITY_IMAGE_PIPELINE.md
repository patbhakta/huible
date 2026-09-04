# Reference-Grounded Identity Image Pipeline

**Issue:** HU-2150 (R&D) · HU-2157 (productionization) · **Status:** onboarding intake + guarded generation runner live (2026-08-31); human gold-set promotion pending consented photos
**Directive (Pat, Aug 27):** clients' loved ones are not celebrities — there is no
photo coverage of them online to harvest. Text-to-image guessing produces
ungrounded "AI slop" (bad entropy). We need our own pipeline to clean, validate,
and *repeatably* generate images of the same real person.

**Principle:** every generated persona image is derived from a **curated,
consented reference set** owned by the client — never from a bare prompt.
Identity comes from references; the prompt only changes scene/pose/context.

---

## Stage overview

```
COLLECT → CURATE → CONDITION → VALIDATE → REGISTRY
(intake)  (dedupe,  (identity-   (quantitative  (append-only
(schema)   quality,  conditioned  ArcFace gate,  provenance
           rights)   generation)  logged scores)  per asset)
```

Stage tools live in `scripts/` (git, same place as `generate_image.py`):

| Stage | Tool | Model basis |
|---|---|---|
| COLLECT | `ref_collect.py` | filesystem + JSON schema |
| CURATE | `ref_curate.py` | perceptual hash + InsightFace `buffalo_l` (SCRFD det + ArcFace w600k_r50 512-d emb + pose) |
| CONDITION | `generate_image.py --ref-image` | FAL `fal-ai/flux-pro/kontext` (queue API, same client as before) |
| VALIDATE | `ref_gate.py` | ArcFace cosine similarity vs curated reference set, threshold from `gate-config.json` |
| CALIBRATE | `calibrate_gate.py` | gold-set score distributions → threshold + metrics (Hume-gate pattern) |
| REGISTRY | `ref_registry.py` (used by gate/generation) | flat append-only JSONL |

Face tooling requires `insightface`, `onnxruntime`, `opencv-python-headless`,
`pillow`, `imagehash` (`scripts/requirements-refpipe.txt`). Model weights
(buffalo_l, ~280 MB) cache to `~/.insightface/models/` on first use; CPU-only is
sufficient (det+embed ≈ 1–2 s/image on the current box).

---

## 1. COLLECT — reference photo intake

References enter through exactly two doors:

1. **Consented uploads (primary).** Client/loved-one photos uploaded during
   onboarding. The uploader attests to the relationship/consent at intake.
2. **Licensed / publicly-available imagery (secondary).** Only where rights
   permit (client-owned licensed collections, genuinely licensed stock). Social
   media scrapes of third parties are **not** permitted absent documented
   permission. *Friends* stills / actor likenesses are **not** rights-permitted
   for Persona-0 — production Chandler references must come from consented
   client uploads or licensed sources.

`ref_collect.py --persona-root <vault>` copies intake photos into
`<vault>/references/raw/` and appends/creates **`reference-set.json`** — one
record per photo:

```json
{
  "photo_id": "ref_20260827_ab12cd",       // stable, content-derived suffix
  "path": "references/raw/ref_20260827_ab12cd.jpg",
  "sha256": "…",
  "source": "consented_upload | licensed | synthetic_seed",   // synthetic_seed = R&D only
  "rights": {
    "basis": "client_upload | license | synthetic",
    "license_ref": null,                   // required when basis=license
    "consent_by": "client:chandler-bing:onboarding",  // required when basis=client_upload
    "expires": null
  },
  "collected_at": "2026-08-27T16:00:00Z",
  "notes": "optional"
}
```

A photo with no rights record is **rejected at intake** (fail closed). No rights
record → never enters a reference set.

**Onboarding door (HU-2157):** `scripts/onboarding_ref_intake.py` is the
one-command operator path over a consented upload folder — it runs COLLECT
(with the consent attestation) + CURATE and writes
`references/intake-report.json`: what was collected, what was kept, every
rejection with its reason, curated-set size vs the 3–10 target, and the gate
promotion status. Refuses to run without `--consent-by` (or `--license-ref`
for licensed imagery).

```bash
python3 scripts/onboarding_ref_intake.py \
  --persona-root /root/repos/personas/<p> \
  --upload-dir /path/to/consented-uploads \
  --consent-by "client:<persona>:onboarding"
```

## 2. CURATE — dedupe, quality, rights

`ref_curate.py --persona-root <vault> [--min-edge 512]` reads every record in
`reference-set.json`, then:

1. **Dedupe** — `imagehash.phash` (hash distance ≤ 8 → duplicate; the
   higher-resolution / higher-quality-score copy wins). Exact sha256 dupes also
   dropped.
2. **Quality filter** — InsightFace on each photo:
   - ≥ 1 face detected; largest face box ≥ 15 % of image area;
   - face width ≥ 160 px (enough pixels for a stable embedding);
   - `|yaw| ≤ 45°`, `|pitch| ≤ 30°` (usable pose, not profile/back-of-head);
   - min image edge ≥ `--min-edge` (default 512).
3. **Rights check** — records failing the §1 rights schema are dropped with
   reason `rights_incomplete`.

Output: **`references/curated.jsonl`** (append-only) — one line per accepted
photo: `{photo_id, path, quality: {face_area_frac, yaw, pitch, w_px}, phash,
kept: true, reasons: []}`. Rejected photos are recorded in
`references/curation-log.jsonl` with reasons (audit trail, never deleted).
Curated set ≥ 1 photo; 3–10 diverse-angle photos is the recommended production
target. Curated embeddings (ArcFace, 512-d) are cached in
`references/embeddings.json` so the gate does not re-run the model per check.

## 3. CONDITION — identity-conditioned generation

`generate_image.py` (extended, not replaced — old text-to-image CLI unchanged)
gains:

```
python3 generate_image.py \
  --prompt "…" --ref-image <path> --out <path> \
  [--aspect square] [--strength 1.0] [--model flux-pro-kontext]
```

- Endpoint: `https://queue.fal.run/fal-ai/flux-pro/kontext` (queue +
  poll + fetch, same client code path as flux/schnell; `--model` selects among
  `flux-pro-kontext`, `flux-kontext-dev`, and the legacy `flux-schnell`
  text-to-image default).
- The reference image is sent as the Kontext `image_url` input (base64 data
  URI); the prompt describes only the *change* ("same person, now hiking in
  running clothes on a mountain trail at sunrise").
- `--ref-image` + legacy `flux-schnell` model is refused: text-to-image for
  persona assets is exactly the slop path we are replacing (schnell remains
  available for rights-clean synthetic-seed identities in R&D experiments).

Batch provenance: each successful generation also writes a sidecar
`<out>.prov.json` (model id, endpoint, ref sha256, prompt, latency, bytes) so
the registry never depends on memory of invocation flags.

## 4. VALIDATE — quantitative identity gate

**Production runner (HU-2157):** `scripts/ref_generate_validated.py` chains
CONDITION → VALIDATE → REGISTRY for one output, with gate retries
(`--retry N`, each retry is a fresh Kontext generation and a new gate row),
and **refuses to generate at all** unless a curated set exists AND
`gate-config.json` says `production_safe=true` (override for R&D synthetic
seeds only: `--allow-unsafe-gate`). Picks the best-quality curated reference
by default (`--ref-photo-id` to pin one).

```bash
python3 scripts/ref_generate_validated.py \
  --persona-root /root/repos/personas/<p> \
  --prompt "same person, hiking in running clothes on a mountain trail at sunrise" \
  --retry 2
```

`ref_gate.py --persona-root <vault> --image <path>` (or `--images glob`):

1. InsightFace detect → primary face (largest). No face → **reject**
   (`no_face`).
2. ArcFace 512-d embedding of the aligned face; cosine similarity vs **every**
   embedding in the curated set; the gate score is the **max** similarity
   (best-matching reference — the person is the same if *any* reference matches).
3. Score ≥ threshold (from `gate-config.json`) → **pass**; else **reject**.
   Every check — pass or reject — appends a line to
   `media/identity-gate-log.jsonl` with the full score vector.
4. Policy on reject: caller may retry with `--retry N` (fresh generation,
   Kontext is stochastic); retries are new gate rows. Systematic rejection is a
   curation smell (bad reference set), not a gate bug.

**Threshold calibration (Hume-gate pattern: measure before production).**
`calibrate_gate.py` builds a gold set:

- **Positives** = (reference, output) pairs where the output was
  Kontext-generated *from that reference* across scene-varying prompts.
- **Negatives** = (reference, output) pairs across *different* identities
  (worst case: same scene genre, different person).
- It writes `gate-config.json` with the chosen threshold, the two score
  distributions, separation, and TPR/FPR at the threshold.

v1 gold set (2026-08-27, this repo): 3 rights-clean **synthetic-seed**
identities (flux/schnell solo portraits, `rights.basis=synthetic`), 8
scene-varying Kontext outputs each → 24 positive and 48 negative pairs
(scores in `experiments/identity-pipeline/2026-08-27-gold-calibration/`).
Production promotion requires a **human-subject gold set (~50 images/class)**
built from consented uploads before client use — same rule that gated the Hume
labeler (accuracy measured first, threshold set from measured agreement).

## 5. REGISTRY — flat append-only provenance

Every validated asset gets one line in `<vault>/media/identity-registry.jsonl`
(`ref_registry.py append`, called by the pipeline runner; JSONL only — no graph
stores, no DB dependency):

```json
{
  "asset": "media/images/2026-08-27T17-00-00_hiking.png",
  "sha256": "…",
  "model": "fal-ai/flux-pro/kontext",
  "references_used": ["ref_20260827_ab12cd", "ref_20260827_ef01aa"],
  "prompt": "same person, hiking …",
  "gate": {"score": 0.612, "threshold": 0.45, "per_ref": {"ref_…": 0.612}, "passed": true},
  "generated_at": "2026-08-27T17:00:00Z",
  "registered_at": "2026-08-27T17:00:05Z"
}
```

Registry is append-only (tamper = visible as hash-chain break; v1 keeps it
simple: never rewrite lines). Rejected generations live in
`media/identity-gate-log.jsonl`, not the registry.

---

## Acceptance protocol (issue criterion, measured not eyeballed)

For one curated reference set, run 3 **unseen** prompts (not used in
calibration) → 3 outputs; each must independently pass the gate against the
curated set. Evidence recorded: gate score vectors + registry rows + the images
themselves, committed under `experiments/identity-pipeline/`.

## Known limits / next steps

- **Threshold is R&D-calibrated on synthetic identities.** Face-embedding
  separation is typically large (ArcFace same-person ≈ 0.5–0.8, different ≈
  0.0–0.25) but the production threshold must be re-proven on a consented human
  gold set (~50 images/class) before any client persona goes live. `ref_generate_validated.py`
  and the Kestra flow enforce this fail-closed (`production_safe=false` → no generation).
  gold-set procedure when consented photos arrive: lay out `<src>/<person>/*.jpg`
  + a consent manifest CSV (`person,file,basis,consent_by,license_ref`) →
  `build_human_goldset.py --src <src> --consent manifest.csv --out <gold_dir>`
  (fails closed on missing consent; dedupes; picks references; dry-scores the
  pos/neg distributions BEFORE any spend) → `calibrate_gate.py --persona-root
  <vault> --gold-dir <dir>` (NO `--force`).
- **Multi-face references** are rejected at curation (identity ambiguity — the
  demoted Aug-27 portrait has a background face and would fail).
- **Older/dated references** (decade-old photos): not yet modeled; the curated
  set schema has room for `quality.notes`/date attributes if drift becomes a
  problem.
- **Kestra productionization** (`flows/vault-media.yaml`): LANDED 2026-08-31
  (HU-2157) — the paused slop flow is replaced by the grounded pipeline
  (`huible-vault-media`: guard → intake → generate → manifest). Guard fails
  closed unless the gate config is `production_safe=true` or an explicit
  `confirm_rd_synthetic` R&D ack is set; text-to-image stays refused by
  `generate_image.py` itself. Voice lane removed (belongs to the HU-2151 voice
  pipeline; `generate_voice.py` CLI remains available).
- **Cost/latency:** flux-pro/kontext ≈ $0.04/gen, 15–45 s each; gate is local
  and free. Gold calibration ≈ $1.5 one-off per model version.

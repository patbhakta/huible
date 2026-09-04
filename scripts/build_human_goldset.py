#!/usr/bin/env python3
"""HUMAN gold-set builder (HU-2157 item 2) — assemble + dry-score in one pass.

Turns a consented per-person photo tree into the calibrate_gate.py layout and
reports the measured positive/negative score distributions BEFORE calibration,
so the gold set can be judged before spending anything on generation.

Input tree (one directory per person, photos inside):

  <src>/<person>/*.{jpg,jpeg,png}

Consent manifest (CSV, fail closed — a photo without a valid row is rejected):

  person,file,basis,consent_by,license_ref
  jane-doe,jane-1.jpg,client_upload,client:jane-doe:onboarding,
  john-smith,john-2.jpg,license,,stock#123

Output layout (what calibrate_gate.py --gold-dir expects):

  <out>/<person>/reference.png     best single-face photo (largest usable face)
  <out>/<person>/outputs/*.png     every other accepted photo of that person

Positives/negatives follow calibrate_gate semantics: real same-person photos
as outputs give a CONSERVATIVE positive distribution (generation stays closer
to its reference than unrelated real shots do), so a threshold that separates
here separates for generated outputs too.

Usage:
  python3 build_human_goldset.py --src /path/consented --consent manifest.csv \
      --out /path/gold-human [--min-pos 50] [--min-neg 50]
"""

import argparse
import csv
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from refpipe_common import now_iso, sha256_of

IMG_EXT = (".jpg", ".jpeg", ".png")
PHASH_DUP_DIST = 8      # mirror ref_curate.py
MIN_FACE_AREA_FRAC = 0.10
MIN_FACE_W_PX = 160
MAX_ABS_YAW = 45.0
MAX_ABS_PITCH = 30.0


def _embed(path):
    from refpipe_common import face_embedding
    return face_embedding(path)


def _phash(path):
    import imagehash
    from PIL import Image
    return imagehash.phash(Image.open(path).convert("RGB"))


def load_consent(path):
    """{(person_lower, file_lower): row} from the CSV manifest."""
    rows = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = ((row.get("person") or "").strip().lower(),
                   (row.get("file") or "").strip().lower())
            if key != ("", ""):
                rows[key] = {k: (v or "").strip() for k, v in row.items()}
    return rows


def consent_ok(row):
    """Fail-closed, mirrors refpipe_common.valid_rights semantics."""
    if not row:
        return False, "no_consent_row"
    basis = row.get("basis")
    if basis == "client_upload":
        return (bool(row.get("consent_by")), None if row.get("consent_by")
                else "consent_by_missing")
    if basis == "license":
        return (bool(row.get("license_ref")), None if row.get("license_ref")
                else "license_ref_missing")
    return False, f"bad_basis:{basis or 'missing'}"


def quality_reasons(m):
    """Same thresholds as ref_curate.py; multi-face rejected (identity ambiguity)."""
    r = []
    if m.get("faces", 0) == 0:
        return ["no_face"]
    if m.get("faces", 0) > 1:
        return ["multi_face"]
    if m.get("face_w_px", 0) < MIN_FACE_W_PX:
        r.append("face_too_small")
    if m.get("face_area_frac", 0) < MIN_FACE_AREA_FRAC:
        r.append("face_area_frac_low")
    if abs(m.get("yaw", 0.0)) > MAX_ABS_YAW:
        r.append("yaw_extreme")
    if abs(m.get("pitch", 0.0)) > MAX_ABS_PITCH:
        r.append("pitch_extreme")
    return r


def build(src_dir, consent, out_dir):
    """Assemble the gold layout. Returns (kept, rejected) records."""
    os.makedirs(out_dir, exist_ok=True)
    rejected, kept = [], []
    people = sorted(d for d in os.listdir(src_dir)
                    if os.path.isdir(os.path.join(src_dir, d)))
    for person in people:
        pdir = os.path.join(src_dir, person)
        cands = []
        for name in sorted(os.listdir(pdir)):
            path = os.path.join(pdir, name)
            if not name.lower().endswith(IMG_EXT):
                continue
            ok, why = consent_ok(consent.get((person.lower(), name.lower())))
            if not ok:
                rejected.append({"person": person, "file": name, "reason": why})
                continue
            emb, m = _embed(path)
            if emb is None:
                rejected.append({"person": person, "file": name,
                                 "reason": "unusable:" + json.dumps(m)})
                continue
            reasons = quality_reasons(m)
            if reasons:
                rejected.append({"person": person, "file": name,
                                 "reason": ",".join(reasons)})
                continue
            cands.append({"person": person, "path": path, "name": name,
                          "emb": emb, "metrics": m,
                          "phash": _phash(path)})

        # phash dedupe within person, keep the better-quality copy
        cands.sort(key=lambda c: (-c["metrics"]["face_area_frac"],
                                  -c["metrics"]["face_w_px"]))
        keepers = []
        for c in cands:
            dup = next((k for k in keepers
                        if c["phash"] - k["phash"] <= PHASH_DUP_DIST), None)
            if dup:
                rejected.append({"person": person, "file": c["name"],
                                 "reason": f"duplicate_of:{dup['name']}"})
                continue
            keepers.append(c)

        if not keepers:
            continue
        # reference = first keeper (already sorted by face prominence)
        ref, outs = keepers[0], keepers[1:]
        odir = os.path.join(out_dir, person, "outputs")
        os.makedirs(odir, exist_ok=True)
        shutil.copyfile(ref["path"], os.path.join(out_dir, person, "reference.png"))
        for c in outs:
            shutil.copyfile(c["path"], os.path.join(odir, c["name"]))
        kept.append({"person": person, "reference": ref["name"],
                     "n_outputs": len(outs),
                     "ref_sha256": sha256_of(ref["path"]),
                     "ref_metrics": ref["metrics"],
                     "_ref_emb": ref["emb"],
                     "_output_embs": [c["emb"] for c in outs]})
    return kept, rejected


def dry_scores(kept):
    """Cosine distributions over the assembled layout (reference vs outputs)."""
    from refpipe_common import cosine
    pos, neg = [], []
    for i, a in enumerate(kept):
        for j, b in enumerate(kept):
            if i == j:
                continue
            for c in a["_output_embs"]:
                pos.append(cosine(c, a["_ref_emb"]))
                neg.append(cosine(c, b["_ref_emb"]))
    return pos, neg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="per-person photo tree")
    ap.add_argument("--consent", required=True, help="consent manifest CSV")
    ap.add_argument("--out", required=True, help="gold dir for calibrate_gate")
    ap.add_argument("--min-pos", type=int, default=50)
    ap.add_argument("--min-neg", type=int, default=50)
    args = ap.parse_args()

    kept, rejected = build(args.src, load_consent(args.consent), args.out)
    pos, neg = dry_scores(kept)
    kept_public = [{k: v for k, v in rec.items() if not k.startswith("_")}
                   for rec in kept]

    fully_separated = bool(pos and neg and max(neg) < min(pos))
    summary = {
        "built_at": now_iso(),
        "src": os.path.abspath(args.src),
        "out": os.path.abspath(args.out),
        "identities": len(kept),
        "photos_accepted": sum(k["n_outputs"] + 1 for k in kept),
        "photos_rejected": len(rejected),
        "pairs": {"positive": len(pos), "negative": len(neg)},
        "recommended_minimums": {"positive": args.min_pos, "negative": args.min_neg},
        "meets_recommendation": len(pos) >= args.min_pos and len(neg) >= args.min_neg,
        "dry_scores": {
            "positive": {"min": min(pos) if pos else None,
                         "max": max(pos) if pos else None},
            "negative": {"min": min(neg) if neg else None,
                         "max": max(neg) if neg else None},
            "fully_separated": fully_separated,
        },
        "kept": kept_public,
        "rejected": rejected,
    }
    with open(os.path.join(args.out, "goldset-summary.json"), "w",
              encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print(json.dumps({k: summary[k] for k in (
        "identities", "photos_accepted", "photos_rejected", "pairs",
        "meets_recommendation", "dry_scores")}, indent=2))
    if not kept:
        sys.exit("no usable identities assembled — see goldset-summary.json")
    print(f"\nNext: calibrate_gate.py --persona-root <vault> --gold-dir {args.out} "
          "(WITHOUT --force)")


if __name__ == "__main__":
    main()

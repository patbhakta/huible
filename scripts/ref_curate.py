#!/usr/bin/env python3
"""CURATE stage (HU-2150) — dedupe, quality filter, rights check.

Reads references/reference-set.json, drops perceptual duplicates (phash
distance <= 8), low-quality photos (no face / tiny face / extreme pose /
low resolution) and rights-incomplete records. Writes:

  references/curated.jsonl        accepted set (append-only)
  references/curation-log.jsonl   every decision incl. rejections (audit)
  references/embeddings.json      ArcFace 512-d embeddings of accepted photos

Usage:
  python3 ref_curate.py --persona-root /root/repos/personas/<p> [--min-edge 512]
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from refpipe_common import (
    append_jsonl,
    face_embedding,
    load_json,
    now_iso,
    valid_rights,
    vault_paths,
)

PHASH_DUP_DIST = 8          # imagehash hamming distance => perceptual duplicate
MIN_FACE_AREA_FRAC = 0.10   # largest face must cover >= 10% of the image
MIN_FACE_W_PX = 160         # enough pixels for a stable ArcFace embedding
MAX_ABS_YAW = 45.0          # degrees — beyond this the face is not usable
MAX_ABS_PITCH = 30.0


def phash_of(path):
    import imagehash
    from PIL import Image
    return imagehash.phash(Image.open(path).convert("RGB"))


def quality_ok(m, min_edge):
    reasons = []
    if m.get("faces", 0) == 0:
        reasons.append("no_face")
        return reasons
    if m.get("faces", 0) > 1:
        reasons.append("multi_face")  # identity ambiguity — reject
    if m["face_w_px"] < MIN_FACE_W_PX:
        reasons.append(f"face_too_narrow({m['face_w_px']}px<{MIN_FACE_W_PX})")
    if m["face_area_frac"] < MIN_FACE_AREA_FRAC:
        reasons.append(f"face_too_small({m['face_area_frac']:.2f}<{MIN_FACE_AREA_FRAC})")
    if abs(m["yaw"]) > MAX_ABS_YAW:
        reasons.append(f"pose_yaw({m['yaw']:.0f}deg)")
    if abs(m["pitch"]) > MAX_ABS_PITCH:
        reasons.append(f"pose_pitch({m['pitch']:.0f}deg)")
    return reasons


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--persona-root", required=True)
    ap.add_argument("--min-edge", type=int, default=512)
    args = ap.parse_args()

    p = vault_paths(args.persona_root)
    if not os.path.exists(p["set_json"]):
        sys.exit(f"no reference set at {p['set_json']} — run ref_collect.py first")

    records = load_json(p["set_json"])
    kept, rejected = [], []
    hashes = []  # (phash, quality_score, record) of current keepers

    for rec in records:
        path = os.path.join(args.persona_root, rec["path"])
        reasons = []
        if not os.path.isfile(path):
            reasons.append("file_missing")
            rejected.append((rec, reasons))
            continue
        if not valid_rights(rec):
            reasons.append("rights_incomplete")
            rejected.append((rec, reasons))
            continue
        from PIL import Image
        with Image.open(path) as im:
            w, h = im.size
        if min(w, h) < args.min_edge:
            reasons.append(f"low_resolution({w}x{h}<{args.min_edge})")
            rejected.append((rec, reasons))
            continue

        h_ph = phash_of(path)
        dup_of = None
        for other_ph, _, other_rec in hashes:
            if h_ph - other_ph <= PHASH_DUP_DIST:
                dup_of = other_rec["photo_id"]
                break
        emb, m = face_embedding(path)
        m["width"], m["height"] = w, h
        reasons += quality_ok(m, args.min_edge)
        if dup_of:
            reasons.append(f"perceptual_duplicate(of {dup_of})")
        if reasons:
            rejected.append((rec, reasons))
            continue

        quality_score = m["face_w_px"] * m["face_area_frac"]  # bigger, better-res face wins
        kept.append({"rec": rec, "phash": str(h_ph), "metrics": m, "emb": emb})
        hashes.append((h_ph, quality_score, rec))

    # Persist
    emb_store = {}
    for k in kept:
        line = {
            "photo_id": k["rec"]["photo_id"], "path": k["rec"]["path"],
            "sha256": k["rec"]["sha256"], "quality": k["metrics"], "phash": k["phash"],
            "kept": True, "reasons": [], "curated_at": now_iso(),
        }
        append_jsonl(p["curated"], line)
        emb_store[k["rec"]["photo_id"]] = {
            "path": k["rec"]["path"], "sha256": k["rec"]["sha256"],
            "emb": [round(float(x), 6) for x in k["emb"]],
        }
    for rec, reasons in rejected:
        append_jsonl(p["cur_log"], {
            "photo_id": rec.get("photo_id"), "path": rec.get("path"),
            "kept": False, "reasons": reasons, "curated_at": now_iso(),
        })
    with open(p["emb"], "w", encoding="utf-8") as f:
        json.dump(emb_store, f)
    append_jsonl(p["cur_log"], {
        "event": "curate", "at": now_iso(), "input": len(records),
        "kept": len(kept), "rejected": len(rejected),
    })

    print(json.dumps({"ok": True, "input": len(records), "kept": len(kept),
                      "rejected": len(rejected),
                      "rejections": {r[0]["photo_id"]: r[1] for r in rejected}}))
    if not kept:
        sys.exit(1)


if __name__ == "__main__":
    main()

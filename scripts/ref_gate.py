#!/usr/bin/env python3
"""VALIDATE stage (HU-2150) — quantitative identity gate.

ArcFace cosine similarity of the primary face in a candidate image vs every
embedding in the curated reference set (references/embeddings.json). Gate score
= max similarity (best-matching reference). Threshold comes from
references/gate-config.json (calibrated by calibrate_gate.py — Hume-gate
pattern: measure before production). Every check, pass or reject, is appended
to media/identity-gate-log.jsonl with the full per-reference score vector.

Usage:
  python3 ref_gate.py --persona-root /root/repos/personas/<p> --image out.png
  python3 ref_gate.py --persona-root ... --images 'out/*.png' --json
"""

import argparse
import glob
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from refpipe_common import (
    append_jsonl,
    cosine,
    face_embedding,
    load_json,
    now_iso,
    vault_paths,
)


def gate_one(paths, image_path):
    emb, metrics = face_embedding(image_path)
    per_ref = {}
    if emb is None:
        verdict = {"image": image_path, "ok": False, "reason": "no_face",
                   "metrics": metrics, "score": None, "per_ref": {}}
    else:
        per_ref = {pid: round(cosine(emb, np.asarray(e["emb"], dtype=np.float32)), 4)
                   for pid, e in paths["emb_store"].items()}
        score = max(per_ref.values())
        verdict = {
            "image": image_path, "ok": score >= paths["threshold"], "reason": None,
            "metrics": metrics, "score": round(float(score), 4),
            "threshold": paths["threshold"], "per_ref": per_ref,
        }
    append_jsonl(paths["gate_log"], {"at": now_iso(), **verdict})
    return verdict


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--persona-root", required=True)
    ap.add_argument("--image")
    ap.add_argument("--images", help="glob")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if not args.image and not args.images:
        sys.exit("need --image or --images")

    p = vault_paths(args.persona_root)
    emb_store = load_json(p["emb"])
    if not emb_store:
        sys.exit(f"empty curated embeddings at {p['emb']} — run ref_curate.py first")
    cfg = load_json(p["gate_config"]) if os.path.exists(p["gate_config"]) else None
    if not cfg:
        sys.exit(f"no gate config at {p['gate_config']} — run calibrate_gate.py first "
                 "(thresholds are measured, never guessed)")
    paths = {"emb_store": emb_store, "threshold": float(cfg["threshold"]),
             "gate_log": p["gate_log"]}

    targets = [args.image] if args.image else sorted(glob.glob(args.images))
    verdicts = [gate_one(paths, t) for t in targets]
    all_ok = all(v["ok"] for v in verdicts)
    if args.json:
        # Marker protocol: insightface/onnxruntime chatter shares stdout;
        # machine consumers parse everything after this line.
        print("===GATE_JSON===")
        print(json.dumps({"ok": all_ok, "results": verdicts}, indent=2))
    else:
        for v in verdicts:
            print(("PASS " if v["ok"] else "REJECT") + f" {v['image']}"
                  + (f" score={v['score']}" if v["score"] is not None else f" reason={v['reason']}")
                  + f" thr={paths['threshold']}")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()

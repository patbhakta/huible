#!/usr/bin/env python3
"""Gate calibration (HU-2150) — Hume-gate pattern: measure before production.

Scores a gold set with the SAME embedder the gate uses (ArcFace buffalo_l) and
writes references/gate-config.json with a measured threshold.

Gold-set layout (works for synthetic R&D sets AND future consented human sets):

  <gold_dir>/<identity>/reference.png     curated-style reference photo
  <gold_dir>/<identity>/outputs/*.png     generations from that reference

Positives  = (reference_i, output of i)      — same identity, scene varies.
Negatives  = (reference_i, output of j != i) — different identity (worst case:
             same genre scenes, different person).
Threshold  = midpoint(max_negative, min_positive) when fully separated,
             else the best-F1 cut; overlap is reported loudly and the config
             is marked not production-safe.

Usage:
  python3 calibrate_gate.py --persona-root /root/repos/personas/<p> --gold-dir <dir>
"""

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from refpipe_common import cosine, face_embedding, now_iso, vault_paths


def score_pairs(gold_dir):
    identities = sorted(d for d in os.listdir(gold_dir)
                        if os.path.isdir(os.path.join(gold_dir, d)))
    refs, outs = {}, {}
    for ident in identities:
        ref_path = os.path.join(gold_dir, ident, "reference.png")
        if not os.path.isfile(ref_path):
            sys.exit(f"missing {ref_path}")
        emb, m = face_embedding(ref_path)
        if emb is None:
            sys.exit(f"reference of {ident} has no face: {m}")
        refs[ident] = emb
        outs[ident] = []
        od = os.path.join(gold_dir, ident, "outputs")
        for name in sorted(os.listdir(od)) if os.path.isdir(od) else []:
            if not name.lower().endswith((".png", ".jpg", ".jpeg")):
                continue
            oemb, om = face_embedding(os.path.join(od, name))
            outs[ident].append((name, oemb, om))

    pos, neg = [], []
    for ident, olist in outs.items():
        for name, oemb, _om in olist:
            if oemb is None:
                print(f"WARN {ident}/{name}: no face — excluded from calibration")
                continue
            for rident, remb in refs.items():
                s = cosine(oemb, remb)
                (pos if rident == ident else neg).append(
                    {"identity": ident, "ref": rident, "output": name, "score": round(s, 4)})
    return identities, pos, neg


def pick_threshold(pos, neg):
    ps = sorted(x["score"] for x in pos)
    ns = sorted(x["score"] for x in neg)
    fully_separated = ns[-1] < ps[0]
    if fully_separated:
        thr = round((ns[-1] + ps[0]) / 2, 4)
        note = f"fully separated: max_neg={ns[-1]} < min_pos={ps[0]}"
    else:
        # best F1 cut over candidate midpoints (overlap — flag as not production-safe)
        candidates = sorted({s for s in ps + ns})
        best = (None, -1.0)
        for i in range(len(candidates) - 1):
            t = (candidates[i] + candidates[i + 1]) / 2
            tp = sum(1 for s in ps if s >= t)
            fp = sum(1 for s in ns if s >= t)
            fn = len(ps) - tp
            prec = tp / (tp + fp) if tp + fp else 0.0
            rec = tp / (tp + fn) if tp + fn else 0.0
            f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
            if f1 > best[1]:
                best = (t, f1)
        thr = round(best[0], 4)
        note = f"OVERLAP — best-F1 cut only (F1={best[1]:.3f}); NOT production-safe"
    tpr = sum(1 for s in ps if s >= thr) / len(ps) if ps else 0.0
    fpr = sum(1 for s in ns if s >= thr) / len(ns) if ns else 0.0
    return thr, fully_separated, note, tpr, fpr, ps, ns


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--persona-root", required=True)
    ap.add_argument("--gold-dir", required=True)
    ap.add_argument("--min-pos", type=int, default=50)
    ap.add_argument("--min-neg", type=int, default=50)
    ap.add_argument("--force", action="store_true",
                    help="write config even below the recommended pair counts")
    args = ap.parse_args()

    identities, pos, neg = score_pairs(args.gold_dir)
    if not pos or not neg:
        sys.exit("gold set must contain at least one positive and one negative pair")
    thr, separated, note, tpr, fpr, ps, ns = pick_threshold(pos, neg)

    counts_ok = len(pos) >= args.min_pos and len(neg) >= args.min_neg
    if not counts_ok and not args.force:
        sys.exit(f"gold set too small ({len(pos)} pos / {len(neg)} neg < "
                 f"{args.min_pos}/{args.min_neg} recommended — Hume-gate pattern). "
                 "Add pairs or rerun with --force (R&D only).")

    p = vault_paths(args.persona_root)
    cfg = {
        "threshold": thr,
        "embedder": "insightface buffalo_l / w600k_r50 (ArcFace 512-d, cosine)",
        "calibrated_at": now_iso(),
        "gold_dir": os.path.abspath(args.gold_dir),
        "identities": identities,
        "pairs": {"positive": len(pos), "negative": len(neg)},
        "distributions": {
            "positive": {"min": ps[0], "median": round(float(np.median(ps)), 4), "max": ps[-1]},
            "negative": {"min": ns[0], "median": round(float(np.median(ns)), 4), "max": ns[-1]},
        },
        "at_threshold": {"tpr": round(tpr, 4), "fpr": round(fpr, 4)},
        "fully_separated": separated,
        "note": note,
        "production_safe": bool(separated and counts_ok),
    }
    with open(p["gate_config"], "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)

    print(json.dumps({
        "ok": True, "threshold": thr, "pos_min": ps[0], "neg_max": ns[-1],
        "separated": separated, "tpr": round(tpr, 4), "fpr": round(fpr, 4),
        "pairs": {"positive": len(pos), "negative": len(neg)},
        "production_safe": cfg["production_safe"],
    }, indent=2))
    with open(os.path.join(args.gold_dir, "calibration-pairs.json"), "w") as f:
        json.dump({"positive": pos, "negative": neg, "config": cfg}, f, indent=2)


if __name__ == "__main__":
    main()

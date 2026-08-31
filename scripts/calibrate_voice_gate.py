#!/usr/bin/env python3
"""CALIBRATE stage (HU-2151) — gold-set threshold, Hume-gate pattern.

Mirrors calibrate_gate.py (image twin). Builds the score distributions and
writes references/voice-gate-config.json:

- Positives  = (reference set, held-out same-speaker clip) — the zero-spend
  proxy for "clone output vs its references": a faithful clone IS the same
  speaker, so its similarity lands in the same-speaker band. (The image
  twin's gold set used actual generator outputs; here cloning spend is
  forbidden until the gate exists, so v1 is calibrated on held-out natural
  speech of the same speakers.)
- Negatives  = (reference set, clip of a DIFFERENT speaker) — worst case
  same corpus/genre, different person.

Threshold = midpoint(pos_min, neg_max) when the distributions separate;
records TPR/FPR at the threshold. production_safe stays false by design —
promotion requires (a) clone-output gold set (same protocol, cloned lines
vs held-out references) and (b) for client personas, a consented human gold
set (~50 clips/class). internal_only is inherited from the reference set.

Usage:
  calibrate_voice_gate.py --persona-root <vault> --gold-dir <dir>

  gold-dir layout (one vault per speaker):
    <dir>/<speaker>/references/*.wav   curated-style reference clips
    <dir>/<speaker>/heldout/*.wav      held-out same-speaker clips
"""

import argparse
import glob
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from voicepipe_common import (
    GATE_ID,
    append_jsonl,
    cosine,
    embed_file,
    load_json,
    now_iso,
    vault_paths,
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--persona-root", required=True)
    ap.add_argument("--gold-dir", required=True)
    ap.add_argument("--evidence-dir", help="write per-pair scores + summary here")
    args = ap.parse_args()

    speakers = sorted(d for d in os.listdir(args.gold_dir)
                      if os.path.isdir(os.path.join(args.gold_dir, d)))
    if len(speakers) < 2:
        sys.exit("gold set needs ≥ 2 speakers for real negatives")

    ref_embs, held_embs = {}, {}
    for spk in speakers:
        refs = sorted(glob.glob(os.path.join(args.gold_dir, spk, "references", "*.wav")))
        held = sorted(glob.glob(os.path.join(args.gold_dir, spk, "heldout", "*.wav")))
        if not refs or not held:
            sys.exit(f"speaker {spk}: needs references/ and heldout/ wavs")
        ref_embs[spk] = {os.path.basename(r): embed_file(r)[0] for r in refs}
        held_embs[spk] = {os.path.basename(h): embed_file(h)[0] for h in held}

    pairs = []
    for spk in speakers:
        for hid, hemb in held_embs[spk].items():
            per_ref = {rid: round(cosine(hemb, remb), 4) for rid, remb in ref_embs[spk].items()}
            pairs.append({"speaker": spk, "clip": hid, "label": "pos", **per_ref,
                          "score": max(per_ref.values())})
        for other in speakers:
            if other == spk:
                continue
            for hid, hemb in held_embs[other].items():
                per_ref = {rid: round(cosine(hemb, remb), 4) for rid, remb in ref_embs[spk].items()}
                pairs.append({"speaker": other, "vs_refs_of": spk, "clip": hid, "label": "neg",
                              **per_ref, "score": max(per_ref.values())})

    pos = [p["score"] for p in pairs if p["label"] == "pos"]
    neg = [p["score"] for p in pairs if p["label"] == "neg"]
    pos_min, neg_max = min(pos), max(neg)
    separated = pos_min > neg_max
    threshold = round((pos_min + neg_max) / 2.0, 4)
    tpr = sum(s >= threshold for s in pos) / len(pos)
    fpr = sum(s >= threshold for s in neg) / len(neg)
    passed = separated and tpr == 1.0 and fpr == 0.0

    set_json = vault_paths(args.persona_root)["set_json"]
    internal_only = False
    if os.path.exists(set_json):
        internal_only = bool(load_json(set_json).get("internal_only"))

    cfg = {
        "gate": GATE_ID,
        "threshold": threshold,
        "passed": passed,
        "production_safe": False,
        "internal_only": internal_only,
        "calibrated_at": now_iso(),
        "gold_set": {
            "speakers": speakers,
            "pos_pairs": len(pos), "neg_pairs": len(neg),
            "pos_min": round(pos_min, 4), "pos_mean": round(float(np.mean(pos)), 4),
            "neg_max": round(neg_max, 4), "neg_mean": round(float(np.mean(neg)), 4),
            "separated": separated, "tpr": tpr, "fpr": fpr,
            "protocol": "held-out same-speaker positives (zero-spend proxy), "
                        "cross-speaker negatives",
            "promotion_requires": "clone-output gold set + consented human "
                                  "gold set (~50 clips/class)",
        },
        "references_used": sorted(
            os.path.basename(r) for spk in speakers
            for r in glob.glob(os.path.join(args.gold_dir, spk, "references", "*.wav"))),
    }
    cfg_path = vault_paths(args.persona_root)["gate_config"]
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)

    if args.evidence_dir:
        os.makedirs(args.evidence_dir, exist_ok=True)
        with open(os.path.join(args.evidence_dir, "gold-pairs.jsonl"), "w", encoding="utf-8") as f:
            for p in pairs:
                f.write(json.dumps(p) + "\n")
        append_jsonl(os.path.join(args.evidence_dir, "summary.json"), cfg["gold_set"])

    print(json.dumps({"ok": passed, "threshold": threshold, "tpr": tpr, "fpr": fpr,
                      "pos_min": round(pos_min, 4), "neg_max": round(neg_max, 4),
                      "config": os.path.relpath(cfg_path, args.persona_root)}))
    if not passed:
        sys.exit(1)


if __name__ == "__main__":
    main()

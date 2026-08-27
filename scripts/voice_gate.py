#!/usr/bin/env python3
"""VALIDATE stage (HU-2151) — speaker-similarity identity gate.

Mirrors ref_gate.py (image twin). For each --audio file:

1. Decode → 16 kHz mono → VAD-trim → resemblyzer 256-d speaker embedding.
2. Cosine similarity vs EVERY embedding in the curated reference set; the
   gate score is the max similarity (best-matching reference clip).
3. Score ≥ threshold (from references/voice-gate-config.json) → pass; else
   reject. Every check — pass or reject — appends a line to
   media/voice-gate-log.jsonl with the full score vector.
4. Policy on reject: caller may regenerate/retry; retries are new gate rows.
   Systematic rejection is a curation smell (bad reference set), not a gate
   bug.

Usage:
  voice_gate.py --persona-root <vault> --audio out.wav [--out-verdict out.verdict.json]
"""

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from voicepipe_common import (
    append_jsonl,
    cosine,
    embed_file,
    load_json,
    now_iso,
    sha256_of,
    vault_paths,
)


def gate_one(persona_root, path, cfg, ref_embs):
    emb, q = embed_file(path)
    per_ref = {cid: round(cosine(emb, e), 4) for cid, e in ref_embs.items()}
    score = max(per_ref.values())
    best = max(per_ref, key=per_ref.get)
    return {
        "ok": score >= cfg["threshold"],
        "score": score,
        "threshold": cfg["threshold"],
        "best_ref": best,
        "per_ref": per_ref,
        "quality": q,
        "audio": path,
        "sha256": sha256_of(path),
        "gate": "resemblyzer-256d-cosine-max",
        "checked_at": now_iso(),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--persona-root", required=True)
    ap.add_argument("--audio", nargs="+", required=True)
    ap.add_argument("--out-verdict",
                    help="write last verdict JSON to this path (for voice_registry)")
    args = ap.parse_args()

    p = vault_paths(args.persona_root)
    if not os.path.exists(p["gate_config"]):
        sys.exit("no gate config — run calibrate_voice_gate.py first "
                 "(Hume-gate pattern: measure before production)")
    cfg = load_json(p["gate_config"])
    if not cfg.get("passed"):
        sys.exit("gate config did not pass calibration — recalibrate before gating")
    ref_embs = {cid: np.asarray(v["emb"], dtype="float32")
                for cid, v in load_json(p["emb"])["embeddings"].items()}
    if not ref_embs:
        sys.exit("empty curated embeddings — run voice_curate.py first")

    verdict = None
    for path in args.audio:
        verdict = gate_one(args.persona_root, path, cfg, ref_embs)
        append_jsonl(p["gate_log"], verdict)
        print(json.dumps({"ok": verdict["ok"], "score": verdict["score"],
                          "threshold": verdict["threshold"],
                          "best_ref": verdict["best_ref"], "audio": path}))
    if args.out_verdict and verdict is not None:
        with open(args.out_verdict, "w", encoding="utf-8") as f:
            json.dump(verdict, f, indent=2)
        print(json.dumps({"ok": True, "verdict": args.out_verdict}))


if __name__ == "__main__":
    main()

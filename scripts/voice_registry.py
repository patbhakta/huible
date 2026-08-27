#!/usr/bin/env python3
"""REGISTRY stage (HU-2151) — flat append-only provenance records.

One JSONL line per validated voice asset in
<persona_root>/media/voice-registry.jsonl: asset path + sha256, cloning
model+version, references used, text, gate similarity score, dates.
Append-only; no graph stores. Rejected generations live in the gate log,
never here.

Usage:
  voice_registry.py append --persona-root ... \
      --audio out.wav --prov out.wav.prov.json --gate-verdict out.verdict.json
  voice_registry.py verify --persona-root ...
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from voicepipe_common import append_jsonl, load_json, now_iso, sha256_of, vault_paths


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["append", "verify"])
    ap.add_argument("--persona-root", required=True)
    ap.add_argument("--audio")
    ap.add_argument("--prov")
    ap.add_argument("--gate-verdict")
    args = ap.parse_args()

    p = vault_paths(args.persona_root)

    if args.cmd == "append":
        prov = load_json(args.prov)
        verdict = load_json(args.gate_verdict)
        if not verdict.get("ok"):
            sys.exit("refused: gate verdict is not ok — rejected assets are never registered")
        record = {
            "asset": os.path.relpath(os.path.abspath(args.audio), args.persona_root),
            "sha256": sha256_of(args.audio),
            "model": prov["model"],
            "model_version": prov.get("model_version"),
            "references_used": prov["references_used"],
            "text": prov["text"],
            "gate": {
                "score": verdict["score"], "threshold": verdict["threshold"],
                "per_ref": verdict["per_ref"], "passed": True,
            },
            "generated_at": prov["generated_at"],
            "registered_at": now_iso(),
        }
        append_jsonl(p["registry"], record)
        print(json.dumps({"ok": True, "registered": record["asset"]}))
        return

    seen, n = {}, 0
    with open(p["registry"], encoding="utf-8") as f:
        for line in f:
            n += 1
            r = json.loads(line)
            key = (r["asset"], r["sha256"])
            if key in seen:
                print(f"DUPLICATE line {n}: {key}")
                sys.exit(1)
            seen[key] = n
            for field in ("asset", "sha256", "model", "model_version", "references_used",
                          "gate", "generated_at", "registered_at"):
                if field not in r:
                    print(f"SCHEMA line {n} missing {field}")
                    sys.exit(1)
    print(json.dumps({"ok": True, "records": n, "unique": len(seen)}))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Re-embed a vault's curated set with the current embedder (HU-2160).

Embedding-model migration helper: after swapping the embedder behind
embed_wav (resemblyzer 256-d -> ECAPA 192-d), the cached curated
embeddings in references/voice-embeddings.json are stale — the gate would
compare a fresh 192-d candidate embedding against 256-d reference vectors
and fail on the dimension mismatch. This script re-embeds every kept
curated clip (same clip ids, same source paths/offsets from
voice-curated.jsonl) and overwrites the cache in place. The curated jsonl,
curation log, and reference set are NOT touched.

A mismatch guard aborts if the cached embeddings already have the current
dimension (no-op protection against accidental double runs overwriting
good data with nothing).

Usage:
  voice_reembed.py --persona-root <vault>
"""

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from voicepipe_common import decode_audio, embed_wav, load_json, now_iso, vault_paths


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--persona-root", required=True)
    args = ap.parse_args()

    p = vault_paths(args.persona_root)
    if not os.path.exists(p["curated"]) or not os.path.exists(p["emb"]):
        sys.exit("vault is missing curated set or embeddings cache")

    cache = load_json(p["emb"])
    dims = {len(v["emb"]) for v in cache["embeddings"].values()}
    if len(dims) != 1:
        sys.exit(f"inconsistent cached dims {dims} — refusing to re-embed")

    with open(p["curated"], encoding="utf-8") as f:
        last_row = json.loads(f.readlines()[-1])
    probe, _ = embed_wav(decode_audio(
        os.path.join(args.persona_root, last_row["path"])))
    if probe.shape[0] == dims.pop():
        print(json.dumps({"ok": True, "reembedded": 0, "reason": "dims already current"}))
        return

    rows, seen = [], set()
    with open(p["curated"], encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("kept") and rec["clip_id"] not in seen:
                seen.add(rec["clip_id"])
                rows.append(rec)

    embeddings = {}
    for rec in rows:
        wav = decode_audio(os.path.join(args.persona_root, rec["path"]))
        off = int(rec.get("offset_s") or 0)
        seg = wav[off * 16000:] if off else wav
        emb, q = embed_wav(seg)
        embeddings[rec["clip_id"]] = {"emb": [round(float(v), 6) for v in np.asarray(emb)],
                                      "speech_s": q["speech_s"]}

    if set(embeddings) != set(cache["embeddings"]):
        sys.exit("clip id mismatch between curated set and cache — aborting before write")

    with open(p["emb"], "w", encoding="utf-8") as f:
        json.dump({"updated_at": now_iso(), "embeddings": embeddings}, f)
    print(json.dumps({"ok": True, "reembedded": len(embeddings)}))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""CURATE stage (HU-2151) — dedupe, quality-filter, segment reference audio.

Mirrors ref_curate.py (image twin). Reads every record in
voice-reference-set.json, then per clip:

1. Decode → 16 kHz mono (ffmpeg).
2. Segment long files into ≤ --max-clip-s reference chunks (fixed windows,
   speech quality-filtered afterwards).
3. Dedupe — exact sha256 + coarse waveform-signature fingerprint.
4. Quality filter — VAD-trimmed speech ≥ --min-speech-s (default 3 s),
   clipping < 1 %, rms ≥ 0.005, source duration ≥ 2 s.
5. Rights check — fail-closed (voicepipe_common.valid_rights).

Output: references/voice-curated.jsonl (accepted clips) + rejected clips in
voice-curation-log.jsonl (audit, never deleted) + cached speaker
embeddings in references/voice-embeddings.json. Curated >= 1 clip required;
8-20 diverse clips is the production target. If any intake record is
internal_only (benchmark corpus), the curated set is flagged internal_only
and the gate config can never be promoted to production_safe on this set.
"""

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from voicepipe_common import (
    SAMPLE_RATE,
    append_jsonl,
    audio_fingerprint,
    audio_quality,
    decode_audio,
    embed_wav,
    load_json,
    now_iso,
    valid_rights,
    vault_paths,
)


def segments(wav, max_clip_s):
    """Yield (offset_s, chunk) — whole clip if short, else fixed windows."""
    n = len(wav)
    if n <= max_clip_s * SAMPLE_RATE:
        yield 0.0, wav
        return
    win = max_clip_s * SAMPLE_RATE
    for start in range(0, n, win):
        yield round(start / SAMPLE_RATE, 2), wav[start:start + win]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--persona-root", required=True)
    ap.add_argument("--min-speech-s", type=float, default=3.0)
    ap.add_argument("--max-clip-s", type=float, default=30.0)
    args = ap.parse_args()

    p = vault_paths(args.persona_root)
    set_rec = load_json(p["set_json"]) if os.path.exists(p["set_json"]) else None
    if not set_rec or not set_rec.get("records"):
        sys.exit("no reference set — run voice_collect.py first")

    seen_fp, seen_sha = set(), set()
    accepted, embeddings = [], {}
    for rec in set_rec["records"]:
        src = os.path.join(args.persona_root, rec["path"])
        if not os.path.exists(src):
            append_jsonl(p["cur_log"], {"clip_id": rec["clip_id"], "kept": False,
                                        "reasons": ["file_missing"], "at": now_iso()})
            continue
        if not valid_rights(rec):
            append_jsonl(p["cur_log"], {"clip_id": rec["clip_id"], "kept": False,
                                        "reasons": ["rights_incomplete"], "at": now_iso()})
            continue
        if rec["sha256"] in seen_sha:
            append_jsonl(p["cur_log"], {"clip_id": rec["clip_id"], "kept": False,
                                        "reasons": ["sha256_duplicate"], "at": now_iso()})
            continue
        seen_sha.add(rec["sha256"])
        try:
            wav = decode_audio(src)
        except ValueError as e:
            append_jsonl(p["cur_log"], {"clip_id": rec["clip_id"], "kept": False,
                                        "reasons": [f"undecodable:{e}"], "at": now_iso()})
            continue
        fp = audio_fingerprint(wav)
        if fp in seen_fp:
            append_jsonl(p["cur_log"], {"clip_id": rec["clip_id"], "kept": False,
                                        "reasons": ["fingerprint_duplicate"], "at": now_iso()})
            continue
        seen_fp.add(fp)

        for off, chunk in segments(wav, args.max_clip_s):
            base_q = audio_quality(chunk)
            seg_id = rec["clip_id"] if off == 0.0 else f"{rec['clip_id']}@{int(off)}s"
            reasons = []
            if base_q["duration_s"] < 2.0:
                reasons.append("too_short")
            if base_q["clipping_frac"] > 0.01:
                reasons.append("clipping")
            if base_q["rms"] < 0.005:
                reasons.append("too_quiet")
            emb = None
            if not reasons:
                emb, q = embed_wav(chunk)
                q.update(base_q)
                if q["speech_s"] < args.min_speech_s:
                    reasons.append(f"speech<{args.min_speech_s}s")
            if reasons:
                append_jsonl(p["cur_log"], {"clip_id": seg_id, "kept": False,
                                            "reasons": reasons, "at": now_iso()})
                continue
            line = {
                "clip_id": seg_id,
                "path": rec["path"],
                "offset_s": off,
                "sha256": rec["sha256"],
                "rights": rec["rights"],
                "quality": {k: q[k] for k in ("duration_s", "speech_s", "rms", "clipping_frac")},
                "kept": True,
                "reasons": [],
                "curated_at": now_iso(),
            }
            append_jsonl(p["curated"], line)
            accepted.append(seg_id)
            embeddings[seg_id] = {"emb": [round(float(v), 6) for v in np.asarray(emb)],
                                  "speech_s": q["speech_s"]}

    if not accepted:
        sys.exit("curated set is empty — nothing passed quality/rights filters")

    cache = {"updated_at": now_iso(), "embeddings": embeddings}
    with open(p["emb"], "w", encoding="utf-8") as f:
        json.dump(cache, f)
    print(json.dumps({"ok": True, "curated": len(accepted), "clips": accepted,
                      "internal_only": bool(set_rec.get("internal_only"))}))


if __name__ == "__main__":
    main()

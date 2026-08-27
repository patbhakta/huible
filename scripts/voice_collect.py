#!/usr/bin/env python3
"""COLLECT stage (HU-2151) — consented reference-audio intake.

Mirrors ref_collect.py (image twin) for voice. Reference audio enters through
explicit doors only, each with a rights basis; a clip with no valid rights
record is rejected at intake (fail closed). No rights record → never enters a
reference set.

Source tiers (docs §1):
  onboarding_recording  recorded live with the loved one during onboarding
  family_archive        existing family recordings (rights: consent_by + provided_by)
  voicemail             saved voicemails (rights: consent_by + provided_by)
  licensed              licensed audio (rights: license_ref)
  benchmark_corpus      sitcom-walled corpus (friends-v2 / MELD) — internal
                        benchmarking ONLY: never eligible for production
                        persona voice; marks the whole set internal_only
  synthetic_seed        rights-clean synthetic speakers for R&D calibration

Usage:
  voice_collect.py --persona-root <vault> --audio <path...> \
      --source onboarding_recording --consent-by "client:…:onboarding"
  voice_collect.py --persona-root <vault> --audio meld/*.wav \
      --source benchmark_corpus --corpus-ref "MELD.Raw/chandler" \
      --speaker-filter "Chandler Bing"
"""

import argparse
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from voicepipe_common import (
    audio_quality,
    decode_audio,
    load_json,
    now_iso,
    sha256_of,
    valid_rights,
    vault_paths,
)

SOURCES = {
    "onboarding_recording": {"basis": "onboarding_consent", "internal_only": False},
    "family_archive": {"basis": "client_archive", "internal_only": False},
    "voicemail": {"basis": "client_archive", "internal_only": False},
    "licensed": {"basis": "license", "internal_only": False},
    "benchmark_corpus": {"basis": "benchmark_only", "internal_only": True},
    "synthetic_seed": {"basis": "synthetic", "internal_only": False},
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--persona-root", required=True)
    ap.add_argument("--audio", nargs="+", required=True)
    ap.add_argument("--source", required=True, choices=sorted(SOURCES))
    ap.add_argument("--consent-by",
                    help="who consented (required for onboarding/archive/voicemail)")
    ap.add_argument("--provided-by", help="who provided the file (required for archive/voicemail)")
    ap.add_argument("--license-ref", help="license reference (required for licensed)")
    ap.add_argument("--corpus-ref", help="corpus path/collection (required for benchmark_corpus)")
    ap.add_argument("--speaker-filter", help="only corpus lines from this speaker (benchmark tier)")
    ap.add_argument("--notes", default="")
    args = ap.parse_args()

    meta = SOURCES[args.source]
    p = vault_paths(args.persona_root)
    os.makedirs(p["raw_dir"], exist_ok=True)
    set_json = p["set_json"]
    records = load_json(set_json) if os.path.exists(set_json) else []

    accepted = rejected = 0
    for path in args.audio:
        path = os.path.abspath(path)
        try:
            q = audio_quality(decode_audio(path))
        except (ValueError, OSError) as e:
            print(json.dumps({"ok": False, "file": path, "error": f"undecodable: {e}"}))
            rejected += 1
            continue
        digest = sha256_of(path)
        suffix = digest[:6]
        rec = {
            "clip_id": f"vaudio_{now_iso()[:10].replace('-', '')}_{suffix}",
            "path": os.path.relpath(
                os.path.join(p["raw_dir"], f"{digest[:12]}{os.path.splitext(path)[1]}"),
                args.persona_root),
            "sha256": digest,
            "duration_s": q["duration_s"],
            "source": args.source,
            "rights": {
                "basis": meta["basis"],
                "consent_by": args.consent_by,
                "provided_by": args.provided_by,
                "license_ref": args.license_ref,
                "corpus_ref": args.corpus_ref,
                "internal_only": meta["internal_only"],
            },
            "collected_at": now_iso(),
            "notes": " ".join(
                s for s in (args.notes,
                            f"speaker={args.speaker_filter}" if args.speaker_filter else "")
                if s),
        }
        if not valid_rights(rec):
            print(json.dumps({"ok": False, "file": path,
                               "error": "rights record incomplete — "
                                        "rejected at intake (fail closed)"}))
            rejected += 1
            continue
        dst = os.path.join(args.persona_root, rec["path"])
        shutil.copyfile(path, dst)
        records.append(rec)
        accepted += 1
        print(json.dumps({"ok": True, "clip_id": rec["clip_id"],
                           "duration_s": rec["duration_s"], "source": rec["source"]}))

    meta_rec = {
        "updated_at": now_iso(),
        "records": records,
        "internal_only": any(r["rights"].get("internal_only") for r in records),
    }
    with open(set_json, "w", encoding="utf-8") as f:
        json.dump(meta_rec, f, indent=2)
    print(json.dumps({"ok": True, "accepted": accepted, "rejected": rejected,
                      "internal_only": meta_rec["internal_only"],
                      "set": os.path.relpath(set_json, args.persona_root)}))


if __name__ == "__main__":
    main()

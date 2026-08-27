#!/usr/bin/env python3
"""COLLECT stage (HU-2150) — reference photo intake with provenance.

Copies intake photos into <persona_root>/references/raw/, writes one
reference-set.json record per photo (source, rights, consent). Photos without
a rights basis are rejected at intake (fail closed).

Usage:
  python3 ref_collect.py --persona-root /root/repos/personas/<p> \
      --source consented_upload --consent-by "client:<persona>:onboarding" \
      --photos a.jpg b.jpg [--notes "..."]

  # licensed imagery:
  python3 ref_collect.py --persona-root ... --source licensed \
      --license-ref "stock#123" --photos x.jpg

  # R&D synthetic seed identities ONLY:
  python3 ref_collect.py --persona-root ... --source synthetic_seed --photos s.png
"""

import argparse
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from refpipe_common import append_jsonl, load_json, now_iso, sha256_of, vault_paths


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--persona-root", required=True)
    ap.add_argument("--photos", nargs="+", required=True)
    ap.add_argument("--source", required=True,
                    choices=["consented_upload", "licensed", "synthetic_seed"])
    ap.add_argument("--consent-by", default=None)
    ap.add_argument("--license-ref", default=None)
    ap.add_argument("--notes", default="")
    args = ap.parse_args()

    if args.source == "consented_upload" and not args.consent_by:
        sys.exit("refused: consented_upload requires --consent-by (fail closed)")
    if args.source == "licensed" and not args.license_ref:
        sys.exit("refused: licensed requires --license-ref (fail closed)")

    p = vault_paths(args.persona_root)
    os.makedirs(p["raw_dir"], exist_ok=True)
    set_records = load_json(p["set_json"]) if os.path.exists(p["set_json"]) else []

    taken = 0
    for src in args.photos:
        if not os.path.isfile(src):
            print(f"SKIP (missing): {src}")
            continue
        digest = sha256_of(src)
        if any(r["sha256"] == digest for r in set_records):
            print(f"SKIP (sha256 already collected): {src}")
            continue
        photo_id = "ref_" + now_iso()[:10].replace("-", "") + "_" + digest[:6]
        dst = os.path.join(p["raw_dir"], photo_id + os.path.splitext(src)[1].lower())
        shutil.copy2(src, dst)
        rec = {
            "photo_id": photo_id,
            "path": os.path.relpath(dst, args.persona_root),
            "sha256": digest,
            "source": args.source,
            "rights": {
                "basis": {"consented_upload": "client_upload",
                          "licensed": "license",
                          "synthetic_seed": "synthetic"}[args.source],
                "license_ref": args.license_ref,
                "consent_by": args.consent_by,
                "expires": None,
            },
            "collected_at": now_iso(),
            "notes": args.notes,
        }
        set_records.append(rec)
        print(f"COLLECTED {photo_id} <- {src}")
        taken += 1

    with open(p["set_json"], "w", encoding="utf-8") as f:
        import json
        json.dump(set_records, f, indent=2)
    append_jsonl(p["cur_log"], {
        "event": "collect", "at": now_iso(), "added": taken,
        "photos": args.photos, "source": args.source,
    })
    print(f"OK collected {taken} photo(s); reference set now {len(set_records)}")


if __name__ == "__main__":
    main()

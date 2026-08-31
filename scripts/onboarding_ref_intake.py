#!/usr/bin/env python3
"""Onboarding intake for reference photos (HU-2157 item 1).

The single onboarding-facing door for client reference photos. Wraps the
COLLECT (ref_collect.py) and CURATE (ref_curate.py) stages so an onboarding
operator gets a one-command intake over a consented upload folder, with a
client-readable report of what was accepted, what was rejected and why, and
whether the persona is ready for identity-conditioned generation.

Fail-closed consent (docs/IDENTITY_IMAGE_PIPELINE.md §1):
  consented_upload requires --consent-by (who attests, e.g.
  "client:chandler-bing:onboarding"); licensed requires --license-ref.
  Photos without a rights record never enter a reference set.

Writes references/intake-report.json (latest intake snapshot) and prints a
human-readable summary. The curation audit trail stays in
references/curation-log.jsonl (append-only, never rewritten).

Usage:
  python3 onboarding_ref_intake.py --persona-root /root/repos/personas/<p> \
      --upload-dir /path/to/client-uploads \
      --consent-by "client:<persona>:onboarding" [--min-edge 512]

  # licensed imagery supplied by the client:
  python3 onboarding_ref_intake.py ... --source licensed --license-ref "stock#123"

Exit codes: 0 = intake ran (report written; check "ready" fields), 1 = refused
or no usable photos.
"""

import argparse
import glob
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from refpipe_common import load_json, now_iso, vault_paths

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")

# Production target from the design doc §2: >=1 required, 3-10 diverse recommended.
TARGET_MIN = 1
TARGET_RECOMMENDED_MIN = 3
TARGET_RECOMMENDED_MAX = 10


def run_stage(script, args):
    """Run a sibling stage script; return (returncode, stdout)."""
    proc = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS_DIR, script), *args],
        capture_output=True, text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr


def find_uploads(upload_dir):
    files = []
    for ext in IMAGE_EXTS:
        files.extend(glob.glob(os.path.join(upload_dir, "**", f"*{ext}"), recursive=True))
        files.extend(glob.glob(os.path.join(upload_dir, "**", f"*{ext.upper()}"), recursive=True))
    return sorted(set(files))


def parse_curate_output(stdout):
    """Extract the curate JSON (after the ===GATE_JSON===-style chatter)."""
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("{\"ok\""):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--persona-root", required=True)
    ap.add_argument("--upload-dir", required=True)
    ap.add_argument("--source", default="consented_upload",
                    choices=["consented_upload", "licensed"])
    ap.add_argument("--consent-by", default=None,
                    help="consent attestation, e.g. client:<persona>:onboarding "
                         "(required for consented_upload)")
    ap.add_argument("--license-ref", default=None,
                    help="license identifier (required for licensed)")
    ap.add_argument("--min-edge", type=int, default=512)
    ap.add_argument("--notes", default="")
    args = ap.parse_args()

    if args.source == "consented_upload" and not args.consent_by:
        sys.exit("refused: consented uploads require --consent-by attestation (fail closed)")
    if args.source == "licensed" and not args.license_ref:
        sys.exit("refused: licensed imagery requires --license-ref (fail closed)")
    if not os.path.isdir(args.upload_dir):
        sys.exit(f"refused: upload dir not found: {args.upload_dir}")

    uploads = find_uploads(args.upload_dir)
    if not uploads:
        sys.exit(f"no image files ({', '.join(IMAGE_EXTS)}) under {args.upload_dir}")

    # --- COLLECT ---
    collect_args = ["--persona-root", args.persona_root, "--source", args.source,
                    "--photos", *uploads]
    if args.consent_by:
        collect_args += ["--consent-by", args.consent_by]
    if args.license_ref:
        collect_args += ["--license-ref", args.license_ref]
    if args.notes:
        collect_args += ["--notes", args.notes]
    rc, out = run_stage("ref_collect.py", collect_args)
    print(out, end="")
    if rc != 0:
        sys.exit(1)

    # --- CURATE ---
    rc, out = run_stage("ref_curate.py",
                        ["--persona-root", args.persona_root, "--min-edge", str(args.min_edge)])
    print(out, end="")
    curate = parse_curate_output(out)
    if curate is None:
        sys.exit("curate stage output not parseable — see curation-log.jsonl")

    # --- Report ---
    p = vault_paths(args.persona_root)
    set_records = load_json(p["set_json"]) if os.path.exists(p["set_json"]) else []
    curated_ids = set()
    if os.path.exists(p["curated"]):
        with open(p["curated"], encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    rec = json.loads(line)
                    if rec.get("kept"):
                        curated_ids.add(rec["photo_id"])
    curated_total = len(curated_ids)

    gate_cfg = load_json(p["gate_config"]) if os.path.exists(p["gate_config"]) else None
    gate = {
        "config_present": gate_cfg is not None,
        "production_safe": bool(gate_cfg and gate_cfg.get("production_safe")),
        "threshold": gate_cfg.get("threshold") if gate_cfg else None,
        "note": None if gate_cfg else
            "no gate-config.json — run calibrate_gate.py (human gold set) before any generation",
    }

    report = {
        "at": now_iso(),
        "persona_root": args.persona_root,
        "upload_dir": os.path.abspath(args.upload_dir),
        "source": args.source,
        "consent_by": args.consent_by,
        "license_ref": args.license_ref,
        "uploaded": len(uploads),
        "collected_this_run": curate.get("input", 0),
        "curated_kept_this_run": curate.get("kept", 0),
        "rejected_this_run": {
            pid: reasons for pid, reasons in (curate.get("rejections") or {}).items()
        },
        "reference_set_total": len(set_records),
        "curated_set_total": curated_total,
        "curated_photo_ids": sorted(curated_ids),
        "target": {"min": TARGET_MIN,
                   "recommended_min": TARGET_RECOMMENDED_MIN,
                   "recommended_max": TARGET_RECOMMENDED_MAX},
        "gate": gate,
        "ready_for_conditioning": curated_total >= TARGET_MIN,
        "next_steps": [],
    }
    if curated_total < TARGET_RECOMMENDED_MIN:
        report["next_steps"].append(
            f"curated set is {curated_total} photo(s); ask the client for more "
            f"diverse-angle photos (target {TARGET_RECOMMENDED_MIN}-{TARGET_RECOMMENDED_MAX})")
    if not gate["production_safe"]:
        report["next_steps"].append(
            "gate not production_safe — identity-conditioned generation stays "
            "OFF until the human gold-set calibration passes (calibrate_gate.py, no --force)")
    if report["ready_for_conditioning"] and gate["production_safe"]:
        report["next_steps"].append(
            "ready: generate via ref_generate_validated.py or the vault-media Kestra flow")

    with open(os.path.join(os.path.dirname(p["set_json"]), "intake-report.json"),
              "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print("\n=== INTAKE REPORT ===")
    print(json.dumps(report, indent=2))
    print(f"\nreport: {os.path.join(os.path.dirname(p['set_json']), 'intake-report.json')}")
    if curate.get("kept", 0) == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()

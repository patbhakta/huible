#!/usr/bin/env python3
"""Identity-conditioned generation with the production gate enforced (HU-2157).

Chains CONDITION (generate_image.py --ref-image, Kontext) → VALIDATE
(ref_gate.py) → REGISTRY (ref_registry.py append) for ONE output, with
retries, and refuses to generate at all unless:

  - a curated reference set exists (references/embeddings.json), and
  - references/gate-config.json exists and is production_safe=true,
    unless --allow-unsafe-gate is passed (R&D synthetic seeds ONLY).

This is the production entry point the vault-media Kestra flow calls; the
prompt describes only the scene/pose CHANGE — identity comes from the
reference, never from the prompt (text-to-image stays refused by
generate_image.py for persona assets).

Usage:
  python3 ref_generate_validated.py --persona-root /root/repos/personas/<p> \
      --prompt "same person, hiking on a mountain trail at sunrise" \
      [--out media/images/<name>.png] [--ref-photo-id ref_...] [--retry 2]
"""

import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from refpipe_common import load_json, now_iso, vault_paths

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
GATE_JSON_MARKER = "===GATE_JSON==="


def run_stage(script, args):
    proc = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS_DIR, script), *args],
        capture_output=True, text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


def pick_reference(p, ref_photo_id=None):
    """Best curated reference (largest face_w_px * face_area_frac) or by id."""
    best = None
    with open(p["curated"], encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if not rec.get("kept"):
                continue
            if ref_photo_id:
                if rec["photo_id"] == ref_photo_id:
                    return rec
                continue
            q = rec.get("quality", {})
            score = q.get("face_w_px", 0) * q.get("face_area_frac", 0)
            if best is None or score > best[0]:
                best = (score, rec)
    if ref_photo_id:
        sys.exit(f"refused: photo_id {ref_photo_id} not in curated set")
    if best is None:
        sys.exit("refused: curated set is empty — run onboarding_ref_intake.py first")
    return best[1]


def gate_verdict(stdout):
    try:
        payload = stdout.split(GATE_JSON_MARKER, 1)[1]
        return json.loads(payload.strip().splitlines()[0])
    except (IndexError, json.JSONDecodeError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--persona-root", required=True)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--ref-photo-id", default=None)
    ap.add_argument("--retry", type=int, default=0,
                    help="extra generation attempts after a gate reject")
    ap.add_argument("--strength", type=float, default=1.0)
    ap.add_argument("--allow-unsafe-gate", action="store_true",
                    help="R&D only: allow generation with a non-production_safe gate")
    args = ap.parse_args()

    p = vault_paths(args.persona_root)
    if not os.path.exists(p["emb"]) or not load_json(p["emb"]):
        sys.exit("refused: no curated reference embeddings — run onboarding_ref_intake.py first")

    if not os.path.exists(p["gate_config"]):
        sys.exit("refused: no gate config — run calibrate_gate.py first "
                 "(thresholds are measured, never guessed)")
    cfg = load_json(p["gate_config"])
    if not cfg.get("production_safe") and not args.allow_unsafe_gate:
        sys.exit("refused: gate is not production_safe (synthetic-only calibration) — "
                 "promote via the human gold set (calibrate_gate.py WITHOUT --force) "
                 "before generating for a client persona")

    ref = pick_reference(p, args.ref_photo_id)
    ref_path = os.path.join(args.persona_root, ref["path"])
    out = args.out or os.path.join(
        args.persona_root, "media", "images",
        now_iso().replace(":", "").replace("+", "_") + "_kontext.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)

    attempts = 1 + max(0, args.retry)
    last = None
    for attempt in range(1, attempts + 1):
        target = out if attempt == 1 else out.replace(".png", f"_r{attempt}.png")
        rc, gen_out, gen_err = run_stage("generate_image.py", [
            "--prompt", args.prompt, "--out", target, "--ref-image", ref_path,
            "--model", "flux-pro-kontext", "--strength", str(args.strength),
        ])
        print(gen_out.strip() or gen_err.strip())
        if rc != 0:
            last = {"attempt": attempt, "stage": "generate", "error": gen_err.strip()}
            continue

        rc, gate_out, gate_err = run_stage("ref_gate.py",
                                           ["--persona-root", args.persona_root,
                                            "--image", target, "--json"])
        verdict = gate_verdict(gate_out or "")
        if verdict is None:
            print(gate_err.strip())
            last = {"attempt": attempt, "stage": "gate", "error": "unparseable gate output"}
            continue

        result = (verdict.get("results") or [{}])[0]
        if verdict.get("ok"):
            verdict_path = target + ".verdict.json"
            with open(verdict_path, "w", encoding="utf-8") as f:
                json.dump(result, f)
            rc, reg_out, reg_err = run_stage("ref_registry.py", [
                "append", "--persona-root", args.persona_root,
                "--image", target, "--prov", target + ".prov.json",
                "--gate-verdict", verdict_path,
            ])
            print(reg_out.strip() or reg_err.strip())
            if rc != 0:
                sys.exit(1)
            print(json.dumps({"ok": True, "asset": target,
                              "score": result.get("score"),
                              "threshold": result.get("threshold"),
                              "references_used": sorted(result.get("per_ref", {})),
                              "attempt": attempt}, indent=2))
            return
        last = {"attempt": attempt, "stage": "gate", "score": result.get("score"),
                "threshold": result.get("threshold")}
        print(f"REJECT attempt {attempt}: score={result.get('score')} "
              f"< thr={result.get('threshold')} — regenerating")

    sys.exit(f"gate rejected all {attempts} attempt(s): {json.dumps(last)}")


if __name__ == "__main__":
    main()

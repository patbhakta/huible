#!/usr/bin/env python3
"""One-off R&D driver for the HU-2150 gold calibration + acceptance run (2026-08-27).

Builds a rights-clean synthetic gold set (3 identities x 8 scene-varying
Kontext outputs), scores pos/neg pairs, calibrates the gate, then runs the
acceptance protocol: 1 curated reference set + 3 UNSEEN prompts -> 3 outputs
that must all pass the gate, then registers them.

Identities are synthetic seeds (flux/schnell solo portraits) — R&D only,
rights.basis=synthetic. The pipeline itself never uses text-to-image for
persona assets.

Run inside the experiment dir:
  python3 run_experiment.py --stage build    # FAL generations (slow, costs ~$1.5)
  python3 run_experiment.py --stage calibrate
  python3 run_experiment.py --stage acceptance
"""

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = "/root/repos/huible"
SCRIPTS = os.path.join(REPO, "scripts")
PERSONA = os.path.join(HERE, "persona")  # scratch "vault" for this experiment

SEED_PROMPTS = {
    "ident-a": ("solo portrait photograph of a woman in her 60s with short silver "
                "hair and round glasses, plain studio background, soft even "
                "lighting, looking at camera, photorealistic, neutral expression"),
    "ident-b": ("solo portrait photograph of a man in his 30s with a short black "
                "beard and buzzcut hair, plain gray studio background, soft even "
                "lighting, looking at camera, photorealistic, neutral expression"),
    "ident-c": ("solo portrait photograph of a woman in her 40s with long curly "
                "auburn hair, plain warm beige studio background, soft even "
                "lighting, looking at camera, photorealistic, neutral expression"),
}

# Scene-varying calibration prompts — "same person, now ..."; identity stays put.
CALIB_PROMPTS = [
    "the same person sitting at a kitchen table in the morning, drinking coffee, "
    "warm window light, candid photorealistic photo",
    "the same person hiking on a mountain trail at sunrise, wearing running "
    "clothes and a light jacket, wide shot, photorealistic",
    "the same person laughing at a backyard barbecue, holding a plate, golden "
    "hour, candid photorealistic photo",
    "the same person reading a book in a cozy armchair by a fireplace, warm lamp "
    "light, photorealistic",
    "the same person at a beach boardwalk in summer clothes, sunny afternoon, "
    "candid photorealistic photo",
    "the same person wearing a winter coat and scarf in falling snow, city "
    "street at dusk, photorealistic",
    "the same person cooking in a home kitchen, apron on, steam rising, "
    "natural light, candid photorealistic photo",
    "the same person in a garden holding fresh flowers, soft overcast light, "
    "photorealistic portrait",
]

# Acceptance prompts — disjoint from CALIB_PROMPTS (unseen during calibration).
ACCEPT_PROMPTS = [
    "the same person riding a bicycle on a park path in autumn, leaves on the "
    "trees, candid photorealistic photo",
    "the same person at an art gallery opening, smart casual clothes, indoor "
    "gallery lighting, photorealistic",
    "the same person kayaking on a calm lake at midday, wearing a life vest, "
    "wide shot, photorealistic",
]

GEN = os.path.join(SCRIPTS, "generate_image.py")
PYTHON = sys.executable


def sh(*cmd, env=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    print("+", " ".join(str(c) for c in cmd), flush=True)
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, env=e)
    if r.returncode != 0:
        print(r.stdout)
        print(r.stderr, file=sys.stderr)
        sys.exit(f"command failed rc={r.returncode}")
    return r.stdout


def fal_key():
    with open("/opt/kestra/kestra.env") as f:
        for line in f:
            if line.startswith("FAL_KEY="):
                return line.strip().split("=", 1)[1]
    sys.exit("FAL_KEY not found")


def stage_build():
    env = {"FAL_KEY": fal_key()}
    for ident, prompt in SEED_PROMPTS.items():
        d = os.path.join(HERE, "gold", ident)
        os.makedirs(os.path.join(d, "outputs"), exist_ok=True)
        ref = os.path.join(d, "reference.png")
        if not os.path.exists(ref):
            sh(PYTHON, GEN, "--prompt", prompt, "--out", ref, env=env)
        for i, cp in enumerate(CALIB_PROMPTS):
            out = os.path.join(d, "outputs", f"out-{i:02d}.png")
            if os.path.exists(out):
                continue
            sh(PYTHON, GEN, "--prompt", cp, "--ref-image", ref, "--out", out,
               "--model", "flux-pro-kontext", env=env)
    # Acceptance outputs for identity A (generated in the acceptance stage)
    print(json.dumps({"ok": True, "gold": "built", "identities": sorted(SEED_PROMPTS)}))


def stage_collect_curate():
    """Exercise COLLECT+CURATE end-to-end on identity A (+dedupe proof)."""
    env = {"FAL_KEY": fal_key()}
    os.makedirs(PERSONA, exist_ok=True)
    a_ref = os.path.join(HERE, "gold", "ident-a", "reference.png")
    out = sh(PYTHON, os.path.join(SCRIPTS, "ref_collect.py"),
             "--persona-root", PERSONA, "--source", "synthetic_seed",
             "--photos", a_ref,
             os.path.join(HERE, "dedupe-proof", "duplicate-copy.png"),
             os.path.join(HERE, "dedupe-proof", "slightly-cropped.png"),
             env=env)
    print(out)
    out = sh(PYTHON, os.path.join(SCRIPTS, "ref_curate.py"), "--persona-root", PERSONA)
    print(out)


def stage_calibrate():
    # --force: synthetic R&D gold set is intentionally below the 50-pair
    # production rule; the config records production_safe=false (Hume-gate
    # pattern: human gold set still required before client use).
    out = sh(PYTHON, os.path.join(SCRIPTS, "calibrate_gate.py"),
             "--persona-root", PERSONA, "--gold-dir", os.path.join(HERE, "gold"),
             "--force")
    print(out)


def stage_acceptance():
    env = {"FAL_KEY": fal_key()}
    os.makedirs(os.path.join(HERE, "acceptance"), exist_ok=True)
    ref = os.path.join(HERE, "gold", "ident-a", "reference.png")
    verdicts = []
    for i, prompt in enumerate(ACCEPT_PROMPTS):
        out = os.path.join(HERE, "acceptance", f"accept-{i}.png")
        if not os.path.exists(out):
            sh(PYTHON, GEN, "--prompt", prompt, "--ref-image", ref, "--out", out,
               "--model", "flux-pro-kontext", env=env)
        vf = out + ".verdict.json"
        r = subprocess.run([PYTHON, os.path.join(SCRIPTS, "ref_gate.py"),
                            "--persona-root", PERSONA, "--image", out, "--json"],
                           capture_output=True, text=True, env=env)
        raw = r.stdout
        marker = raw.find("===GATE_JSON===")
        if marker == -1 and not raw.strip():
            print(raw, r.stderr)
            sys.exit(f"gate invocation failed for {out}")
        wrapped = json.loads(raw[marker + len("===GATE_JSON==="):] if marker != -1 else raw)
        v = wrapped["results"][0]
        with open(vf, "w", encoding="utf-8") as f:
            json.dump(v, f, indent=2)
        verdicts.append(v)
        if not v["ok"]:
            print(f"ACCEPTANCE FAILED at {out}")
            sys.exit(2)
        sh(PYTHON, os.path.join(SCRIPTS, "ref_registry.py"), "append",
           "--persona-root", PERSONA, "--image", out, "--prov", out + ".prov.json",
           "--gate-verdict", vf, env=env)
    sh(PYTHON, os.path.join(SCRIPTS, "ref_registry.py"), "verify",
       "--persona-root", PERSONA, env=env)
    print(json.dumps({"ok": True, "acceptance": "PASSED",
                      "scores": [v["score"] for v in verdicts]}))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True,
                    choices=["build", "collect-curate", "calibrate", "acceptance"])
    a = ap.parse_args()
    {"build": stage_build, "collect-curate": stage_collect_curate,
     "calibrate": stage_calibrate, "acceptance": stage_acceptance}[a.stage]()

#!/usr/bin/env python3
"""Clone-output gold set builder (HU-2163) — prove the speaker gate on CLONES.

Docs §3 promotion step (a): before any production persona voice, the
natural-speech threshold must be re-proven on actual clone outputs. This
driver batch-generates clones with the zero-API-spend local adapter
(chatterbox-local, both std and turbo variants) conditioned on the
LibriSpeech gold-set reference clips (open licence, OpenSLR 12), then
scores every clone against every reference with the production embedder
(ECAPA-TDNN 192-d, cosine max — same rule as voice_gate.py).

Doctrine: experiment-only artifacts under experiments/voice-pipeline/ —
never persona vault assets, never the registry. Zero API spend. The
MELD/Chandler set is internal_only and is NOT touched by this tool.

Usage (two venvs):
  # generate (venv with chatterbox-tts + torch):
  clone_goldset.py gen --goldset-dir <2026-08-27-gold-calibration/gold-set> \
      --out-dir <2026-08-31-clone-goldset> --variant turbo --texts 3
  # score (repo .venv with speechbrain, scripts/ on sys.path):
  clone_goldset.py score --exp-dir <2026-08-31-clone-goldset> \
      --goldset-dir <.../gold-set> --baseline-threshold 0.5049
"""

import argparse
import glob
import json
import os
import subprocess
import sys
import time

SPEAKER_DIRS_SENTINEL = "references"


def now_iso():
    import datetime
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


def wav_duration_s(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path],
        capture_output=True, timeout=60)
    if out.returncode != 0:
        sys.exit(f"ffprobe failed on {path}")
    return float(out.stdout.strip())


def speaker_refs(goldset_dir):
    """{speaker: {clip_name: abs_path}} from gold-set/<spk>/references/*.wav."""
    speakers = {}
    for spk_dir in sorted(glob.glob(os.path.join(goldset_dir, "*", SPEAKER_DIRS_SENTINEL))):
        spk = os.path.basename(os.path.dirname(spk_dir))
        refs = sorted(glob.glob(os.path.join(spk_dir, "*.wav")))
        if refs:
            speakers[spk] = {os.path.basename(r): os.path.abspath(r) for r in refs}
    if not speakers:
        sys.exit(f"no speakers with references/ under {goldset_dir}")
    return speakers


# Same texts for every speaker+variant — controls the text variable so score
# spread measures voice identity, not content. Deliberately mixed domains:
# narrative, conversational, question, exclamation, mundane short line.
TEXTS = [
    "It was a bright cold day in April, and the clocks were striking thirteen.",
    "Could you hand me that notebook before you leave the room?",
    "What a ridiculous way to spend a Tuesday afternoon!",
    "She said the meeting had been moved, but nobody told me anything about it.",
    "The train arrives at six.",
]


def load_chatterbox(variant):
    import torch
    device = "cuda" if torch.cuda.is_available() else "cpu"
    if variant == "turbo":
        from chatterbox.tts_turbo import ChatterboxTurboTTS
        return ChatterboxTurboTTS.from_pretrained(device=device), \
            "ResembleAI/chatterbox-turbo", device
    from chatterbox.tts import ChatterboxTTS
    return ChatterboxTTS.from_pretrained(device=device), \
        "ResembleAI/chatterbox", device


def cmd_gen(args):
    speakers = speaker_refs(args.goldset_dir)
    print(f"[gen] speakers: {', '.join(speakers)} | variant={args.variant} "
          f"| texts/speaker={args.texts}")
    model, repo, device = load_chatterbox(args.variant)
    try:
        from importlib.metadata import version
        pkg_version = version("chatterbox-tts")
    except Exception:
        pkg_version = "unknown"
    os.makedirs(args.out_dir, exist_ok=True)
    rows = []
    for spk, refs in speakers.items():
        # conditioning policy mirrors voice_clone._chatterbox_prompt_clip:
        # the reference clip with the most signal (longest duration here).
        prompt_name = max(refs, key=lambda n: wav_duration_s(refs[n]))
        prompt_path = refs[prompt_name]
        for ti in range(args.texts):
            text = TEXTS[ti % len(TEXTS)]
            stem = f"clone-{spk}-{args.variant}-{ti + 1:02d}"
            out_wav = os.path.join(args.out_dir, stem + ".wav")
            if os.path.exists(out_wav) and os.path.exists(out_wav + ".prov.json"):
                print(f"[gen] {stem} exists — skipping (resumable)")
                rows.append({"asset": stem, "speaker": spk,
                             "variant": args.variant, "latency_s": None})
                continue
            t0 = time.time()
            if args.variant == "turbo":
                # 0.1.7: turbo ignores exaggeration/cfg_weight — do not claim them
                wav = model.generate(text, audio_prompt_path=prompt_path)
                controls = {"controls_applied": False,
                            "note": "turbo ignores exaggeration/cfg_weight "
                                    "(chatterbox-tts 0.1.7)"}
            else:
                wav = model.generate(text, audio_prompt_path=prompt_path,
                                     exaggeration=0.5, cfg_weight=0.5)
                controls = {"exaggeration": 0.5, "cfg_weight": 0.5,
                            "controls_applied": True}
            import torchaudio
            torchaudio.save(out_wav, wav, model.sr)
            prov = {"model": "chatterbox-local",
                    "model_version": f"{repo} {args.variant} "
                                     f"(chatterbox-tts {pkg_version})",
                    "references_used": [prompt_name],
                    "speaker": spk, "device": device, "controls": controls,
                    "watermark": "perth-implicit (built into every Chatterbox output)",
                    "privacy": "self-hosted — reference audio never leaves our boundary",
                    "experiment": "HU-2163 clone-output gold set (experiment-only, "
                                  "not a persona asset)",
                    "text": text, "latency_s": round(time.time() - t0, 2),
                    "bytes": os.path.getsize(out_wav), "generated_at": now_iso()}
            with open(out_wav + ".prov.json", "w", encoding="utf-8") as f:
                json.dump(prov, f, indent=2)
            rows.append({"asset": stem, "speaker": spk, "variant": args.variant,
                         "latency_s": prov["latency_s"]})
            print(f"[gen] {stem} latency={prov['latency_s']}s "
                  f"bytes={prov['bytes']}")
    print(json.dumps({"ok": True, "generated": len(rows)}, indent=2))


def embed_refs_and_clones(exp_dir, goldset_dir):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from voicepipe_common import embed_file  # noqa: E402
    ref_emb = {}
    for spk, refs in speaker_refs(goldset_dir).items():
        for name, path in refs.items():
            emb, q = embed_file(path)
            ref_emb[(spk, name)] = emb
    clones = []
    for prov_path in sorted(glob.glob(os.path.join(exp_dir, "*.wav.prov.json"))):
        prov = json.load(open(prov_path, encoding="utf-8"))
        wav = prov_path[:-len(".prov.json")]
        emb, q = embed_file(wav)
        clones.append({"asset": os.path.basename(wav)[:-4],
                       "speaker": prov["speaker"],
                       "variant": prov["model_version"].split(" ")[1],
                       "emb": emb, "quality": q})
    return ref_emb, clones


def cmd_score(args):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from voicepipe_common import cosine, GATE_ID  # noqa: E402
    ref_emb, clones = embed_refs_and_clones(args.exp_dir, args.goldset_dir)
    speakers = sorted({spk for spk, _ in ref_emb})
    by_speaker = {s: {n: e for (sp, n), e in ref_emb.items() if sp == s}
                  for s in speakers}

    pairs = []  # every (clone, reference) cosine — full distribution
    for c in clones:
        for spk in speakers:
            for name, emb in by_speaker[spk].items():
                score = cosine(c["emb"], emb)
                pairs.append({"clone": c["asset"], "clone_speaker": c["speaker"],
                              "clone_variant": c["variant"], "ref_speaker": spk,
                              "ref": name, "score": round(score, 4),
                              "label": "pos" if spk == c["speaker"] else "neg"})
        # gate-equivalent score per (clone, speaker): max over that speaker's refs
        own = max(p["score"] for p in pairs
                  if p["clone"] == c["asset"] and p["ref_speaker"] == c["speaker"])
        cross = {spk: max(p["score"] for p in pairs
                          if p["clone"] == c["asset"] and p["ref_speaker"] == spk)
                 for spk in speakers if spk != c["speaker"]}

    pos = [p["score"] for p in pairs if p["label"] == "pos"]
    neg = [p["score"] for p in pairs if p["label"] == "neg"]
    # gate-equivalent rows: clone vs own refs (pos) and clone vs each other
    # speaker's refs (neg) — max cosine, the voice_gate.py rule
    gate_rows = []
    for c in clones:
        own_max = max(p["score"] for p in pairs
                      if p["clone"] == c["asset"] and p["ref_speaker"] == c["speaker"])
        gate_rows.append({"clone": c["asset"], "variant": c["variant"],
                          "gate_score": own_max, "label": "pos"})
        for spk in speakers:
            if spk == c["speaker"]:
                continue
            cross_max = max(p["score"] for p in pairs
                            if p["clone"] == c["asset"] and p["ref_speaker"] == spk)
            gate_rows.append({"clone": c["asset"], "variant": c["variant"],
                              "gate_score": cross_max, "label": "neg",
                              "vs_speaker": spk})
    gpos = [r["gate_score"] for r in gate_rows if r["label"] == "pos"]
    gneg = [r["gate_score"] for r in gate_rows if r["label"] == "neg"]

    def dist(v):
        return {"n": len(v), "min": round(min(v), 4), "max": round(max(v), 4),
                "mean": round(sum(v) / len(v), 4)}

    separated = min(gpos) > max(gneg)
    threshold = round((min(gpos) + max(gneg)) / 2, 4) if separated else None
    summary = {
        "experiment": "HU-2163 clone-output gold set",
        "gate": GATE_ID,
        "clones": len(clones),
        "per_variant": {v: sum(1 for c in clones if c["variant"] == v)
                        for v in {c["variant"] for c in clones}},
        "speakers": speakers,
        "pairs_all": {"pos": dist(pos), "neg": dist(neg)},
        "gate_equivalent_max_cosine": {"pos": dist(gpos), "neg": dist(gneg)},
        "separated": separated,
        "clone_calibrated_threshold": threshold,
        "baseline": {
            "threshold": args.baseline_threshold,
            "note": "LibriSpeech natural-speech ECAPA threshold "
                    "(2026-08-31 recalibration, HU-2160)",
            "tpr_at_baseline": round(
                sum(1 for s in gpos if s >= args.baseline_threshold) / len(gpos), 4),
            "fpr_at_baseline": round(
                sum(1 for s in gneg if s >= args.baseline_threshold) / len(gneg), 4),
        },
        "protocol": "positives = (clone, own-speaker refs) max-cosine; "
                    "negatives = (clone, each other speaker's refs) max-cosine — "
                    "the voice_gate.py scoring rule on CLONE outputs",
        "scored_at": now_iso(),
    }
    with open(os.path.join(args.exp_dir, "gold-pairs.jsonl"), "w",
              encoding="utf-8") as f:
        for p in pairs:
            f.write(json.dumps(p) + "\n")
    with open(os.path.join(args.exp_dir, "gate-rows.jsonl"), "w",
              encoding="utf-8") as f:
        for r in gate_rows:
            f.write(json.dumps(r) + "\n")
    with open(os.path.join(args.exp_dir, "summary.json"), "w",
              encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    if separated:
        cfg = {"gate": GATE_ID, "passed": True,
               "threshold": threshold,
               "basis": "clone-output gold set (HU-2163)",
               "distributions": summary["gate_equivalent_max_cosine"],
               "clones": len(clones), "production_safe": False,
               "internal_only": False, "calibrated_at": now_iso()}
        with open(os.path.join(args.exp_dir, "clone-gate-config.json"), "w",
                  encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    print(json.dumps(summary, indent=2))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gen")
    g.add_argument("--goldset-dir", required=True)
    g.add_argument("--out-dir", required=True)
    g.add_argument("--variant", choices=["std", "turbo"], required=True)
    g.add_argument("--texts", type=int, default=3,
                   help="texts per speaker for this variant")
    g.set_defaults(func=cmd_gen)
    s = sub.add_parser("score")
    s.add_argument("--exp-dir", required=True)
    s.add_argument("--goldset-dir", required=True)
    s.add_argument("--baseline-threshold", type=float, default=0.5049)
    s.set_defaults(func=cmd_score)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

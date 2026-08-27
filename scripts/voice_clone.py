#!/usr/bin/env python3
"""CLONE stage (HU-2151) — reference-audio voice cloning (no prebuilt voices).

The voice is conditioned on N seconds of reference audio from the curated
set, never on a prompt-text voice name. Prebuilt voices cannot serve persona
assets (Pat verdict Aug 27); this tool has no prebuilt mode at all.

Adapters:
  elevenlabs-ivc  Instant Voice Cloning (production path, API spend).
                  SPEND RULE: refuses unless (a) references/voice-gate-config.json
                  exists and passed calibration, (b) --allow-spend is explicit,
                  (c) ELEVENLABS_API_KEY is set. Zero API spend on persona
                  voice until the gate exists and passes.
  chatterbox-local Chatterbox TTS (Resemble AI, MIT), self-hosted, ZERO API
                  spend — grief audio never leaves our boundary. Voice is
                  conditioned on one curated reference clip per generation
                  (audio_prompt_path); no clone-creation step, no prebuilt
                  mode. Variants (chatterbox-tts 0.1.7): std
                  (ResembleAI/chatterbox, 500M) and turbo
                  (ResembleAI/chatterbox-turbo, 350M). Controls
                  --exaggeration / --cfg-weight are provenance-logged; every
                  output carries Resemble's built-in Perth watermark.
                  internal_only sets still require --benchmark-only
                  (doctrine, docs §1/§5).
  xtts-local      Coqui XTTS v2, local, zero API spend (CPML licence — R&D
                  benchmarking only). Requires coqui-tts + model download.
  openvoice-local OpenVoice v2 tone-color clone, local, zero API spend
                  (MIT). Requires openvoice package + checkpoints.

Every generation writes a <out>.prov.json sidecar (references used, cloning
model+version, text, latency, bytes) so the registry never depends on memory
of invocation flags. Outputs MUST then pass voice_gate.py before registry.

Usage:
  voice_clone.py --persona-root <vault> --adapter elevenlabs-ivc \
      --text "…" --out media/voice/line1.wav [--allow-spend]
"""

import argparse
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from voicepipe_common import load_json, now_iso, vault_paths

ELEVEN_API = "https://api.elevenlabs.io/v1"


def spend_gate(persona_root, allow_spend, benchmark_only):
    """Fail closed unless the calibrated gate exists and passed (docs §5).

    internal_only sets (benchmark corpus, e.g. friends-v2/MELD for
    Persona-0) may drive internal benchmarking ONLY, and even then require
    the explicit dual override --allow-spend --benchmark-only.
    """
    cfg_path = vault_paths(persona_root)["gate_config"]
    if not os.path.exists(cfg_path):
        return None, ("no voice-gate-config.json — gate does not exist yet; "
                      "ZERO persona-voice spend allowed")
    cfg = load_json(cfg_path)
    if not cfg.get("passed"):
        return None, ("gate config exists but calibration did not pass; "
                      "ZERO persona-voice spend allowed")
    if cfg.get("internal_only") and not benchmark_only:
        return None, ("reference set is internal_only (benchmark corpus) — "
                      "internal benchmarking requires --benchmark-only; "
                      "production cloning is never permitted on this set")
    if not allow_spend:
        return None, "spend rule: pass --allow-spend explicitly once the gate exists and passes"
    return cfg, None


def clone_elevenlabs(persona_root, text, out, allow_spend, model_version, t0,
                     benchmark_only=False):
    cfg, err = spend_gate(persona_root, allow_spend, benchmark_only)
    if err:
        sys.exit(f"REFUSED: {err}")
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        sys.exit("REFUSED: ELEVENLABS_API_KEY not set")
    if not os.path.exists(vault_paths(persona_root)["emb"]):
        sys.exit("no curated set — run voice_curate.py first")

    refs = cfg["references_used"]
    # 1) create/reuse the cloned voice from curated reference audio
    voice_id = os.environ.get("ELEVENLABS_VOICE_ID")  # reuse an existing clone
    created = False
    if not voice_id:
        parts = []
        for r in refs:
            with open(os.path.join(persona_root, r), "rb") as fh:
                parts.append((os.path.basename(r), fh.read()))
        boundary = "----huiblevoicepipe"
        body, fields = b"", {"name": f"persona-{os.path.basename(persona_root)}",
                             "remove_background_noise": "true"}
        for k, v in fields.items():
            body += (f"--{boundary}\r\nContent-Disposition: form-data; "
                     f"name=\"{k}\"\r\n\r\n{v}\r\n").encode()
        for name, data in parts:
            body += (f"--{boundary}\r\nContent-Disposition: form-data; "
                     f"name=\"files\"; filename=\"{name}\"\r\n"
                     f"Content-Type: audio/wav\r\n\r\n").encode() + data + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        req = urllib.request.Request(
            f"{ELEVEN_API}/voices/add", data=body, method="POST",
            headers={"xi-api-key": api_key,
                     "Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(req, timeout=180) as resp:
            voice_id = json.loads(resp.read())["voice_id"]
        created = True

    # 2) TTS with the cloned voice
    req = urllib.request.Request(
        f"{ELEVEN_API}/text-to-speech/{voice_id}?output_format=wav",
        data=json.dumps({"text": text, "model_id": model_version}).encode(),
        headers={"xi-api-key": api_key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        audio = resp.read()
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "wb") as f:
        f.write(audio)
    return {"model": "elevenlabs-ivc", "model_version": model_version,
            "voice_id": voice_id, "voice_created": created, "references_used": refs}


def clone_local(persona_root, text, out, adapter):
    """Local zero-spend adapters: documented stubs with lazy imports."""
    if not os.path.exists(vault_paths(persona_root)["emb"]):
        sys.exit("no curated set — run voice_curate.py first")
    if adapter == "xtts-local":
        sys.exit("xtts-local requires the coqui-tts package + XTTS v2 checkpoints; "
                 "CPML licence — R&D benchmarking only, no commercial use. "
                 "Install path: docs/IDENTITY_VOICE_PIPELINE.md §3")
    if adapter == "openvoice-local":
        sys.exit("openvoice-local requires the openvoice package + v2 checkpoints "
                 "(MIT licence). Install path: docs/IDENTITY_VOICE_PIPELINE.md §3")
    sys.exit(f"unknown adapter {adapter}")


def _chatterbox_prompt_clip(persona_root, prompt_clip):
    """Pick the conditioning clip from the curated set.

    Chatterbox conditions each generation on ONE reference wav
    (audio_prompt_path) — no multi-clip upload, no voice-creation step.
    Default policy: the curated clip with the most speech (most conditioning
    signal); --prompt-clip overrides with an explicit clip_id.
    """
    p = vault_paths(persona_root)
    rows = []
    with open(p["curated"], encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    rows = [r for r in rows if r.get("kept")]
    if not rows:
        sys.exit("no curated set — run voice_curate.py first")
    if prompt_clip:
        match = [r for r in rows if r["clip_id"] == prompt_clip]
        if not match:
            sys.exit(f"--prompt-clip {prompt_clip} not in curated set")
        row = match[0]
    else:
        row = max(rows, key=lambda r: r.get("quality", {}).get("speech_s", 0))
    return row["clip_id"], os.path.join(persona_root, row["path"])


def clone_chatterbox(persona_root, text, out, variant, exaggeration, cfg_weight,
                     prompt_clip, benchmark_only, t0):
    """Chatterbox local clone — MIT, self-hosted, ZERO API spend (docs §2).

    Voice = conditioned on curated reference audio (audio_prompt_path),
    never on a voice name or prompt text. Grief audio stays in-house: no
    third-party API, no upload step. Expression controls are logged in the
    provenance sidecar; every Chatterbox output carries Resemble's built-in
    Perth watermark (detected with resemble-perth).
    """
    # Doctrine guard (mirrors the elevenlabs internal_only rule, minus spend):
    # benchmark-corpus sets may drive internal benchmarking ONLY.
    cfg_path = vault_paths(persona_root)["gate_config"]
    if os.path.exists(cfg_path):
        cfg = load_json(cfg_path)
        if cfg.get("internal_only") and not benchmark_only:
            sys.exit("REFUSED: reference set is internal_only (benchmark corpus) — "
                     "internal benchmarking requires --benchmark-only; production "
                     "cloning is never permitted on this set")
    try:
        import torch
        import torchaudio
    except ImportError:
        sys.exit("chatterbox-local requires torch + torchaudio (CPU wheel ok): "
                 "scripts/requirements-voicepipe.txt")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    gen_kwargs = {"exaggeration": exaggeration, "cfg_weight": cfg_weight}
    try:
        if variant == "turbo":
            from chatterbox.tts_turbo import ChatterboxTurboTTS
            model = ChatterboxTurboTTS.from_pretrained(device=device)
            repo = "ResembleAI/chatterbox-turbo"
        else:
            from chatterbox.tts import ChatterboxTTS
            model = ChatterboxTTS.from_pretrained(device=device)
            repo = "ResembleAI/chatterbox"
    except ImportError:
        sys.exit("chatterbox-local requires the chatterbox-tts package "
                 "(pip install chatterbox-tts, MIT licence). "
                 "Install path: docs/IDENTITY_VOICE_PIPELINE.md §2")
    clip_id, prompt_path = _chatterbox_prompt_clip(persona_root, prompt_clip)
    try:
        wav = model.generate(text, audio_prompt_path=prompt_path, **gen_kwargs)
    except TypeError:
        wav = model.generate(text, audio_prompt_path=prompt_path)
        gen_kwargs = {"controls_applied": False,
                      "note": "variant generate() rejected expression controls"}
    if variant == "turbo" and "controls_applied" not in gen_kwargs:
        # chatterbox-tts 0.1.7: turbo accepts the kwargs but logs
        # "CFG, min_p and exaggeration are not supported by Turbo version
        # and will be ignored" — provenance must not claim they were applied.
        gen_kwargs = {"requested": {"exaggeration": exaggeration,
                                    "cfg_weight": cfg_weight},
                      "controls_applied": False,
                      "note": "turbo ignores exaggeration/cfg_weight "
                              "(chatterbox-tts 0.1.7 runtime warning)"}
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    torchaudio.save(out, wav, model.sr)
    try:
        from importlib.metadata import version
        pkg_version = version("chatterbox-tts")
    except Exception:
        pkg_version = "unknown"
    prov = {"model": "chatterbox-local",
            "model_version": f"{repo} {variant} (chatterbox-tts {pkg_version})",
            "references_used": [clip_id], "device": device,
            "controls": gen_kwargs,
            "watermark": "perth-implicit (built into every Chatterbox output)",
            "privacy": "self-hosted — reference audio never leaves our boundary"}
    return prov


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--persona-root", required=True)
    ap.add_argument("--adapter", default="elevenlabs-ivc",
                    choices=["elevenlabs-ivc", "chatterbox-local",
                             "xtts-local", "openvoice-local"])
    ap.add_argument("--text", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--allow-spend", action="store_true",
                    help="explicitly authorize API spend (gate must exist and pass)")
    ap.add_argument("--benchmark-only", action="store_true",
                    help="required (with local adapters too) when the reference "
                         "set is internal_only (benchmark corpus)")
    ap.add_argument("--model-version", default="eleven_multilingual_v2")
    ap.add_argument("--chatterbox-variant", default="turbo",
                    choices=["std", "turbo"],
                    help="std=ResembleAI/chatterbox (500M), "
                         "turbo=ResembleAI/chatterbox-turbo (350M) — "
                         "verified against chatterbox-tts 0.1.7, docs §2")
    ap.add_argument("--exaggeration", type=float, default=0.5,
                    help="chatterbox expression/intensity control (0.5 default)")
    ap.add_argument("--cfg-weight", type=float, default=0.5,
                    help="chatterbox pacing/creativity (classifier-free-guidance) "
                         "weight; lower = slower, more literal pacing")
    ap.add_argument("--prompt-clip",
                    help="chatterbox: condition on this curated clip_id "
                         "(default: curated clip with the most speech)")
    args = ap.parse_args()

    t0 = time.time()
    if args.adapter == "elevenlabs-ivc":
        prov = clone_elevenlabs(args.persona_root, args.text, args.out,
                                args.allow_spend, args.model_version, t0,
                                benchmark_only=args.benchmark_only)
    elif args.adapter == "chatterbox-local":
        prov = clone_chatterbox(args.persona_root, args.text, args.out,
                                args.chatterbox_variant, args.exaggeration,
                                args.cfg_weight, args.prompt_clip,
                                args.benchmark_only, t0)
    else:
        prov = clone_local(args.persona_root, args.text, args.out, args.adapter)
        prov.setdefault("references_used", [])
    prov.update({"text": args.text, "endpoint": args.adapter,
                 "latency_s": round(time.time() - t0, 2),
                 "bytes": os.path.getsize(args.out),
                 "generated_at": now_iso()})
    with open(args.out + ".prov.json", "w", encoding="utf-8") as f:
        json.dump(prov, f, indent=2)
    print(json.dumps({"ok": True, "out": args.out, "model": prov["model"],
                      "references_used": prov["references_used"],
                      "next": "voice_gate.py --audio (output must pass the gate before registry)"}))


if __name__ == "__main__":
    main()

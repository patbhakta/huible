"""Shared helpers for the reference-grounded voice pipeline (HU-2151).

Stages: voice_collect → voice_curate → voice_clone → voice_gate →
voice_registry. See docs/IDENTITY_VOICE_PIPELINE.md.

Mirrors refpipe_common.py (HU-2150 image twin) but is audio-native and does
not import OpenCV/insightface. Speaker embeddings: speechbrain ECAPA-TDNN
(spkrec-ecapa-voxceleb, 192-d; HU-2160 swap from resemblyzer 256-d after
HU-2159 showed it does not separate sitcom speech). VAD-trim still uses
resemblyzer's preprocess_wav (webrtcvad). Decoding: ffmpeg to 16 kHz mono
PCM — no audioread dependency.

Requires: torch (CPU), speechbrain, resemblyzer
(scripts/requirements-voicepipe.txt).
"""

import datetime
import hashlib
import json
import os
import subprocess

import numpy as np

SAMPLE_RATE = 16000

# Identity of the speaker gate — recorded in gate configs and gate log rows
# so a config calibrated with one embedder is never silently used with another.
GATE_ID = "ecapa-voxceleb-192d-cosine-max"

# Module-level singleton — loading the encoder takes ~5 s; do it once.
_ENCODER = None


def voice_encoder():
    global _ENCODER
    if _ENCODER is None:
        try:
            from speechbrain.inference.speaker import EncoderClassifier
        except ImportError:
            from speechbrain.pretrained import EncoderClassifier
        _ENCODER = EncoderClassifier.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb", run_opts={"device": "cpu"})
    return _ENCODER


def now_iso():
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def append_jsonl(path, record):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=False) + "\n")


def decode_audio(path):
    """Decode any audio file to 16 kHz mono float32 via ffmpeg."""
    proc = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", path, "-f", "s16le",
         "-ar", str(SAMPLE_RATE), "-ac", "1", "-"],
        capture_output=True, timeout=300,
    )
    if proc.returncode != 0 or not proc.stdout:
        err = proc.stderr.decode("utf-8", "replace")[:200]
        raise ValueError(f"ffmpeg decode failed for {path}: {err}")
    return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def audio_fingerprint(wav):
    """Cheap near-duplicate fingerprint: hash of 2 kHz-downsampled sign bits."""
    step = SAMPLE_RATE // 2000
    coarse = wav[::step]
    return hashlib.sha256(np.signbit(coarse).tobytes()).hexdigest()


def audio_quality(wav):
    """Pre-gate quality metrics on a decoded waveform."""
    dur = len(wav) / SAMPLE_RATE
    clip_frac = float(np.mean(np.abs(wav) >= 0.999))
    rms = float(np.sqrt(np.mean(wav ** 2))) if len(wav) else 0.0
    return {
        "duration_s": round(dur, 2),
        "clipping_frac": round(clip_frac, 5),
        "rms": round(rms, 5),
    }


def preprocess(wav):
    """VAD-trim + normalize via resemblyzer (16 kHz float in/out)."""
    from resemblyzer import preprocess_wav

    return preprocess_wav(wav, source_sr=SAMPLE_RATE)


def embed_wav(wav):
    """(192-d normed embedding, metrics) for a decoded waveform."""
    q = audio_quality(wav)
    trimmed = preprocess(wav)
    q["speech_s"] = round(len(trimmed) / SAMPLE_RATE, 2)
    import torch
    wav_tensor = torch.tensor(trimmed).unsqueeze(0)
    emb = voice_encoder().encode_batch(wav_tensor).squeeze().cpu().numpy()
    return np.asarray(emb, dtype=np.float32), q


def embed_file(path):
    return embed_wav(decode_audio(path))


def cosine(a, b):
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


def valid_rights(rec):
    """Fail-closed rights check per the COLLECT schema (docs §1).

    benchmark_only sets (friends-v2 / MELD for Persona-0) are valid *only*
    for internal benchmarking — they make the whole reference set
    internal_only and can never back a production voice asset.
    """
    r = rec.get("rights") or {}
    basis = r.get("basis")
    if basis == "onboarding_consent":
        return bool(r.get("consent_by"))
    if basis == "client_archive":  # family recordings / voicemails
        return bool(r.get("consent_by") and r.get("provided_by"))
    if basis == "license":
        return bool(r.get("license_ref"))
    if basis == "benchmark_only":
        return bool(r.get("corpus_ref") and r.get("internal_only") is True)
    if basis == "synthetic":
        return rec.get("source") == "synthetic_seed"
    return False


def vault_paths(persona_root):
    return {
        "raw_dir": os.path.join(persona_root, "references", "voice-raw"),
        "set_json": os.path.join(persona_root, "references", "voice-reference-set.json"),
        "curated": os.path.join(persona_root, "references", "voice-curated.jsonl"),
        "cur_log": os.path.join(persona_root, "references", "voice-curation-log.jsonl"),
        "emb": os.path.join(persona_root, "references", "voice-embeddings.json"),
        "gate_config": os.path.join(persona_root, "references", "voice-gate-config.json"),
        "gate_log": os.path.join(persona_root, "media", "voice-gate-log.jsonl"),
        "registry": os.path.join(persona_root, "media", "voice-registry.jsonl"),
    }


def load_curated_embeddings(persona_root):
    """{clip_id: embedding} from the cached curated set (fail if absent)."""
    p = vault_paths(persona_root)
    cache = load_json(p["emb"])
    return {k: np.asarray(v["emb"], dtype=np.float32) for k, v in cache.items()}

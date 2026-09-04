"""Audio ingestion: faster-whisper verbatim dialog atom (CPU-only).

Measured basis (HU-2697): ``base.en`` int8, 8 CPU threads — corpus WER 0.077
on LibriSpeech test-clean at 3.8x realtime. The atom keeps verbatim segments
with timestamps + provenance (model/version/compute/WER context) in the vault;
the original audio file is an irreplaceable raw measurement and is stored
alongside.

VAD/no-speech gating is a measured R&D follow-up (not v1): we store
``no_speech_prob`` per segment and a caution flag so downstream consumers know
transcripts from non-dialog audio (music/sfx) can be confident hallucinations
(Big Buck Bunny failure mode, HU-2697).
"""

from __future__ import annotations

import time
from pathlib import Path

from .atoms import Tier, VaultWriter, atom_from
from .config import WHISPER_CORPUS_WER_BASELINE, IngestConfig

AUDIO_EXTS = {".flac", ".wav", ".mp3", ".m4a", ".ogg", ".opus"}


def transcribe(path: str | Path, config: IngestConfig | None = None) -> dict:
    """Transcribe one audio file; returns verbatim segments + provenance."""
    config = config or IngestConfig()
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:  # pragma: no cover - environment guard
        raise RuntimeError(
            f"faster-whisper is required for the audio lane (pip install 'huible[ingest]'): {e}"
        ) from e

    model = WhisperModel(
        config.whisper_model,
        device="cpu",
        compute_type="int8",
        cpu_threads=config.cpu_threads,
        num_workers=1,
    )
    t0 = time.perf_counter()
    segments, info = model.transcribe(str(path), language="en", beam_size=5)
    segs = [
        {
            "start": round(s.start, 2),
            "end": round(s.end, 2),
            "text": s.text.strip(),
            "avg_logprob": round(s.avg_logprob, 3),
            "no_speech_prob": round(s.no_speech_prob, 3),
        }
        for s in segments
    ]
    wall = time.perf_counter() - t0
    return {
        "segments": segs,
        "verbatim_text": " ".join(s["text"] for s in segs),
        "duration_sec": round(info.duration, 3),
        "wall_sec": round(wall, 2),
        "provenance": {
            "tool": "faster-whisper",
            "model": config.whisper_model,
            "compute": "cpu/int8",
            "cpu_threads": config.cpu_threads,
            "wer_context": {
                "corpus": "LibriSpeech test-clean (HU-2697 measured baseline)",
                "corpus_wer": WHISPER_CORPUS_WER_BASELINE.get(config.whisper_model),
                "note": "read-speech baseline; real persona audio will score worse",
            },
        },
    }


def ingest_audio(
    path: str | Path,
    writer: VaultWriter,
    config: IngestConfig | None = None,
    reference_text: str | None = None,
) -> dict:
    """Store the original + a vault-tier verbatim dialog atom."""
    config = config or IngestConfig()
    src = Path(path)
    original = writer.store_original(src)
    result = transcribe(src, config)

    provenance = dict(result["provenance"])
    content: dict = {
        "verbatim_text": result["verbatim_text"],
        "segments": result["segments"],
        "duration_sec": result["duration_sec"],
        "flags": ["vad_gating_followup", "no_speech_prob_stored"],
    }
    if reference_text:
        from .metrics import wer

        file_wer = round(wer(result["verbatim_text"], reference_text), 4)
        provenance["file_wer_vs_reference"] = file_wer
        content["file_wer_vs_reference"] = file_wer

    writer.write_atom(
        atom_from(
            "dialog_verbatim",
            Tier.VAULT,
            {"file": original["stored_as"], "sha256": original["sha256"]},
            provenance,
            content,
        ),
        slug=src.stem,
    )
    return {
        "input": str(src),
        "sha256": original["sha256"],
        "segments": len(result["segments"]),
        "duration_sec": result["duration_sec"],
        "wall_sec": result["wall_sec"],
        "file_wer_vs_reference": content.get("file_wer_vs_reference"),
    }

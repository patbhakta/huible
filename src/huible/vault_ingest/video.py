"""Video ingestion: ffmpeg composition of already-measured lanes.

Measured basis (HU-2697): full decode ~117x realtime on CPU; 1 fps frame
sampling and audio-track extraction cost seconds per 10-minute file. Video
ingestion is therefore a composition, not a new capability:

- audio track -> 16 kHz mono wav -> the whisper audio lane (vault atom)
- 1 fps frames -> the image lane (regenerable intermediates, TencentDB tier)

Tier mapping: the original video is vault (irreplaceable); extracted wav and
frames are regenerable intermediates (derived); the transcript atom is vault.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from .atoms import Tier, VaultWriter, atom_from
from .audio import ingest_audio
from .config import IngestConfig

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"}


def probe(path: str | Path) -> dict:
    p = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if p.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {p.stderr.strip()}")
    meta = json.loads(p.stdout)
    streams = {}
    for s in meta.get("streams", []):
        streams[s["codec_type"]] = {
            k: s.get(k)
            for k in (
                "codec_name",
                "width",
                "height",
                "avg_frame_rate",
                "duration",
                "sample_rate",
                "channels",
            )
            if s.get(k) is not None
        }
    return {
        "container": meta.get("format", {}).get("format_name"),
        "duration_sec": float(meta.get("format", {}).get("duration", 0)),
        "streams": streams,
    }


def _run(cmd: list[str]) -> None:
    p = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({' '.join(cmd[:3])}...): {p.stderr.strip()[-400:]}")


def ingest_video(path: str | Path, writer: VaultWriter, config: IngestConfig | None = None) -> dict:
    config = config or IngestConfig()
    src = Path(path)
    t0 = time.perf_counter()
    original = writer.store_original(src)
    report: dict = {"input": str(src), "sha256": original["sha256"], "probe": probe(src)}

    # Audio track -> 16 kHz mono wav (regenerable intermediate, derived tier).
    wav = writer.derived_media_dir / f"{src.stem}_16k.wav"
    t1 = time.perf_counter()
    _run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", str(wav)]
    )
    report["audio_extraction"] = {
        "wav": str(wav.relative_to(writer.root)),
        "wall_sec": round(time.perf_counter() - t1, 2),
    }

    # 1 fps frames (regenerable intermediates, derived tier).
    frames_dir = writer.derived_media_dir / f"{src.stem}_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    t1 = time.perf_counter()
    _run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(src),
            "-vf",
            f"fps={config.video_frame_fps}",
            str(frames_dir / "frame_%05d.jpg"),
        ]
    )
    frames = sorted(frames_dir.glob("frame_*.jpg"))
    report["frame_sampling"] = {
        "fps": config.video_frame_fps,
        "frames": len(frames),
        "dir": str(frames_dir.relative_to(writer.root)),
        "wall_sec": round(time.perf_counter() - t1, 2),
    }
    writer.write_atom(
        atom_from(
            "media_frames",
            Tier.DERIVED,
            {"file": original["stored_as"], "sha256": original["sha256"]},
            {"tool": f"ffmpeg (fps={config.video_frame_fps})", "regenerable": True},
            {"count": len(frames), "dir": str(frames_dir.relative_to(writer.root))},
        ),
        slug=src.stem,
    )

    # Audio lane (whisper) over the extracted track -> vault verbatim atom.
    audio_report = ingest_audio(wav, writer, config)
    report["audio_lane"] = audio_report
    report["wall_sec"] = round(time.perf_counter() - t0, 2)
    return report

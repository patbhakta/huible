"""Video ingestion feasibility probe (CPU-only): ffmpeg decode cost + audio lane.

Uses the 10s 720p Big Buck Bunny clip (CC-BY, Blender Foundation). Measures:
- container/audio/video properties (ffprobe)
- 1 fps frame sampling -> jpg count, bytes, wall time
- full decode throughput (all frames to null) as an upper bound
- audio track extraction to 16 kHz mono wav (the whisper lane's input format)
- composition check: whisper tiny.en over the extracted audio

Output: ../outputs/video_probe.json (+ frames/, bbb_audio.wav, bbb_audio_transcript.json)
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SAMPLES = ROOT / "samples"
OUT = ROOT / "outputs" / "video"
OUT.mkdir(parents=True, exist_ok=True)
VIDEO = SAMPLES / "bbb_720p.mp4"


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def probe() -> dict:
    p = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(VIDEO)])
    meta = json.loads(p.stdout)
    streams = {}
    for s in meta.get("streams", []):
        streams[s["codec_type"]] = {
            k: s.get(k)
            for k in ("codec_name", "width", "height", "avg_frame_rate", "duration", "sample_rate", "channels", "bit_rate")
            if s.get(k) is not None
        }
    return {
        "container": meta.get("format", {}).get("format_name"),
        "duration_sec": float(meta.get("format", {}).get("duration", 0)),
        "size_bytes": int(meta.get("format", {}).get("size", 0)),
        "streams": streams,
    }


def main() -> None:
    report = {"file": "samples/bbb_720p.mp4", "license": "CC-BY, Blender Foundation (archive.org derivative, 640x360)"}
    report["probe"] = probe()

    # 1 fps frame sampling (the vault "keyframe lane" input)
    t0 = time.perf_counter()
    p = run(["ffmpeg", "-y", "-v", "error", "-i", str(VIDEO), "-vf", "fps=1", str(OUT / "frame_%03d.jpg")])
    frames_sec = time.perf_counter() - t0
    frames = sorted(OUT.glob("frame_*.jpg"))
    report["frame_sampling_1fps"] = {
        "frames": len(frames),
        "bytes": sum(f.stat().st_size for f in frames),
        "wall_sec": round(frames_sec, 3),
        "exit_code": p.returncode,
    }

    # full-decode throughput upper bound
    t0 = time.perf_counter()
    p = run(["ffmpeg", "-y", "-v", "error", "-i", str(VIDEO), "-f", "null", "-"])
    decode_sec = time.perf_counter() - t0
    dur = report["probe"]["duration_sec"]
    report["full_decode"] = {
        "wall_sec": round(decode_sec, 3),
        "decode_fps_x": round(dur / decode_sec, 1) if decode_sec else None,
        "exit_code": p.returncode,
    }

    # audio extraction -> whisper lane input format
    wav = OUT / "bbb_audio.wav"
    t0 = time.perf_counter()
    p = run(["ffmpeg", "-y", "-v", "error", "-i", str(VIDEO), "-vn", "-ac", "1", "-ar", "16000", str(wav)])
    audio_sec = time.perf_counter() - t0
    report["audio_extraction"] = {
        "wall_sec": round(audio_sec, 3),
        "bytes": wav.stat().st_size if wav.exists() else 0,
        "exit_code": p.returncode,
    }

    # composition check: whisper over extracted audio
    t0 = time.perf_counter()
    from faster_whisper import WhisperModel

    model = WhisperModel("tiny.en", device="cpu", compute_type="int8", cpu_threads=8)
    segments, info = model.transcribe(str(wav), language="en", beam_size=5)
    segs = [{"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()} for s in segments]
    whisper_sec = time.perf_counter() - t0
    report["audio_to_whisper"] = {
        "model": "tiny.en",
        "audio_sec": round(info.duration, 2),
        "wall_sec": round(whisper_sec, 2),
        "segments": segs,
        "note": "BBB soundtrack is music/sfx only — segments show what the audio lane does on non-dialog audio",
    }

    (ROOT / "outputs" / "video_probe.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "probe"}, indent=2)[:1200])


if __name__ == "__main__":
    main()

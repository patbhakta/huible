"""Run faster-whisper (CPU) over the audio manifest and record timings.

Measures, per model: model load wall time, per-file transcription wall time,
audio seconds, and the full segment list (start/end/text) so a dialog atom with
timestamps can be built from the output. compute_type=int8, cpu_threads=8.

Output: ../outputs/whisper_{model}.json
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from faster_whisper import WhisperModel

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SAMPLES = ROOT / "samples"
OUT = ROOT / "outputs"
OUT.mkdir(parents=True, exist_ok=True)
MANIFEST = json.loads((ROOT / "ground_truth" / "manifest.json").read_text())

MODELS = ["tiny.en", "base.en", "small.en"]
CPU_THREADS = 8


def cache_dir_bytes(model_name: str) -> int:
    hub = Path.home() / ".cache" / "huggingface" / "hub"
    d = hub / f"models--Systran--faster-whisper-{model_name}"
    if not d.exists():
        return 0
    return sum(p.stat().st_size for p in d.rglob("*") if p.is_file())


def run(model_name: str) -> None:
    t0 = time.perf_counter()
    model = WhisperModel(
        model_name,
        device="cpu",
        compute_type="int8",
        cpu_threads=CPU_THREADS,
        num_workers=1,
    )
    load_sec = time.perf_counter() - t0

    results = []
    total_audio = 0.0
    total_wall = 0.0
    for entry in MANIFEST["entries"]:
        audio_path = SAMPLES / entry["audio"]
        t1 = time.perf_counter()
        segments, info = model.transcribe(str(audio_path), language="en", beam_size=5)
        segs = [
            {
                "start": round(s.start, 2),
                "end": round(s.end, 2),
                "text": s.text.strip(),
                "avg_logprob": round(s.avg_logprob, 3),
            }
            for s in segments
        ]
        wall = time.perf_counter() - t1
        total_audio += info.duration
        total_wall += wall
        text = " ".join(s["text"] for s in segs)
        results.append(
            {
                "id": entry["id"],
                "speaker": entry["speaker"],
                "audio_sec": round(info.duration, 3),
                "wall_sec": round(wall, 3),
                "hypothesis_text": text,
                "segments": segs,
            }
        )
        print(f"[{model_name}] {entry['id']}: {info.duration:.1f}s audio in {wall:.1f}s")

    payload = {
        "model": model_name,
        "device": "cpu",
        "compute_type": "int8",
        "cpu_threads": CPU_THREADS,
        "model_load_sec": round(load_sec, 2),
        "model_disk_bytes": cache_dir_bytes(model_name),
        "total_audio_sec": round(total_audio, 2),
        "total_wall_sec": round(total_wall, 2),
        "audio_per_cpu_sec": round(total_audio / total_wall, 2) if total_wall else None,
        "results": results,
    }
    (OUT / f"whisper_{model_name}.json").write_text(json.dumps(payload, indent=2))
    print(
        f"[{model_name}] load {load_sec:.1f}s | {total_audio:.0f}s audio in "
        f"{total_wall:.0f}s wall -> {payload['audio_per_cpu_sec']}x realtime"
    )


if __name__ == "__main__":
    for m in MODELS:
        run(m)

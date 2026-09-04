"""Build the audio measurement manifest from the extracted LibriSpeech subset.

Picks utterances from three speakers, records true audio duration (ffprobe) and
the verbatim reference text (LibriSpeech ground-truth transcripts, which are the
caption ground truth for WER).

Output: ../ground_truth/manifest.json
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
SAMPLES = HERE.parent / "samples"
LIBRI = SAMPLES / "LibriSpeech" / "test-clean"
OUT = HERE.parent / "ground_truth"
OUT.mkdir(parents=True, exist_ok=True)

SPEAKERS = ["121", "61", "1089", "1995"]
MAX_PER_SPEAKER = 8
TARGET_TOTAL_SEC = 200.0  # ~3.3 minutes of audio total


def ffprobe_duration(path: Path) -> float:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(out.stdout.strip())


def main() -> None:
    entries: list[dict] = []
    total = 0.0
    for spk in SPEAKERS:
        chapter_dirs = sorted(p for p in (LIBRI / spk).iterdir() if p.is_dir())
        picked = 0
        for ch in chapter_dirs:
            trans = ch / f"{spk}-{ch.name}.trans.txt"
            if not trans.exists():
                continue
            refs: dict[str, str] = {}
            for line in trans.read_text().splitlines():
                uid, _, text = line.partition(" ")
                refs[uid] = text.strip()
            for flac in sorted(ch.glob("*.flac")):
                if picked >= MAX_PER_SPEAKER or total >= TARGET_TOTAL_SEC:
                    break
                dur = ffprobe_duration(flac)
                entries.append(
                    {
                        "id": flac.stem,
                        "speaker": spk,
                        "audio": str(flac.relative_to(SAMPLES)),
                        "duration_sec": round(dur, 3),
                        "reference_text": refs[flac.stem],
                    }
                )
                total += dur
                picked += 1
            if picked >= MAX_PER_SPEAKER or total >= TARGET_TOTAL_SEC:
                break

    manifest = {
        "source": "LibriSpeech test-clean (public domain, CC-BY-4.0 distribution by OpenSLR)",
        "speakers": SPEAKERS,
        "n_utterances": len(entries),
        "total_audio_sec": round(total, 2),
        "entries": entries,
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"manifest: {len(entries)} utterances, {total:.1f}s audio")


if __name__ == "__main__":
    main()

"""Score whisper hypotheses against LibriSpeech references: WER per model.

WER = (substitutions + deletions + insertions) / reference words, on
lowercase, punctuation-stripped tokens. Output: ../outputs/wer_scores.json
"""

from __future__ import annotations

import json
import re
import string
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = ROOT / "outputs"
MANIFEST = json.loads((ROOT / "ground_truth" / "manifest.json").read_text())

_PUNCT = str.maketrans("", "", string.punctuation)


def normalize(text: str) -> list[str]:
    return text.lower().translate(_PUNCT).split()


def edit_distance(a: list[str], b: list[str]) -> int:
    prev = list(range(len(b) + 1))
    for i, wa in enumerate(a, 1):
        cur = [i]
        for j, wb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (wa != wb)))
        prev = cur
    return prev[-1]


def wer(ref: str, hyp: str) -> float:
    r, h = normalize(ref), normalize(hyp)
    if not r:
        return 0.0 if not h else 1.0
    return edit_distance(r, h) / len(r)


def main() -> None:
    refs = {e["id"]: e["reference_text"] for e in MANIFEST["entries"]}
    scores: dict[str, dict] = {}
    for path in sorted(OUT.glob("whisper_*.json")):
        payload = json.loads(path.read_text())
        per_file = []
        total_err = 0
        total_words = 0
        for r in payload["results"]:
            w = wer(refs[r["id"]], r["hypothesis_text"])
            n = len(normalize(refs[r["id"]]))
            total_err += round(w * n)
            total_words += n
            per_file.append({"id": r["id"], "wer": round(w, 4)})
        scores[payload["model"]] = {
            "corpus_wer": round(total_err / total_words, 4),
            "mean_file_wer": round(sum(f["wer"] for f in per_file) / len(per_file), 4),
            "max_file_wer": max(f["wer"] for f in per_file),
            "total_words": total_words,
            "per_file": per_file,
        }
    (OUT / "wer_scores.json").write_text(json.dumps(scores, indent=2))
    for model, s in scores.items():
        print(f"{model}: corpus WER {s['corpus_wer']:.3f} (mean file {s['mean_file_wer']:.3f})")


if __name__ == "__main__":
    main()

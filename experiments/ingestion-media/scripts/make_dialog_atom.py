"""Emit a vault-shaped verbatim dialog atom with timestamps.

Picks the best-scoring whisper output for one representative utterance and
writes ../outputs/dialog_atom.json — the artifact shape an ingestion pipeline
would write into a persona/domain vault (verbatim text + source timing offsets,
per the HU-1839 vault doctrine).
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = ROOT / "outputs"

# Representative utterance: first manifest entry (speaker 121).
manifest = json.loads((ROOT / "ground_truth" / "manifest.json").read_text())
entry = manifest["entries"][0]
scores = json.loads((OUT / "wer_scores.json").read_text())
best_model = min(scores, key=lambda m: scores[m]["corpus_wer"])
run = json.loads((OUT / f"whisper_{best_model}.json").read_text())
result = next(r for r in run["results"] if r["id"] == entry["id"])
file_wer = next(f for f in scores[best_model]["per_file"] if f["id"] == entry["id"])

atom = {
    "atom_type": "dialog_verbatim",
    "source": {
        "sample_id": entry["id"],
        "file": entry["audio"],
        "speaker_id": entry["speaker"],
        "corpus": manifest["source"],
        "sha256_note": "raw flac stored in vault as irreplaceable raw measurement",
    },
    "transcription": {
        "model": f"faster-whisper/{best_model}",
        "compute": "cpu/int8",
        "file_wer_vs_reference": file_wer["wer"],
        "segments": result["segments"],
        "verbatim_text": result["hypothesis_text"],
    },
    "ground_truth_reference": {
        "text": entry["reference_text"],
        "role": "evaluation-only; not stored in production vault",
    },
    "vault_doctrine": {
        "vault_tier": ["source flac", "verbatim text + segment timestamps"],
        "regenerable_tier": ["embedding vectors", "normalized/lowercased text"],
    },
}
(OUT / "dialog_atom.json").write_text(json.dumps(atom, indent=2))
print(f"dialog_atom.json from {best_model} (WER {file_wer['wer']}) on {entry['id']}")

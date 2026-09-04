"""Vault ingestion pipeline v1 (CPU-only): PDF router + media atoms.

Scope (HU-2699, per the frozen HU-2692 game plan):
- PDF per-page router: pymupdf text-layer check -> Tier 0 direct extraction;
  no/short text layer -> raster 200 DPI -> docling CPU (Tier 1).
- Tier-2 VLM lane: implemented, flag-gated OFF by default.
- Media atoms: audio -> faster-whisper verbatim segments; video -> ffmpeg
  composition (audio track -> audio lane, 1 fps frames -> image lane);
  images -> source-of-truth artifacts, retrieval rides extracted text.
- Tier mapping end-to-end: originals/verbatim/formulas/table structure ->
  vault; embeddings/intermediates/approximate values -> TencentDB tier.

Usage::

    python -m huible.vault_ingest --out OUTDIR FILE [FILE ...]
"""

from __future__ import annotations

import time
from pathlib import Path

from .atoms import VaultWriter
from .audio import AUDIO_EXTS, ingest_audio
from .config import IngestConfig
from .images import IMAGE_EXTS, ingest_image
from .pdf import ingest_pdf
from .video import VIDEO_EXTS, ingest_video

__all__ = [
    "IngestConfig",
    "VaultWriter",
    "ingest_audio",
    "ingest_image",
    "ingest_path",
    "ingest_paths",
    "ingest_pdf",
    "ingest_video",
]


def ingest_path(path: str | Path, writer: VaultWriter, config: IngestConfig | None = None) -> dict:
    """Dispatch one input file to its lane by extension."""
    config = config or IngestConfig()
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".pdf":
        return ingest_pdf(p, writer, config)
    if suffix in AUDIO_EXTS:
        return ingest_audio(p, writer, config)
    if suffix in VIDEO_EXTS:
        return ingest_video(p, writer, config)
    if suffix in IMAGE_EXTS:
        return ingest_image(p, writer, config)
    raise ValueError(f"unsupported input type: {p} (suffix {suffix!r})")


def ingest_paths(
    paths: list[str | Path], out_dir: str | Path, config: IngestConfig | None = None
) -> dict:
    """Run the pipeline over a set of inputs into one two-tier output root."""
    config = config or IngestConfig()
    writer = VaultWriter(Path(out_dir))
    t0 = time.perf_counter()
    runs = []
    for p in paths:
        runs.append(ingest_path(p, writer, config))
    manifest = writer.write_manifest(
        {
            "inputs": [str(p) for p in paths],
            "runs": runs,
            "wall_sec": round(time.perf_counter() - t0, 2),
            "config": {"vlm_enabled": config.vlm_enabled},
        }
    )
    return {"manifest": str(manifest), "runs": runs, "atom_count": len(writer.atoms)}

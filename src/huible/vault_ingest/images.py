"""Image ingestion: source-of-truth artifact, retrieval rides extracted text.

Measured basis (HU-2697): CLIP-style image embeddings are scene/photo-oriented
and rank dense document pages weakly (2/4 top-1); the primary key into a
persona/domain vault over scanned images is the extracted text (PDF half,
0.93-0.98 F1), with the image stored as the source-of-truth artifact. No CLIP
lane in v1.
"""

from __future__ import annotations

from pathlib import Path

from .atoms import Tier, VaultWriter, atom_from
from .config import IngestConfig

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}

RETRIEVAL_NOTE = (
    "retrieval rides extracted text; image embeddings are an optional extra "
    "index, not the primary key (HU-2697 measured)"
)


def ingest_image(path: str | Path, writer: VaultWriter, config: IngestConfig | None = None) -> dict:
    config = config or IngestConfig()
    src = Path(path)
    original = writer.store_original(src)
    writer.write_atom(
        atom_from(
            "image_source",
            Tier.VAULT,
            {"file": original["stored_as"], "sha256": original["sha256"]},
            {"tool": "artifact store", "retrieval": RETRIEVAL_NOTE},
            {"pixels_note": "stored as-is; no derived representation in v1"},
        )
    )
    return {"input": str(src), "sha256": original["sha256"]}

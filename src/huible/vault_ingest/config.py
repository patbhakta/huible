"""Ingestion configuration.

v1 flags:
- ``vlm_enabled`` gates the Tier-2 VLM lane. It is **OFF by default**; production
  enable is new paid spend and requires Pat's approval (HU-2692 plan, 2026-09-04
  revision). Credentials are only ever read from the environment at call time —
  nothing is hardcoded.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

VLM_ENABLED_ENV = "VAULT_INGEST_VLM_ENABLED"
TRUTHY = {"1", "true", "yes", "on"}

# HU-2697 measured baseline: faster-whisper base.en int8 corpus WER on
# LibriSpeech test-clean (32 utterances / 172.3 s / 4 speakers).
WHISPER_CORPUS_WER_BASELINE = {
    "tiny.en": 0.097,
    "base.en": 0.077,
    "small.en": 0.075,
}


@dataclass
class IngestConfig:
    """Runtime configuration for the vault ingestion pipeline (CPU-only)."""

    vlm_enabled: bool = False
    raster_dpi: int = 200
    # Pages whose extracted text layer holds at least this many characters go
    # Tier 0 (direct pymupdf); shorter/absent layers go Tier 1 (docling).
    text_layer_min_chars: int = 32
    whisper_model: str = "base.en"
    cpu_threads: int = 8
    video_frame_fps: float = 1.0

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> IngestConfig:
        env = os.environ if env is None else env
        return cls(vlm_enabled=env.get(VLM_ENABLED_ENV, "").lower() in TRUTHY)

"""Ingestion configuration.

v1 flags:
- ``vlm_enabled`` gates the Tier-2 VLM lane. It is **OFF by default** in code;
  spend is approved (gemini-3.8-flash, Pat 2026-09-04) and the lane was enabled
  2026-09-04 via env in the deployment environment (HU-2701; production
  endpoint: OpenRouter ``google/gemini-3.8-flash`` — measured lane, see
  ``docs/evidence/hu2692_ingestion_extraction_20260904.md``). Credentials are
  only ever read from the environment at call time — nothing is hardcoded.
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

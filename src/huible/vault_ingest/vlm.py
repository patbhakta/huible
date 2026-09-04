"""Tier-2 VLM lane — implemented but flag-gated OFF by default.

Purpose (HU-2692 plan): formula/chart pages (detected via docling
``formula-not-decoded`` markers / layout classes) get a VLM pass on that page
only. Formulas come back as LaTeX (vault tier — the "specific"); chart values
come back flagged ``approximate, chart-derived`` (TencentDB tier).

Production enable is new paid spend and requires Pat's approval:

- the lane only activates when ``IngestConfig.vlm_enabled`` is True (env
  ``VAULT_INGEST_VLM_ENABLED``), which defaults to False;
- credentials are read from the environment at call time and never hardcoded:
  ``VAULT_INGEST_VLM_BASE_URL``, ``VAULT_INGEST_VLM_API_KEY``,
  ``VAULT_INGEST_VLM_MODEL`` (any OpenAI-compatible vision endpoint);
- with the flag off, nothing here performs I/O — callers record a
  ``vlm_skipped`` flag on the page instead.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

from .config import IngestConfig

VLM_BASE_URL_ENV = "VAULT_INGEST_VLM_BASE_URL"
VLM_API_KEY_ENV = "VAULT_INGEST_VLM_API_KEY"
VLM_MODEL_ENV = "VAULT_INGEST_VLM_MODEL"

DISABLED_REASON = "tier-2 VLM lane disabled by config (production enable requires spend approval)"
UNCONFIGURED_REASON = "tier-2 VLM lane enabled but credentials not configured in environment"

CHART_VALUE_FLAGS = ["approximate", "chart-derived"]


class VLMDisabledError(RuntimeError):
    """Raised when the VLM lane is attempted while gated off or unconfigured."""


@dataclass
class VLMSettings:
    base_url: str | None
    api_key: str | None
    model: str | None

    @classmethod
    def from_env(cls) -> VLMSettings:
        import os

        return cls(
            base_url=os.environ.get(VLM_BASE_URL_ENV),
            api_key=os.environ.get(VLM_API_KEY_ENV),
            model=os.environ.get(VLM_MODEL_ENV),
        )


def vlm_page_pass(page_png: str, config: IngestConfig) -> dict:
    """Run the VLM extraction pass for one page image.

    Returns ``{"formulas_latex": [...], "chart_values": [...]}``. Chart values
    are returned already flagged approximate/chart-derived so the caller can
    tier them to the TencentDB side.
    """

    if not config.vlm_enabled:
        raise VLMDisabledError(DISABLED_REASON)
    settings = VLMSettings.from_env()
    if not (settings.base_url and settings.api_key and settings.model):
        raise VLMDisabledError(UNCONFIGURED_REASON)

    import httpx

    with open(page_png, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    prompt = (
        "Extract from this document page. Return strict JSON with keys: "
        '"formulas": list of formulas as LaTeX strings; '
        '"chart_values": list of {"series": str, "x": str, "y": number} read '
        "from any charts (mark them approximate)."
    )
    resp = httpx.post(
        f"{settings.base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {settings.api_key}"},
        json={
            "model": settings.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        },
                    ],
                }
            ],
        },
        timeout=120,
    )
    resp.raise_for_status()
    text = resp.json()["choices"][0]["message"]["content"]
    return _parse_extraction(text)


def _parse_extraction(text: str) -> dict:
    import json

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end < 0:
            return {"formulas_latex": [], "chart_values": [], "parse_error": True}
        data = json.loads(text[start : end + 1])
    return {
        "formulas_latex": [str(f) for f in data.get("formulas", [])],
        "chart_values": [
            {**v, "flags": CHART_VALUE_FLAGS}
            for v in data.get("chart_values", [])
            if isinstance(v, dict)
        ],
    }

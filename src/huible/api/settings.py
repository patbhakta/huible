"""Pydantic settings for the Huible API server (HU-1403).

Reads configuration from a ``.env`` file and the process environment. The
defaults are intentionally key-free (``GENERATOR_PROVIDER=mock``,
``EMBEDDING_PROVIDER=fake``, no database) so the server skeleton boots and the
test suite runs without secrets or external services — matching the
``EMBEDDING_PROVIDER=fake`` / mock-generator convention used across the engine.

The LLM-provider slots (``generator_*``) mirror
:meth:`huible.persona.generator.GeneratorConfig.from_env` and ``.env.example``
so the sibling generator client (HU-1400) and the server share one source of
truth. :meth:`Settings.to_generator_config` is the bridge.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from huible.persona.generator import GeneratorConfig

logger = logging.getLogger(__name__)

__all__ = ["Settings", "get_settings"]

#: Schemes that identify a Huible async postgres URL. Plain ``postgres://``
#: without a driver is intentionally excluded so a foreign control-plane
#: ``DATABASE_URL`` leaking into the environment can never be mistaken for the
#: Huible memory database — the server simply stays on the key-free path.
_HUIBLE_DB_SCHEMES = frozenset({"postgresql+asyncpg", "postgres+asyncpg"})


class Settings(BaseSettings):
    """Server + engine configuration loaded from ``.env`` / environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ────────────────────────────────────────────────────────
    huible_host: str = "0.0.0.0"
    huible_port: int = 8000
    huible_log_level: str = "INFO"
    huible_env: str = "production"

    # ── Database ───────────────────────────────────────────────────────────
    database_url: str = ""
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = ""
    postgres_password: str = ""
    postgres_db: str = ""

    # ── Embedding provider (Phase 2+; fake in Phase 1) ─────────────────────
    embedding_provider: str = "fake"
    openai_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"

    # ── Advisory (Tier 2) — never the persona voice ────────────────────────
    claude_api_key: str = ""
    openai_advisory_key: str = ""

    # ── Persona generator (the speaking voice) ─────────────────────────────
    generator_provider: str = "mock"
    generator_base_url: str = ""
    generator_model: str = ""
    generator_api_key: str = ""
    generator_max_tokens: int = 512
    generator_temperature: float = 0.7
    generator_request_timeout_s: float = 60.0

    # ── API authentication (Phase 2+) ──────────────────────────────────────
    api_keys: str = ""

    # ── CORS ───────────────────────────────────────────────────────────────
    cors_origins: str = ""

    # ── Reverse proxy / Tailscale ──────────────────────────────────────────
    huible_domain: str = "localhost"
    tailscale_enabled: bool = False
    tailscale_funnel_domain: str = ""

    @field_validator("huible_log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = v.strip().upper()
        return v

    # --- derived views -------------------------------------------------------

    @property
    def log_level(self) -> str:
        return self.huible_log_level

    @property
    def is_development(self) -> bool:
        return self.huible_env.strip().lower() == "development"

    @property
    def effective_database_url(self) -> str:
        """Return the asyncpg postgres URL to use, or ``""`` when not configured.

        A ``database_url`` whose scheme is not in :data:`_HUIBLE_DB_SCHEMES`
        (e.g. a plain ``postgres://`` control-plane URL) is ignored so the
        server never dials a foreign database by accident. When ``database_url``
        is empty but the individual ``POSTGRES_*`` fields are populated, the URL
        is assembled from them (matching the ``.env.example`` template).
        """
        url = (self.database_url or "").strip()
        if url:
            if "://" in url:
                scheme = url.split("://", 1)[0].lower()
                if scheme not in _HUIBLE_DB_SCHEMES:
                    logger.debug("Ignoring database_url with non-asyncpg scheme %r", scheme)
                    return ""
                return url
            return ""
        if self.postgres_user and self.postgres_db:
            return (
                f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
            )
        return ""

    @property
    def cors_origins_list(self) -> list[str]:
        raw = (self.cors_origins or "").strip()
        if not raw:
            return []
        return [o.strip() for o in raw.split(",") if o.strip()]

    @property
    def api_keys_list(self) -> list[str]:
        raw = (self.api_keys or "").strip()
        if not raw:
            return []
        return [k.strip() for k in raw.split(",") if k.strip()]

    def to_generator_config(self) -> GeneratorConfig:
        """Build a :class:`GeneratorConfig` from these settings.

        Reuses :meth:`GeneratorConfig.from_env` so provider parsing, numeric
        coercion, and the mock-fallback rule stay in one place.
        """
        from huible.persona.generator import GeneratorConfig

        return GeneratorConfig.from_env(
            {
                "GENERATOR_PROVIDER": self.generator_provider,
                "GENERATOR_BASE_URL": self.generator_base_url,
                "GENERATOR_MODEL": self.generator_model,
                "GENERATOR_API_KEY": self.generator_api_key,
                "GENERATOR_MAX_TOKENS": str(self.generator_max_tokens),
                "GENERATOR_TEMPERATURE": str(self.generator_temperature),
                "GENERATOR_REQUEST_TIMEOUT_S": str(self.generator_request_timeout_s),
            }
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide cached :class:`Settings` instance."""
    return Settings()

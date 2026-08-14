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
from uuid import UUID

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from huible.llm.client import LLMConfig
    from huible.persona.generator import GeneratorConfig

logger = logging.getLogger(__name__)

__all__ = ["Settings", "get_settings"]

#: Schemes that identify a Huible async postgres URL. Plain ``postgres://``
#: without a driver is intentionally excluded so a foreign control-plane
#: ``DATABASE_URL`` leaking into the environment can never be mistaken for the
#: Huible memory database — the server simply stays on the key-free path.
_HUIBLE_DB_SCHEMES = frozenset({"postgresql+asyncpg", "postgres+asyncpg"})

#: Driver suffixes in :attr:`_HUIBLE_DB_SCHEMES` that get swapped to the sync
#: psycopg driver when deriving :meth:`Settings.effective_safety_database_url`.
#: The §7.4 safety backends (handoff / consent / conversation) are intentionally
#: synchronous so the chat endpoint's G1 path stays pre-generation (§7.1 G1);
#: they therefore use ``postgresql+psycopg`` instead of the asyncpg memory URL.
_ASYNC_TO_SYNC_DRIVER = {
    "postgresql+asyncpg": "postgresql+psycopg",
    "postgres+asyncpg": "postgres+psycopg",
}


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

    # ── LLM client (runtime generation: OpenRouter / Gemini) ────────────────
    # Key-free default (``fake``) so the chat endpoint and tests run without a
    # hosted key. Mirrors the ``EMBEDDING_PROVIDER=fake`` convention. Real
    # providers are gated on their key at ``build_llm_client`` time (HU-1405).
    llm_provider: str = "fake"
    openrouter_api_key: str = ""
    openrouter_base_url: str = ""
    openrouter_model: str = ""
    gemini_api_key: str = ""
    gemini_base_url: str = ""
    gemini_model: str = ""
    llm_model: str = ""
    llm_max_tokens: int = 512
    llm_temperature: float = 0.7
    llm_request_timeout_s: float = 60.0

    # ── API authentication (Phase 2+) ──────────────────────────────────────
    api_keys: str = ""

    # ── CORS ───────────────────────────────────────────────────────────────
    cors_origins: str = ""

    # ── Reverse proxy / Tailscale ──────────────────────────────────────────
    huible_domain: str = "localhost"
    tailscale_enabled: bool = False
    tailscale_funnel_domain: str = ""

    # ── Human-handoff (crisis escalation) queue — §7.4.1 ───────────────────
    # Pre-real-user clinical gate (HU-1421 / HU-1407 §7.4 #1). When a G1
    # crisis signal fires on a persona-chat turn, the turn is routed into a
    # staffed-responder queue with a defined, monitored SLA and a fail-safe
    # that degrades to the G1 non-persona safe response when no human is
    # available (never drops, never the persona voice). Defaults reflect the
    # honest pre-real-user posture: SLA target 5 minutes and **zero** staffed
    # responders → every escalation degrades to G1 (the clinically correct
    # fail-safe until a roster exists) while still being audited. There is no
    # "disable handoff" knob: §10.1 invariant 5 requires auditing *every*
    # escalation, so the queue always runs and the responder count is the only
    # operational lever.
    handoff_sla_target_seconds: int = 300
    handoff_available_responders: int = 0
    handoff_responder_pool: str = ""
    # Coverage-hours gate for the §7.4.1 queue — funding-independent plumbing
    # (HU-1428 AC #2). Even when responders are staffed, escalations outside
    # the configured coverage window degrade to the G1 safe response rather
    # than promising a person who is off-shift (§10.1 #2/#4). Default ``always``
    # (24/7) preserves today's single-lever behaviour; switching to ``hours``
    # with open/close + tz enables the bounded window recorded in the §7.4.1
    # coverage-hours decision (AC #1).
    handoff_coverage_mode: str = "always"
    handoff_coverage_tz: str = "UTC"
    handoff_coverage_open_hour: int = 0
    handoff_coverage_close_hour: int = 24
    # On-call paging transport — Stage 0.4 wire (HU-1450). The alert→on-call
    # paging link deferred from HU-1446 lands here: once the roster is named
    # (HU-1447), an enqueued crisis ticket pages a real person. Key-free
    # default (``log``) emits a structured ``handoff.page`` CRITICAL log line
    # the operator scrapes/alerts on without external credentials — mirrors the
    # ``llm_provider`` / ``generator_provider`` key-free-default convention.
    # ``webhook`` POSTs to ``HANDOFF_PAGER_WEBHOOK_URL`` (Slack incoming
    # webhook / PagerDuty Events API v2 style), falling back to the log line
    # when the URL is empty so credentials land at deploy time.
    handoff_pager_provider: str = "log"
    handoff_pager_webhook_url: str = ""
    # ── Stage 0.4a Sev-1 paging channel (HU-1451) ───────────────────────────
    # The four §3 Sev-1 triggers (crisis enqueue + ack-SLA miss + un-grounded
    # claim leak + degraded net + consent bypass) page a real human channel,
    # not merely a log line. The on-call contact map + the canary T+0 clock
    # resolve the current-window primary + secondary (+ CEO on miss); the
    # multi-channel pager fans the page out to them. Telnyx is the SMS
    # transport (the inbound side already speaks the Telnyx webhook); email is
    # the second channel. All keys default empty so the key-free LoggingPager
    # stays the honest pre-deploy posture — credentials land at deploy time.
    # JSON: seat_id -> {"phone": "+1...", "email": "..."}. Seats are the
    # [HU-1447] §1 roster ids: huible-pm, huible-tech-lead, clinical-advisor, ceo.
    handoff_oncall_contacts: str = ""
    # ISO-8601 canary T+0 clock (the first real grieving-user turn). The 4x12h
    # rotation is anchored here. Empty → roster unconfigured → log fallback.
    handoff_canary_start_ts: str = ""
    # Telnyx SMS outbound (Messaging API). Empty key/from → SMS channel omitted.
    telnyx_api_key: str = ""
    telnyx_from: str = ""
    telnyx_api_base_url: str = "https://api.telnyx.com/v2"
    # Email (SMTP relay) outbound. Empty host → email channel omitted.
    handoff_pager_smtp_host: str = ""
    handoff_pager_smtp_port: int = 587
    handoff_pager_smtp_user: str = ""
    handoff_pager_smtp_password: str = ""
    handoff_pager_email_from: str = ""

    # ── G8 risk-flag enforcement — §7.4.4 dosage cap ──────────────────────
    # Pre-real-user clinical gate (HU-1424 / HU-1407 §7.4 #4). The reserved
    # ``risk_flags`` / ``session_meta`` surfaces MUST change runtime behavior
    # before real grieving-user traffic flows over the chat path. The dosage
    # cap is the per-session turn ceiling above which the binding action
    # escalates to ``pause_session`` (matrix §3): surface support, end the
    # persona turn, require explicit re-entry — never auto-continue. Default
    # is a conservative ceiling; a clinically-tuned cap lands with the ops
    # follow-up that owns the real dosage policy. Set to ``0`` to disable the
    # cap-driven pause (the session-signal surface still fires for the other
    # §3 signals: distress trend + crisis history).
    risk_dosage_cap_turns: int = 20

    # ── Real-user persona-chat ramp gate / kill switch (Stage 0.1, HU-1444) ──
    # Operational kill switch for grieving-user traffic on
    # ``POST /api/v1/chat/{persona_id}``. The §7.4 *clinical* gate being closed
    # does not by itself make a real-user flip safe (HU-1436 rollout plan §0):
    # there must also be an in-band, fast, unilateral OFF that refuses grieving-
    # user turns without a deploy. ``off`` (the default) refuses real-user turns
    # with a warm non-persona response — never the deceased-persona voice;
    # ``canary`` allows only the personas in ``persona_chat_canary_personas``;
    # ``open`` allows all. Internal/synthetic traffic
    # (``X-Huible-Traffic-Class: internal``) is unaffected in every mode so the
    # test suite and synthetic probes keep running when the switch is off. One
    # env flip to ``off`` is the documented ramp-rollback action (plan §4);
    # settings are process-cached so a flip requires a container restart —
    # documented in the runbook. Unknown/blank values default to ``off`` (safe
    # direction).
    persona_chat_real_user_mode: str = "off"
    # Comma-separated persona UUIDs permitted when mode = ``canary``.
    persona_chat_canary_personas: str = ""

    # ── Real-user hard kill switch (Stage 0.7, HU-1462 — MANDATORY) ──────────
    # The PRIMARY rollback path (launch plan §4.2). Distinct from the ramp gate
    # above: this is a hard boolean that refuses *every* real-user turn with
    # HTTP 503 ``SERVICE_DISABLED``, independent of key-revocation propagation.
    # ``on``/``true``/``1``/``yes`` (case-insensitive) permit real-user traffic
    # (still subject to the ramp gate); anything else (default ``off``) refuses
    # it with 503. Crisis/handoff audit still records on a refused turn (§10.1
    # invariant 5), and internal/synthetic traffic is unaffected — so the test
    # suite, probes, and the rollback dry-run (§4.3) keep running while real
    # grieving-user traffic is hard-stopped. One env flip to ``off`` is the
    # documented emergency rollback action; settings are process-cached so a
    # flip requires a container restart (same as the ramp gate).
    persona_chat_real_user_traffic: str = "off"

    @field_validator("huible_log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = v.strip().upper()
        return v

    @field_validator("embedding_provider", mode="before")
    @classmethod
    def _normalize_embedding_provider(cls, v: Any) -> Any:
        # Blank (unset-in-template but present, e.g. ``EMBEDDING_PROVIDER=`` in
        # a staged env file) means "not configured" — the documented key-free
        # default ``fake`` applies rather than an explicit empty string.
        if isinstance(v, str) and not v.strip():
            return "fake"
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
    def effective_safety_database_url(self) -> str:
        """Return the sync (psycopg) postgres URL for the §7.4 safety backends.

        The §7.4 surfaces (handoff queue, consent gate, conversation / crisis
        state) are intentionally synchronous so the chat endpoint's G1 path
        stays pre-generation (§7.1 G1). They therefore use a sync psycopg
        driver instead of the asyncpg memory URL. Derived from the same source
        as :meth:`effective_database_url` so there is one DB config surface:
        the async URL has its driver swapped to ``postgresql+psycopg``. Returns
        ``""`` when no DB is configured (the in-memory defaults stay in place).
        """
        async_url = self.effective_database_url
        if not async_url:
            return ""
        for async_driver, sync_driver in _ASYNC_TO_SYNC_DRIVER.items():
            if async_url.startswith(async_driver + "://"):
                return sync_driver + async_url[len(async_driver):]
        # Defensive: an accepted async URL did not match a known swap — refuse
        # to invent a sync URL rather than risk dialing with the wrong driver.
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

    @property
    def handoff_responder_pool_list(self) -> list[str]:
        """Comma-separated staffed responder ids (the on-call roster)."""
        raw = (self.handoff_responder_pool or "").strip()
        if not raw:
            return []
        return [r.strip() for r in raw.split(",") if r.strip()]

    @property
    def persona_chat_canary_personas_set(self) -> frozenset[UUID]:
        """Persona UUIDs permitted under ``persona_chat_real_user_mode = canary``."""
        raw = (self.persona_chat_canary_personas or "").strip()
        if not raw:
            return frozenset()
        allowed: set[UUID] = set()
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                allowed.add(UUID(part))
            except ValueError:
                logger.warning(
                    "Ignoring non-UUID PERSONA_CHAT_CANARY_PERSONAS entry: %r", part
                )
        return frozenset(allowed)

    @property
    def persona_chat_real_user_traffic_enabled(self) -> bool:
        """Whether the hard kill switch (``PERSONA_CHAT_REAL_USER_TRAFFIC``) is ON.

        ``True`` only for an explicit ON spelling; empty/unknown → ``False``
        (OFF is the load-bearing safe default, HU-1462 §4.2). When OFF, the
        chat path refuses every real-user turn with HTTP 503 while internal
        traffic and crisis/handoff audit continue unaffected.
        """
        from huible.api.real_user_gate import parse_real_user_traffic_switch

        return parse_real_user_traffic_switch(self.persona_chat_real_user_traffic)

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

    def to_llm_config(self) -> LLMConfig:
        """Build an :class:`LLMConfig` from these settings.

        Reuses :meth:`LLMConfig.from_env` so provider parsing, the fake fallback,
        and numeric coercion stay in one place with :mod:`huible.llm.client`.
        """
        from huible.llm.client import LLMConfig

        return LLMConfig.from_env(
            {
                "LLM_PROVIDER": self.llm_provider,
                "OPENROUTER_API_KEY": self.openrouter_api_key,
                "OPENROUTER_BASE_URL": self.openrouter_base_url,
                "OPENROUTER_MODEL": self.openrouter_model,
                "GEMINI_API_KEY": self.gemini_api_key,
                "GEMINI_BASE_URL": self.gemini_base_url,
                "GEMINI_MODEL": self.gemini_model,
                "LLM_MODEL": self.llm_model,
                "LLM_MAX_TOKENS": str(self.llm_max_tokens),
                "LLM_TEMPERATURE": str(self.llm_temperature),
                "LLM_REQUEST_TIMEOUT_S": str(self.llm_request_timeout_s),
            }
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide cached :class:`Settings` instance."""
    return Settings()

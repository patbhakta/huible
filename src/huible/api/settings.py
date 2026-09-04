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

    # ── Embedding provider (W1 makes the setting live; HU-2309 v1.8 M-0R-A) ─
    # ``legacy`` / ``fake`` → Stage-1 token-hash at the 1536 schema dim
    # (pre-W1 behavior, byte-identical). ``local_onnx`` → CPU ONNX
    # bge-small-en-v1.5 at 384 dims (the W1 cutover provider; requires the
    # one-window schema migration + re-embed to run first).
    embedding_provider: str = "fake"
    openai_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"
    # Local ONNX lane (only read when ``embedding_provider == local_onnx``).
    embeddings_model: str = "BAAI/bge-small-en-v1.5"

    @property
    def embedding_schema_dim(self) -> int:
        """Vector dim the schema + query path must agree on (HU-1435 contract)."""
        from huible.embeddings import provider_dim

        return provider_dim(self.embedding_provider)

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
    # Provider-dialect payload extras (e.g. zai/glm thinking on/off) forwarded
    # verbatim into the generator request payload; invalid JSON fails loudly
    # at startup. See ``huible.persona.generator._extra_from_env``.
    generator_extra_json: str = ""

    # ── LLM client (runtime generation: OpenRouter / Gemini) ────────────────
    # Key-free default (``fake``) so the chat endpoint and tests run without a
    # hosted key. Mirrors the ``EMBEDDING_PROVIDER=fake`` convention. Real
    # providers are gated on their key at ``build_llm_client`` time (HU-1405).
    llm_provider: str = "fake"
    openrouter_api_key: str = ""
    openrouter_base_url: str = ""
    openrouter_model: str = ""
    # Board-approved hard monthly cap in USD (HU-1774 sweep 2026-08-18, item
    # 3); ``<= 0`` disables local cap enforcement. Durable spend ledger path
    # is bind-mounted in docker-compose.yml (app-state volume).
    openrouter_monthly_budget_usd: float = 50.0
    openrouter_spend_state_path: str = "/var/lib/huible/openrouter-spend.json"
    gemini_api_key: str = ""
    gemini_base_url: str = ""
    gemini_model: str = ""
    # ── zai (GLM) — day-1 board-approved persona voice (HU-1910 / HU-1461) ─
    # Existing-subscription OpenAI-compatible coding endpoint. ``ZAI_API_KEY``
    # falls back to ``GLM_API_KEY`` (the subscription credential name) inside
    # ``LLMConfig.from_env``. Guardrails: hard per-UTC-day token ceiling
    # (``<= 0`` disables) on a durable ledger bind-mounted at /var/lib/huible,
    # plus a one-knob abort back to ``llm_provider=fake``.
    zai_api_key: str = ""
    zai_base_url: str = ""
    zai_model: str = ""
    zai_daily_token_limit: int = 200000
    zai_token_state_path: str = "/var/lib/huible/zai-tokens.json"
    # glm thinking shares max_tokens; persona voice opts out by default.
    zai_thinking: str = "disabled"
    llm_model: str = ""
    llm_max_tokens: int = 512
    llm_temperature: float = 0.7
    llm_request_timeout_s: float = 60.0
    # ── Product-key separation + BYOK (HU-2243, founder directive Aug 30) ──
    # The persona voice (product traffic) moves to a DEDICATED provider API
    # key so usage/billing are trackable separately from internal workloads.
    # ``persona_llm_provider`` empty (default) = inherit the shared
    # ``llm_provider`` config — today's single-key posture, zero deploy risk.
    # Set (e.g. ``PERSONA_LLM_PROVIDER=zai`` + ``PERSONA_LLM_API_KEY=...``)
    # and the product surface builds its client from the overlay below while
    # internal workloads keep the shared/board key. Guardrail ledgers (zai
    # daily-token, OpenRouter monthly spend) stay shared-path until the
    # dedicated-key provider decision lands; per-workload cost visibility
    # comes from the llm_usage metering rows (key_source split), not the
    # quota ledgers.
    persona_llm_provider: str = ""
    persona_llm_api_key: str = ""
    persona_llm_model: str = ""
    persona_llm_base_url: str = ""
    # BYOK hook gate (default-off, same posture as the kill switch / ramp
    # gates): when armed, a chat request may carry ``X-Provider-Key`` and the
    # turn runs on the client's own provider key (same provider/model as the
    # product voice), attributed per-key in llm_usage with
    # ``key_source='byok'``. Construction failure or absence falls back to
    # the house key — BYOK never breaks a turn.
    byok_enabled: bool = False
    # BYOK vault master secret (HU-2243 Sprint 3): derives the AES-256-GCM
    # row keys that seal tenant provider keys in ``byok_keys`` (migration
    # 006). Empty = vault disabled (management endpoints 403, resolver skips
    # the vault leg — header BYOK still works). Generate with
    # `openssl rand -hex 32`; losing it invalidates every stored tenant key
    # (tenants re-register; house key serves meanwhile).
    byok_vault_master_key: str = ""
    # Persona-voiced turns on the texting channel get a per-turn ceiling
    # below the raw LLM budget (HU-1911 human-touch gate, rubric #3:
    # hosted generators default to essay-length replies). Corpus-derived
    # 2026-08-30 spec: the persona's own lines (friends-v2.csv) run median
    # 44ch / p90 129ch, so ~64 tokens covers the p90 banter register while
    # clipping essay drift; rare sincere pivots (~300ch) fit marginally.
    # ``llm_max_tokens`` stays generous for provider headroom; this cap is
    # applied at the persona-chat call site so non-persona consumers keep
    # the full budget.
    persona_chat_max_tokens: int = 64

    # ── W4 working memory (HU-2309 v1.8 §1.7.2 / M-0R-B) ───────────────────
    # TencentDB as real working memory in the chat path (BEAM Arm A read
    # path, HU-1899/HU-1912 lineage): per-turn recall of the session-gist
    # digest + session-scoped verbatim excerpts, and capture of every
    # completed persona turn to the store. Kills the RC-3 HISTORY_WINDOW
    # eviction failure (E0 turn-34 "what was the first thing I said?"
    # answered wrong). Default off (pre-W4 behavior unchanged); failures
    # degrade to "no working memory this turn" — the lane never breaks a
    # clinical turn. Session keys are namespaced ``huible-`` and scoped per
    # (persona, conversation) per the 2026-08-16 contamination doctrine.
    working_memory_enabled: bool = False
    working_memory_base_url: str = "http://127.0.0.1:8420"
    # Gateway API key (server.apiKey in tdai-gateway.yaml); empty = gateway
    # auth off (local standalone default).
    working_memory_api_key: str = ""
    working_memory_service_id: str = "default"
    working_memory_timeout_s: float = 10.0

    # ── W5 persona tools (HU-2309 v1.8 §1.7.2 / M-0R-E) ────────────────────
    # Era-gated tool lanes. All three are deterministic + local (no network,
    # no LLM, no spend), so unlike the W4 lane they default ON:
    #   era_clock — the in-world era clock line in the system prompt (the
    #     persona's "today" pins to era_knowledge_boundary; fail-closed).
    #   caretaker — the §1.6b caretaker channel: date/time-class questions
    #     get a clearly-labeled out-of-persona answer from the real clock.
    #     CA C2: the caretaker stays INSIDE the G-path — the branch sits
    #     after the G1 crisis pre-check, the G6 consent gate, and the G8
    #     risk-flag enforcement in the chat handler, so crisis disclosures
    #     arriving at the caretaker channel route to G1 handling
    #     (out-of-voice ≠ out-of-safety-stack).
    #   interest_tool — the hobby/interest lane: interest-shaped turns are
    #     grounded in the persona's own era-admissible preference/fact vault
    #     lines (the vault-derived interest/topic map, W1 retrieval feeds it).
    era_clock_enabled: bool = True
    caretaker_channel_enabled: bool = True
    interest_tool_enabled: bool = True

    # ── API authentication (Phase 2+) ──────────────────────────────────────
    api_keys: str = ""

    # ── Durable telemetry log sink (HU-1945) ───────────────────────────────
    # The daily-review runbook reads the stdout telemetry surfaces
    # (chat.trace / consent.record / handoff.page) over a trailing 24h window,
    # but docker json-file history dies with the container on every recreate.
    # These lines are therefore mirrored to a rotating file under the
    # bind-mounted app-state volume (docker-compose.yml) so telemetry survives
    # container recreations. Empty string disables the sink (stdout-only);
    # an unwritable path degrades gracefully to stdout-only at startup.
    telemetry_log_path: str = "/var/lib/huible/logs/telemetry.log"
    telemetry_log_max_bytes: int = 20 * 1024 * 1024
    telemetry_log_backup_count: int = 4

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
    # Committed coverage days for the bounded window (HU-2110): ISO weekdays
    # (1=Mon .. 7=Sun) or day names, single values or comma/range lists —
    # e.g. ``mon-fri``, ``1,3,5``, ``sun``. Empty (default) = every day,
    # preserving time-of-day-only behaviour. Escalations on off days degrade
    # to the G1 safe response — a responder is never paged on an uncommitted
    # day. Parsed by ``huible.safety.handoff.parse_coverage_days``.
    handoff_coverage_days: str = ""
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
    # Rolling window (seconds) for the §3.1 telemetry *gauges* mirrored at
    # /metrics (huible_handoff_* — HU-1865). The alerting gauges reflect
    # current queue health, not all-time history: an all-time cumulative
    # degrade_rate is permanently pinned above zero by a single historical
    # degrade (observed 2026-08-18 — one pre-staffing no_responder_available
    # degrade held HuibleHandoffDegradeRate at 100% and paged for 25 minutes,
    # HU-1865). Default 24h. The /api/v1/handoff/audit dashboard is
    # unaffected (all-time view).
    handoff_telemetry_window_seconds: int = 86400
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
    # Drill-traffic paging suppression (launch-safety watch item, digest #5 /
    # HU-1428 pre-work). Comma-separated case-insensitive markers; a page
    # whose ticket id / conversation_id / persona_id carries any marker is
    # routed to the LoggingPager even when real channels (Telnyx / SMTP /
    # webhook) are credentialed, and counted on
    # huible_paging_drill_suppressed_total{trigger}. The verification drills
    # (verify-*, probe-full) run with demo--prefixed conversation ids
    # on-box and sess-drill-style ids in the suite — without this, a
    # post-activation drill would ring a real on-call human device. Empty
    # string disables suppression (not recommended while drills run).
    handoff_pager_drill_markers: str = "demo-,drill"

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

    # ── C2 coverage gate (HU-2245, CA floor HU-2244) ─────────────────────────
    # ``PERSONA_CHAT_COVERAGE_ENFORCEMENT`` (on|off, default ``off``): when
    # ``on``, real-user persona-chat turns are admitted only inside the
    # handoff coverage window (the same ``HANDOFF_COVERAGE_*`` settings the
    # §7.4.1 queue uses — single source of truth). Outside the window a
    # real-user turn is refused with the warm non-persona response + 988
    # (never the deceased-persona voice); the crisis classifier still runs in
    # the refusal path so a grieving user in crisis is routed to the handoff
    # queue (§10.1 invariant 5). Internal/synthetic traffic is unaffected.
    # Default ``off`` keeps today's behaviour unchanged until the Stage-1
    # entry activation arms it together with
    # ``HANDOFF_COVERAGE_MODE=hours`` 08:00-22:00 America/New_York (the
    # verdict's C2 window). Settings are process-cached — a flip requires a
    # container restart (same as the ramp gate / kill switch).
    persona_chat_coverage_enforcement: str = "off"

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
                return sync_driver + async_url[len(async_driver) :]
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
                logger.warning("Ignoring non-UUID PERSONA_CHAT_CANARY_PERSONAS entry: %r", part)
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

    @property
    def persona_chat_coverage_enforced(self) -> bool:
        """Whether the C2 coverage gate (``PERSONA_CHAT_COVERAGE_ENFORCEMENT``) is armed.

        ``True`` only for an explicit ON spelling; empty/unknown → ``False``
        (unarmed is the pre-Stage-1 default, HU-2245 — zero behaviour change
        on deploy; the gate is armed at entry activation).
        """
        from huible.api.real_user_gate import parse_real_user_traffic_switch

        return parse_real_user_traffic_switch(self.persona_chat_coverage_enforcement)

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
                "GENERATOR_EXTRA_JSON": self.generator_extra_json,
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
                "OPENROUTER_MONTHLY_BUDGET_USD": str(self.openrouter_monthly_budget_usd),
                "OPENROUTER_SPEND_STATE_PATH": self.openrouter_spend_state_path,
                "GEMINI_API_KEY": self.gemini_api_key,
                "GEMINI_BASE_URL": self.gemini_base_url,
                "GEMINI_MODEL": self.gemini_model,
                "ZAI_API_KEY": self.zai_api_key,
                "ZAI_BASE_URL": self.zai_base_url,
                "ZAI_MODEL": self.zai_model,
                "ZAI_DAILY_TOKEN_LIMIT": str(self.zai_daily_token_limit),
                "ZAI_TOKEN_STATE_PATH": self.zai_token_state_path,
                "ZAI_THINKING": self.zai_thinking,
                "LLM_MODEL": self.llm_model,
                "LLM_MAX_TOKENS": str(self.llm_max_tokens),
                "LLM_TEMPERATURE": str(self.llm_temperature),
                "LLM_REQUEST_TIMEOUT_S": str(self.llm_request_timeout_s),
            }
        )

    def to_persona_llm_config(self) -> LLMConfig:
        """Build the product-voice :class:`LLMConfig` (HU-2243 key separation).

        Starts from the shared config (so guardrails — zai daily-token
        ceiling, OpenRouter monthly budget, request timeouts — inherit) and
        overlays the dedicated product provider/key/model/base-url when
        ``persona_llm_provider`` is set. Empty ``persona_llm_provider``
        returns the shared config unchanged (no separation configured).
        """
        from huible.llm.client import LLMConfig

        provider = self.persona_llm_provider.strip()
        if not provider:
            return self.to_llm_config()
        env = {
            "LLM_PROVIDER": provider,
            "OPENROUTER_API_KEY": self.openrouter_api_key,
            "OPENROUTER_BASE_URL": self.openrouter_base_url,
            "OPENROUTER_MODEL": self.openrouter_model,
            "OPENROUTER_MONTHLY_BUDGET_USD": str(self.openrouter_monthly_budget_usd),
            "OPENROUTER_SPEND_STATE_PATH": self.openrouter_spend_state_path,
            "GEMINI_API_KEY": self.gemini_api_key,
            "GEMINI_BASE_URL": self.gemini_base_url,
            "GEMINI_MODEL": self.gemini_model,
            "ZAI_API_KEY": self.zai_api_key,
            "ZAI_BASE_URL": self.zai_base_url,
            "ZAI_MODEL": self.zai_model,
            "ZAI_DAILY_TOKEN_LIMIT": str(self.zai_daily_token_limit),
            "ZAI_TOKEN_STATE_PATH": self.zai_token_state_path,
            "ZAI_THINKING": self.zai_thinking,
            "LLM_MODEL": self.llm_model,
            "LLM_MAX_TOKENS": str(self.llm_max_tokens),
            "LLM_TEMPERATURE": str(self.llm_temperature),
            "LLM_REQUEST_TIMEOUT_S": str(self.llm_request_timeout_s),
        }
        # Overlay the dedicated product credentials onto the chosen
        # provider's slots; unset slots keep the shared values (operator can
        # split just the key, or key+model+endpoint together).
        provider_vars = {
            "zai": ("ZAI_API_KEY", "ZAI_BASE_URL", "ZAI_MODEL"),
            "openrouter": ("OPENROUTER_API_KEY", "OPENROUTER_BASE_URL", "OPENROUTER_MODEL"),
            "gemini": ("GEMINI_API_KEY", "GEMINI_BASE_URL", "GEMINI_MODEL"),
        }.get(provider.lower())
        if provider_vars is not None:
            key_var, base_var, model_var = provider_vars
            if self.persona_llm_api_key.strip():
                env[key_var] = self.persona_llm_api_key.strip()
            if self.persona_llm_base_url.strip():
                env[base_var] = self.persona_llm_base_url.strip()
            if self.persona_llm_model.strip():
                env[model_var] = self.persona_llm_model.strip()
        return LLMConfig.from_env(env)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide cached :class:`Settings` instance."""
    return Settings()

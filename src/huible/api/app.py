"""FastAPI ASGI app for the Huible memory-driven persona engine.

Server foundation (HU-1403) plus the M2 chat wiring (HU-1401 / HU-1406). Exposes:

* ``GET /health`` — top-level liveness / readiness probe with service +
  version + DB/pgvector connectivity (HU-1403).
* ``GET /api/v1/health`` — same probe under the versioned prefix (HU-1401).
* ``POST /api/v1/chat`` — DEPRECATED (HU-1926): permanent 308 redirect to
  ``/api/v1/chat/{persona_id}``. The generic route no longer generates; it
  exists only so callers wired to the retired surface land on the
  safety-stacked one. It performs no persona generation by construction.
* ``POST /api/v1/chat/{persona_id}`` — the **single** persona chat surface,
  wired to the runtime LLM client (HU-1406 Phase-1 integration milestone:
  text -> retrieval -> LLM -> text) and carrying the full §7.4 safety stack:
  G1 crisis pre-filter, G6 consent gate, the HU-1444 real-user ramp gate +
  HU-1462 kill switch, §7.4.1 handoff escalation, and §7.4.4 risk-flag
  enforcement. Returns a structured ``trace``.

The app factory (:func:`create_app`) wires:

* a pydantic-settings :class:`~huible.api.settings.Settings` instance (``.env``
  driven, key-free defaults) — see ``src/huible/api/settings.py``;
* CORS middleware (origins from ``CORS_ORIGINS``; permissive only in dev);
* structured (JSON-line) logging configured on startup;
* an async lifespan hook that constructs the :class:`PostgresMemoryBackend`
  when an asyncpg ``DATABASE_URL`` is configured and disposes it on shutdown —
  and otherwise runs key-free (no database).

Chat wiring (the M2 priority):

    inbound message
      -> bearer auth (persona-scoped API key)
      -> ContextBuilder (provenance-safe memory -> prompt bridge, HU-1399)
      -> PersonaGeneratorClient (the speaking voice, HU-1400) / LLMClient (HU-1405)
      -> reply + retrieval trace

The chat path reads **only** HIGH/MEDIUM L1 memories. The ContextBuilder is the
only sanctioned bridge and hard-excludes LOW / QUARANTINE / missing-confidence
memories before the generator ever sees them. The response surfaces the
admissible activated memories and exclusion counts so callers and tests can
prove the contamination guard fired.

The remaining spec endpoints (memories CRUD, retrieve, quarantine adjudication)
land incrementally; health + chat are the M2 priority per the issue.

Construction is dependency-injected via :func:`create_app` so tests wire a
seeded key store, persona registry, and (mock) generator / fake LLM client
without touching production code paths. The module-level :data:`app` is a bare
default instance so ``uvicorn huible.api.app:app`` boots and ``/health``
returns 200; chat will 401 until keys are seeded (correct behavior).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from logging.handlers import RotatingFileHandler
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from huible.api.auth import (
    ApiKeyPrincipal,
    ApiKeyStore,
    InMemoryApiKeyStore,
    InMemoryPersonaRegistry,
    PersonaBinding,
    PersonaRegistry,
    authenticate,
    get_persona_registry,
    raise_forbidden,
)
from huible.api.metrics import (
    ALERT_ONCALL_CONFIGURED,
    CHAT_COVERAGE_REFUSED,
    GENERIC_CHAT_SHIM_REDIRECTS,
    REAL_USER_TRAFFIC_DISABLED,
    ChatTurnOutcome,
    metrics_response,
    record_alignment_judge_overturn,
    record_alignment_unconfirmed_suppression,
    record_chat_turn,
    record_handoff_responder_readiness,
    record_handoff_telemetry,
    record_health_status,
    record_paging_drill_suppressed,
    record_paging_failures,
)
from huible.api.paging import (
    PAGE_SEVERITY_CRISIS,
    PAGE_TRIGGER_CONSENT_BYPASS,
    PAGE_TRIGGER_CRISIS_ENQUEUE,
    PAGE_TRIGGER_DEGRADED_NET,
    PAGE_TRIGGER_UNGROUNDED_LEAK,
    Pager,
    build_multichannel_pager,
    build_roster,
    escalate_sla_breaches,
    page_degraded_net,
    page_sev1_signal,
)
from huible.api.real_user_gate import (
    REAL_USER_MODE_OFF_RESPONSE,
    REAL_USER_TRAFFIC_CLASS_HEADER,
    SERVICE_DISABLED_MESSAGE,
    RealUserMode,
    TrafficClass,
    is_real_user_turn_refused,
    parse_real_user_mode,
    traffic_class_from_header,
)
from huible.api.schemas import (
    ActivatedMemoryView,
    AlignmentView,
    ChatRequest,
    ChatTrace,
    ConsentAcknowledgeData,
    ConsentAcknowledgeRequest,
    ConsentAcknowledgeResponse,
    ConsentCardView,
    DataEnvelope,
    ExcludedMemoryRefView,
    HandoffQueueItemView,
    HandoffResolveRequest,
    HandoffSLAStatusView,
    HandoffTelemetryView,
    HandoffTicketView,
    HealthCheck,
    HealthResponse,
    PersonaChatRequest,
    PersonaChatResponse,
    RiskEnforcementView,
    RiskIntakeAssessmentRequest,
    RiskIntakeData,
    RiskIntakeResponse,
    SafetyEventView,
    SessionMetaView,
)
from huible.api.settings import Settings, get_settings
from huible.llm.client import FakeLLMClient as _FakeLLMClient
from huible.llm.client import (
    LLMBudgetExceededError,
    LLMClient,
    build_llm_client,
)
from huible.memory.protocol import MemoryBackend, MemoryNode
from huible.memory.store import PostgresMemoryBackend
from huible.persona.context import (
    CONFIDENCE_LEVEL_METADATA_KEY,
    ContextBuilder,
    ConversationTurn,
    PersonaConfig,
    RelationshipTier,
)
from huible.persona.generator import PersonaGeneratorClient, make_generator_client
from huible.persona.length import reply_budget_tokens, stats_from_metadata
from huible.safety import (
    PAUSE_SESSION_RESPONSE,
    PROXY_USER_PAUSE_RESPONSE,
    REFUSE_TOPIC_FALLBACK_RESPONSE,
    ConsentCardProvider,
    ConsentGate,
    ConsentNotRecordedError,
    CoverageWindow,
    CrisisClassifier,
    DefaultConsentCard,
    DeterministicCrisisClassifier,
    HandoffOutcome,
    HandoffQueue,
    InMemoryConsentGate,
    InMemoryHandoffQueue,
    InMemoryRiskProfile,
    RiskFlag,
    RiskIntakeAssessment,
    RiskIntakeService,
    RiskProfileProvider,
    RiskSessionSignals,
    UserAffect,
    apply_affect_guard,
    apply_alignment_guard,
    build_reframe_addendum,
    classify_user_message,
    compute_handoff_telemetry,
    enforce_risk_flags,
    escalate_risk_to_human,
    escalate_to_human,
    parse_coverage_days,
    sla_status,
)
from huible.safety.judge import (
    JudgeVerdict,
    adjudicate_alignment_claims,
    build_canon_digest,
    judge_eligible,
)
from huible.safety.store import (
    ConversationStore,
    InMemoryConversationStore,
    PostgresConsentGate,
    PostgresConversationStore,
    PostgresHandoffQueue,
    PostgresRiskProfile,
)

logger = logging.getLogger(__name__)

__all__ = ["app", "configure_logging", "create_app"]

#: Relationship name (request wire) -> requester RelationshipTier (context layer).
_RELATIONSHIP_TO_TIER: dict[str, RelationshipTier] = {tier.value: tier for tier in RelationshipTier}


def _resolve_relationship(relationship: str) -> RelationshipTier:
    """Map a request ``relationship`` to the requester RelationshipTier."""
    tier = _RELATIONSHIP_TO_TIER.get(relationship)
    if tier is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "status": 400,
                    "message": f"Invalid relationship: {relationship!r}",
                }
            },
        )
    return tier


def _safe_version() -> str:
    try:
        return _pkg_version("huible")
    except PackageNotFoundError:  # pragma: no cover - editable install absent
        return "0.0.0+unknown"


# --- structured logging ------------------------------------------------------


class _JsonLineFormatter(logging.Formatter):
    """Minimal single-line JSON formatter (stdlib only, no extra dependency)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S.%fZ"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)


#: Stdout telemetry surfaces the daily-review runbook greps for (HU-1945):
#: risk/alignment/dosage traces, consent acknowledgments, responder pages.
TELEMETRY_LINE_PREFIXES = ("chat.trace ", "consent.record ", "handoff.page ")


class _TelemetrySinkFilter(logging.Filter):
    """Pass only the runbook telemetry lines through to the durable sink."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.getMessage().startswith(TELEMETRY_LINE_PREFIXES)


def _attach_telemetry_file_sink(settings: Settings) -> logging.Handler | None:
    """Mirror telemetry JSON lines to a durable rotating file (HU-1945).

    Stdout (docker json-file) history does not survive container recreations,
    so the daily review's trailing-24h window was wiped on every deploy. The
    sink writes the same ``_JsonLineFormatter`` lines to a rotating file under
    the bind-mounted app-state volume, which is host-durable across recreates.

    Graceful degradation: an empty :attr:`Settings.telemetry_log_path` disables
    the sink, and an unwritable path logs one warning and leaves the app on
    stdout-only logging (never blocks startup).
    """
    path = settings.telemetry_log_path.strip()
    if not path:
        return None
    root = logging.getLogger()
    if any(getattr(h, "_huible_telemetry_sink", False) for h in root.handlers):
        return None
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        handler: logging.Handler = RotatingFileHandler(
            path,
            maxBytes=settings.telemetry_log_max_bytes,
            backupCount=settings.telemetry_log_backup_count,
            encoding="utf-8",
        )
    except OSError:
        logger.warning(
            "telemetry file sink disabled (path not writable): %s", path
        )
        return None
    handler.setFormatter(_JsonLineFormatter())
    handler.addFilter(_TelemetrySinkFilter())
    handler._huible_telemetry_sink = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    logger.info(
        "telemetry file sink active: %s (surfaces survive container recreations)",
        path,
    )
    return handler


def configure_logging(settings: Settings) -> None:
    """Configure structured (JSON-line) root logging from settings.

    Idempotent: only attaches a structured handler once. Called from the app
    lifespan on startup, so the test suite (which does not boot the lifespan)
    is unaffected. Also attaches the durable telemetry file sink (HU-1945)
    when ``telemetry_log_path`` is configured and writable.
    """
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    root = logging.getLogger()
    # levels first so the sink-attach confirmation below is not gated by the
    # root logger's default WARNING level
    root.setLevel(level)
    logging.getLogger("uvicorn").setLevel(level)
    if not any(isinstance(h.formatter, _JsonLineFormatter) for h in root.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(_JsonLineFormatter())
        root.addHandler(handler)
    _attach_telemetry_file_sink(settings)


# --- lifespan + memory store -------------------------------------------------


async def _init_memory_backend(settings: Settings) -> MemoryBackend | None:
    """Construct the async memory backend when an asyncpg DB URL is configured.

    Returns ``None`` (key-free / mock path) when no usable database is
    configured. Construction is lazy (``create_async_engine`` does not connect),
    so startup stays fast; connectivity is probed on demand by ``/health``.
    """
    url = settings.effective_database_url
    if not url:
        return None
    try:
        return PostgresMemoryBackend(url)
    except Exception:  # pragma: no cover - defensive, misconfiguration only
        logger.exception("failed to construct memory backend; continuing without it")
        return None


def _coverage_from_settings(settings: Settings) -> CoverageWindow:
    """Build the §7.4.1 coverage window from settings (single source of truth).

    Shared by every backend-construction branch so the queue's degrade
    decisions, the paging label, and ops config cannot drift apart. The day
    spec (``handoff_coverage_days``, HU-2110) is optional and defaults to
    every day.
    """
    return CoverageWindow(
        mode=settings.handoff_coverage_mode,
        tz_name=settings.handoff_coverage_tz,
        open_hour=settings.handoff_coverage_open_hour,
        close_hour=settings.handoff_coverage_close_hour,
        days=parse_coverage_days(settings.handoff_coverage_days),
    )


def _init_safety_backends(
    settings: Settings,
) -> tuple[HandoffQueue, ConsentGate, ConversationStore, RiskProfileProvider, list]:
    """Construct durable §7.4 backends when a sync DB URL is configured.

    Returns ``(queue, consent_gate, conversation_store, risk_profile,
    disposables)``. When no sync URL is configured (the key-free default),
    returns in-memory defaults and an empty disposables list — the
    pre-real-user posture. Construction is lazy (``create_engine`` does not
    connect), so startup stays fast; the first request exercises connectivity.
    The disposables are disposed in the lifespan shutdown hook (HU-1440).
    """
    url = settings.effective_safety_database_url
    if not url:
        return (
            InMemoryHandoffQueue(
                available_responders=settings.handoff_available_responders,
                responder_id_pool=tuple(settings.handoff_responder_pool_list),
                sla_target_seconds=settings.handoff_sla_target_seconds,
                coverage=_coverage_from_settings(settings),
            ),
            InMemoryConsentGate(),
            InMemoryConversationStore(),
            InMemoryRiskProfile(),
            [],
        )
    try:
        queue = PostgresHandoffQueue(
            url,
            available_responders=settings.handoff_available_responders,
            responder_id_pool=tuple(settings.handoff_responder_pool_list),
            sla_target_seconds=settings.handoff_sla_target_seconds,
            coverage=_coverage_from_settings(settings),
        )
        consent_gate = PostgresConsentGate(url)
        conversation_store = PostgresConversationStore(url)
        risk_profile = PostgresRiskProfile(url)
        logger.info("durable §7.4 safety backends wired (handoff/consent/conversation/risk)")
        return (
            queue,
            consent_gate,
            conversation_store,
            risk_profile,
            [queue, consent_gate, conversation_store, risk_profile],
        )
    except Exception:  # pragma: no cover - defensive, misconfiguration only
        logger.exception("failed to construct durable safety backends; falling back to in-memory")
        return (
            InMemoryHandoffQueue(
                available_responders=settings.handoff_available_responders,
                responder_id_pool=tuple(settings.handoff_responder_pool_list),
                sla_target_seconds=settings.handoff_sla_target_seconds,
                coverage=_coverage_from_settings(settings),
            ),
            InMemoryConsentGate(),
            InMemoryConversationStore(),
            InMemoryRiskProfile(),
            [],
        )


async def _hydrate_persona_registry(application: FastAPI) -> int:
    """Register persisted personas into the runtime registry at boot (HU-1435).

    The default :class:`InMemoryPersonaRegistry` starts empty and nothing in
    the request path reads the ``personas`` table, so without this hook a
    DB-seeded deploy serves ``404 PERSONA_NOT_FOUND`` for every chat turn —
    the exact gap the real-user flip verification caught on prod. Best-effort:
    a DB failure logs and leaves the registry empty (chat 404s, health still
    reports the DB check) rather than crashing startup. Skipped when the
    registry was injected pre-seeded (tests, harnesses) — hydration only
    fills the empty default.
    """
    settings: Settings = application.state.settings
    registry = application.state.persona_registry
    backend: MemoryBackend | None = getattr(application.state, "memory_backend", None)
    url = settings.effective_database_url
    try:
        registry_preseeded = len(registry) > 0
    except TypeError:  # exotic injected registry: assume caller-managed
        registry_preseeded = True
    if not url or backend is None or registry_preseeded:
        return 0
    import asyncpg

    try:
        # asyncpg rejects SQLAlchemy driver suffixes (``postgresql+asyncpg``).
        dsn = url.split("://", 1)
        url = f"postgresql://{dsn[1]}" if len(dsn) == 2 else url
        conn = await asyncpg.connect(url)
    except Exception:  # pragma: no cover - defensive: DB down at boot
        logger.exception("persona registry hydration: DB connect failed; chat will 404")
        return 0
    try:
        rows = await conn.fetch(
            "SELECT id, name, voice_instructions, era_knowledge_boundary,"
            " age_at_death, death_date, metadata FROM personas"
        )
        await conn.close()
    except Exception:  # pragma: no cover - defensive: schema not migrated yet
        logger.exception("persona registry hydration: query failed; chat will 404")
        with contextlib.suppress(Exception):
            await conn.close()
        return 0
    for row in rows:
        era = row["era_knowledge_boundary"]
        death = row["death_date"]
        # HU-2231: measured corpus length register lives in the persona
        # record's metadata JSON (written at provision time). asyncpg hands
        # the json column back as a str; parse defensively and fail closed —
        # a missing/garbage block keeps the safe default reply budget.
        raw_metadata = row["metadata"]
        if isinstance(raw_metadata, str):
            with contextlib.suppress(ValueError):
                raw_metadata = json.loads(raw_metadata)
        registry.register(
            PersonaConfig(
                id=row["id"],
                name=row["name"],
                voice_instructions=row["voice_instructions"] or "",
                era_knowledge_boundary=str(era) if era else "2020-01-01",
                age_at_death=row["age_at_death"],
                death_date=str(death) if death else None,
                length_stats=stats_from_metadata(raw_metadata),
            ),
            backend,
        )
    await conn.close()
    if rows:
        logger.info("persona registry hydrated from database: %d persona(s)", len(rows))
    return len(rows)


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Startup/shutdown: configure logging and manage the memory backend."""
    settings: Settings = application.state.settings
    configure_logging(settings)
    application.state.memory_backend = await _init_memory_backend(settings)
    await _hydrate_persona_registry(application)
    try:
        yield
    finally:
        backend: MemoryBackend | None = getattr(application.state, "memory_backend", None)
        close = getattr(backend, "close", None)
        if callable(close):
            try:
                await close()
            except Exception:  # pragma: no cover - best-effort shutdown
                logger.warning("memory backend close failed", exc_info=True)
        # Dispose the sync §7.4 safety backends (HU-1440). Each holds its own
        # engine; close() is idempotent across the list.
        for disposable in getattr(application.state, "safety_disposables", []) or []:
            try:
                disposable.close()
            except Exception:  # pragma: no cover - best-effort shutdown
                logger.warning("safety backend close failed", exc_info=True)


# --- health probe -----------------------------------------------------------


async def _health_data(application: FastAPI) -> HealthCheck:
    """Build the shared health payload: service + version + DB/pgvector + generator.

    DB / pgvector status is ``skipped`` when no memory backend is wired (the
    key-free default), probed live when one is, and ``degraded`` overall when a
    wired backend is unreachable.
    """
    checks: dict[str, str] = {}
    overall = "ok"

    backend: MemoryBackend | None = getattr(application.state, "memory_backend", None)
    if backend is None:
        checks["database"] = "skipped"
        checks["pgvector"] = "skipped"
    else:
        probe = getattr(backend, "health_check", None)
        if callable(probe):
            try:
                result = await probe()
            except Exception:
                logger.exception("health check probe failed")
                result = {"database": "unhealthy", "pgvector": "unknown"}
            checks["database"] = result.get("database", "unknown")
            checks["pgvector"] = result.get("pgvector", "unknown")
            if checks["database"] != "ok":
                overall = "degraded"
        else:  # pragma: no cover - backend without a health_check hook
            checks["database"] = "ok"
            checks["pgvector"] = "unknown"

    generator = application.state.generator
    provider_label = getattr(generator, "__class__", type(generator)).__name__
    checks["generator"] = "ready (mock)" if "Mock" in provider_label else "ready"

    # OpenRouter monthly spend cap (HU-1461, board decision 2026-08-18):
    # surface month-to-date accrued USD vs the budget as a compact string,
    # matching the pinned loopback health-probe contract (checks values are
    # plain strings like "ready (mock)"). Monitoring alerts on the
    # "exhausted" prefix before the fake-voice fallback trips.
    llm = getattr(application.state, "llm_client", None)
    spend = getattr(llm, "spend", None)
    if spend is not None:
        try:
            snap = spend.snapshot()  # type: ignore[attr-defined]
            state = "exhausted (fake-voice fallback)" if snap["exhausted"] else "ok"
            checks["llm_budget"] = (
                f"{state} ({snap['month_to_date_usd']:.4f}/{snap['budget_usd']:.2f} "
                f"USD, month {snap['month']})"
            )
        except Exception:  # pragma: no cover - defensive; never fail /health
            logger.exception("llm spend snapshot failed")
            checks["llm_budget"] = "unknown"

    uptime = max(0.0, time.time() - float(application.state.start_time))
    return HealthCheck(
        status=overall,
        version=_safe_version(),
        checks=checks,
        uptime_seconds=uptime,
    )


def create_app(
    *,
    api_key_store: ApiKeyStore | None = None,
    persona_registry: PersonaRegistry | None = None,
    generator: PersonaGeneratorClient | None = None,
    llm_client: LLMClient | None = None,
    context_builder: ContextBuilder | None = None,
    crisis_classifier: CrisisClassifier | None = None,
    crisis_resources: dict[str, str] | None = None,
    handoff_queue: HandoffQueue | None = None,
    consent_gate: ConsentGate | None = None,
    consent_card_provider: ConsentCardProvider | None = None,
    risk_profile: RiskProfileProvider | None = None,
    conversation_store: ConversationStore | None = None,
    pager: Pager | None = None,
    settings: Settings | None = None,
    start_time: float | None = None,
) -> FastAPI:
    """Build a FastAPI app with injected dependencies.

    All parameters are optional; sensible defaults let ``uvicorn
    huible.api.app:app`` boot (chat will 401 without seeded keys). Tests pass a
    seeded key store + persona registry and usually the deterministic mock
    generator / fake LLM client.
    """
    resolved_settings = settings or get_settings()

    application = FastAPI(
        title="Huible Memory Engine API",
        version=_safe_version(),
        description=(
            "Memory-driven persona engine REST interface (M2: health + chat). "
            "See docs/07-api-specification.md for the full contract."
        ),
        lifespan=lifespan,
    )

    # CORS: explicit origins are credentialed; otherwise permissive without
    # credentials (browsers reject credentials alongside a "*" origin).
    origins = resolved_settings.cors_origins_list
    if origins and origins != ["*"]:
        allow_origins = origins
        allow_credentials = True
    else:
        allow_origins = ["*"]
        allow_credentials = False
    application.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    application.state.settings = resolved_settings
    application.state.api_key_store = api_key_store or InMemoryApiKeyStore()
    application.state.persona_registry = persona_registry or InMemoryPersonaRegistry()
    application.state.generator = generator or make_generator_client(
        resolved_settings.to_generator_config()
    )
    application.state.llm_client = llm_client or build_llm_client(resolved_settings.to_llm_config())
    application.state.context_builder = context_builder or ContextBuilder()
    # HU-2070: TTL cache for the persona-scope §7.4.2 grounding corpus (see
    # ``_persona_scope_grounding_refs``). Keyed by persona + disclosure scope
    # + era boundary; per-app so tests with fresh registries never share
    # entries.
    application.state.grounding_scope_cache: dict = {}
    # Runtime clinical guardrails (HU-1413 / HU-1407 §7.3). The crisis
    # classifier is the G1 synchronous pre-generation check AND the shared G3
    # affect signal. The deterministic impl is the default; tests inject a
    # pinned instance. ``crisis_resources`` makes the warm-escalation line a
    # config swap (regional line / human-handoff queue) rather than a re-build.
    application.state.crisis_classifier = crisis_classifier or DeterministicCrisisClassifier()
    application.state.crisis_resources = crisis_resources or {}
    # §7.4.1 / §7.4.3 / §7.4.4 durable safety backends (HU-1440). When an
    # explicit backend is injected (tests, or a caller wiring a custom store),
    # it wins. Otherwise the backends are constructed from settings: durable
    # Postgres-backed implementations when a sync safety DB URL is configured,
    # in-memory defaults otherwise. The §10.1 invariant 5 audit log, the
    # "a person will join you right now" promise, the dosage-cap turn count,
    # the crisis-history marker, and the G8 risk profile all survive a
    # container restart when the durable backends are wired. Disposables
    # (engines) are closed in lifespan.
    (
        durable_queue,
        durable_gate,
        durable_store,
        durable_risk_profile,
        safety_disposables,
    ) = _init_safety_backends(resolved_settings)
    application.state.handoff_queue = handoff_queue or durable_queue
    application.state.consent_gate = consent_gate or durable_gate
    # §7.4.3 consent card content. The DefaultConsentCard ships the
    # clinically-approved revision 3 copy (HU-1441, swapped per HU-1438 §4 over
    # the Onboarding Agent's drafted revision 2 in HU-1429); a future
    # clinically-revised revision swaps in via consent_card_provider without
    # touching the gate. The deceased persona never voices the consent
    # (§7.1 H1) — the card is a non-persona system message, structurally
    # disjoint from generation.
    application.state.consent_card_provider = consent_card_provider or DefaultConsentCard()
    # Per-session conversation + crisis state. The durable store survives
    # restarts so §7.4.4 dosage-cap + crisis-history enforcement stays correct;
    # the in-memory default is the pre-real-users fallback (HU-1440).
    application.state.conversation_store = conversation_store or durable_store
    # When any backend is explicitly injected, the durable disposables are not
    # owned by this app (the caller owns their lifecycle); otherwise this app
    # owns the engines it constructed and disposes them on shutdown.
    application.state.safety_disposables = (
        []
        if (handoff_queue or consent_gate or conversation_store or risk_profile)
        else safety_disposables
    )
    # §7.4.4 G8 risk-flag enforcement. The risk profile is the intake-derived
    # source of the per-session + per-persona risk flags (loss_of_child,
    # minor_decedent, recent_loss, non_acceptance, proxy_user). The default
    # InMemoryRiskProfile is empty (no flags) so the pre-real-user suite runs
    # key-free and the default persona-chat turn is unaffected; tests inject a
    # seeded instance to exercise each flag → action path. Pre-real-launch the
    # onboarding / intake path populates this (memory-content-derived
    # loss_of_child, persona-age-derived minor_decedent, death-date-derived
    # recent_loss, intake-assessment non_acceptance, identity-verification
    # proxy_user) without touching the chat endpoint. The durable
    # PostgresRiskProfile (HU-1445) survives a restart so a populated profile
    # does not silently go inert and disable G8 enforcement mid-ramp.
    application.state.risk_profile = risk_profile or durable_risk_profile
    application.state.start_time = start_time if start_time is not None else time.time()
    # Default: no DB wired. The lifespan constructs the real backend on startup
    # when an asyncpg DATABASE_URL is configured; health reads this attribute.
    application.state.memory_backend: MemoryBackend | None = None
    # Stage 0.4 wire (HU-1450) + Stage 0.4a Sev-1 paging channel (HU-1451): the
    # on-call paging transport + the gauge flip that marks the §3 Sev-1 alerts
    # as wired to the 0.4 on-call roster. The pager is the missing link deferred
    # from HU-1446: an enqueued crisis ticket now pages a real person. The
    # key-free default (LoggingPager) emits a structured ``handoff.page``
    # CRITICAL log line; a WebhookPager lands at deploy time via
    # HANDOFF_PAGER_WEBHOOK_URL. Paging is additive on top of ENQUEUED — it
    # never bypasses the §10.1 #2 degrade gate.
    #
    # HU-1451: when Telnyx / email / webhook credentials are present, a
    # MultiChannelPager fans every page out to the roster-resolved primary +
    # secondary (+ CEO on ack-SLA miss), so a page reaches a real human device
    # — not merely a log line. The OnCallRoster resolves the active window from
    # the canary-start clock. When credentials are absent the key-free
    # LoggingPager stays the honest pre-deploy posture.
    application.state.oncall_roster = build_roster(
        contacts_json=resolved_settings.handoff_oncall_contacts,
        canary_start_ts=resolved_settings.handoff_canary_start_ts,
    )
    if pager is not None:
        # Explicitly injected (tests) — respect it as-is.
        application.state.pager = pager
    else:
        application.state.pager = build_multichannel_pager(
            provider=resolved_settings.handoff_pager_provider,
            webhook_url=resolved_settings.handoff_pager_webhook_url,
            roster=application.state.oncall_roster,
            telnyx_api_key=resolved_settings.telnyx_api_key,
            telnyx_from=resolved_settings.telnyx_from,
            telnyx_api_base_url=resolved_settings.telnyx_api_base_url,
            smtp_host=resolved_settings.handoff_pager_smtp_host,
            smtp_port=resolved_settings.handoff_pager_smtp_port,
            smtp_user=resolved_settings.handoff_pager_smtp_user,
            smtp_password=resolved_settings.handoff_pager_smtp_password,
            email_from_addr=resolved_settings.handoff_pager_email_from,
            drill_markers=resolved_settings.handoff_pager_drill_markers,
            on_suppressed=record_paging_drill_suppressed,
        )
    application.state.coverage_window_label = _coverage_window_label(resolved_settings)
    # C2 coverage gate (HU-2245, CA floor HU-2244): the same CoverageWindow
    # the handoff queue evaluates (single source of truth) is exposed to the
    # chat path so real-user turns can be refused outside the CA-seat window
    # when PERSONA_CHAT_COVERAGE_ENFORCEMENT=on. ``chat_coverage_now`` is an
    # optional test override for the clock; None → real current time.
    application.state.chat_coverage_window = _coverage_from_settings(resolved_settings)
    application.state.chat_coverage_now = None
    # The gauge wiring target (HU-1446) flips to 1 once the roster is staffed
    # (HANDOFF_AVAILABLE_RESPONDERS>0) — i.e. the §3 Sev-1 alerts are now
    # wired to a real on-call rather than the pre-roster fail-safe. Stays 0
    # in the key-free / pre-roster default so the dashboard honestly reports
    # "alerts will not page anyone" until ops configures the roster.
    if resolved_settings.handoff_available_responders > 0:
        ALERT_ONCALL_CONFIGURED.set(1)
    else:
        ALERT_ONCALL_CONFIGURED.set(0)

    _register_routes(application)
    return application


def _coverage_window_label(settings: Settings) -> str:
    """Build a human-readable label for the active coverage window (HU-1450).

    The label rides on every ``handoff.page`` so the paged operator knows which
    seat / window is active (e.g. ``"always"`` for 24/7 cover, or ``"hours
    mon-fri 09:00-17:00 America/New_York"`` for a bounded window). Derived
    from the same settings that construct the :class:`CoverageWindow` so the
    page matches the queue's degrade decisions.
    """
    if settings.handoff_coverage_mode == "always":
        return "always"
    days = settings.handoff_coverage_days.strip()
    day_prefix = f"{days.lower()} " if days else ""
    return (
        f"hours {day_prefix}{settings.handoff_coverage_open_hour:02d}:00-"
        f"{settings.handoff_coverage_close_hour:02d}:00 {settings.handoff_coverage_tz}"
    )


def _register_routes(application: FastAPI) -> None:
    @application.get(
        "/health",
        response_model=HealthResponse,
        tags=["System"],
        summary="Service health (liveness + readiness)",
    )
    async def health() -> HealthResponse:
        """Top-level health probe (HU-1403).

        Reports service status, version, DB/pgvector connectivity, generator
        readiness, and uptime. ``status`` is ``ok`` unless a wired DB check
        fails (then ``degraded``). When no database is configured the DB /
        pgvector checks report ``skipped`` and the service is still ``ok``.
        """
        return HealthResponse(data=await _health_data(application))

    @application.get(
        "/api/v1/health",
        response_model=HealthResponse,
        tags=["System"],
        summary="Versioned health (alias of /health)",
    )
    async def health_v1() -> HealthResponse:
        """Versioned health probe (spec section 3.4) — same payload as /health."""
        return HealthResponse(data=await _health_data(application))

    @application.post(
        "/api/v1/chat",
        response_model=None,
        status_code=status.HTTP_308_PERMANENT_REDIRECT,
        tags=["Chat"],
        summary="DEPRECATED — 308 to /api/v1/chat/{persona_id} (the single safety-stacked surface)",
        deprecated=True,
    )
    async def chat(
        body: ChatRequest,
        principal: ApiKeyPrincipal = Depends(authenticate),
    ) -> Response:
        """Deprecated generic chat shim — consolidates the chat surface (HU-1926).

        This route previously ran a parallel persona pipeline with **none** of
        the §7.4 safety stack: no G1 crisis pre-filter, no G6 consent gate, no
        HU-1444 real-user ramp gate, no §7.4.1 handoff escalation. A synthetic
        crisis probe reached the persona LLM here unaudited (HU-1911 Stage-A
        dogfood finding 1), so the route no longer generates anything.

        It now answers every authenticated request with HTTP 308
        ``Permanent Redirect`` to ``/api/v1/chat/{persona_id}`` — the
        persona-scoped surface that carries the full G1/G6/ramp-gate/§7.4.1
        stack. 308 preserves the method and body, so redirect-following
        clients re-POST the same JSON to the safety-stacked surface; the
        scoped request contract is ``{"message", "relationship",
        "conversation_id"}`` (an old ``disclosure_tier`` of
        private/family/close_friends/all_contacts maps to the equivalent
        relationship intimate/family/close_friend/acquaintance, and the
        response envelope is the scoped ``{response, trace}`` shape).

        Auth: persona-scoped bearer key (401 when missing/unknown). If the
        request specifies ``persona_id`` it must match the key's scope (403).
        The disclosure tier is still validated here (400) so an invalid tier
        fails fast instead of silently redirecting to a defaulted one.
        """
        target_persona_id = body.persona_id or principal.persona_id
        if target_persona_id != principal.persona_id:
            raise_forbidden()

        try:
            body.requester_disclosure()
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "status": 400,
                        "message": str(exc),
                    }
                },
            ) from exc

        GENERIC_CHAT_SHIM_REDIRECTS.inc()
        logger.warning(
            "generic /api/v1/chat shim redirect (HU-1926): persona=%s "
            "conversation_id=%s — caller should migrate to /api/v1/chat/{persona_id}",
            principal.persona_id,
            body.conversation_id,
        )
        return RedirectResponse(
            url=f"/api/v1/chat/{principal.persona_id}",
            status_code=status.HTTP_308_PERMANENT_REDIRECT,
            headers={
                "Deprecation": "true",
                "Sunset": "Wed, 01 Oct 2026 00:00:00 GMT",
                "Link": (f'</api/v1/chat/{principal.persona_id}>; rel="successor-version"'),
            },
        )

    @application.post(
        "/api/v1/chat/{persona_id}",
        response_model=PersonaChatResponse,
        tags=["Chat"],
        summary="Persona chat (text -> retrieval -> LLM -> text)",
    )
    async def persona_chat(
        persona_id: UUID,
        body: PersonaChatRequest,
        principal: ApiKeyPrincipal = Depends(authenticate),
        registry: PersonaRegistry = Depends(get_persona_registry),
        real_user_traffic_class: str | None = Header(
            default=None, alias=REAL_USER_TRAFFIC_CLASS_HEADER
        ),
    ) -> PersonaChatResponse:
        """Persona-scoped chat endpoint — the Phase-1 integration milestone (HU-1406).

        First real text-in -> memory-retrieval -> LLM -> text-out path. Wiring:

            inbound message
              -> ContextBuilder (provenance-safe memory -> prompt bridge, HU-1399)
              -> LLMClient (HU-1405: fake | openrouter | gemini)
              -> response + structured trace

        The ContextBuilder is the only sanctioned bridge and hard-excludes LOW /
        QUARANTINE / missing-confidence memories before the LLM ever sees them,
        so the response is grounded in provenance-safe HIGH/MEDIUM L1 memory
        only. The trace surfaces the admissible memory refs, their provenance
        tiers (canonical/derived), and the provider label so later F-tests
        (fidelity benchmarks) can consume it and prove the firewall held.

        Auth: persona-scoped bearer key (401 when missing/unknown). The path
        ``persona_id`` must match the key's scope (403 otherwise).
        """
        # Stage 0.3: per-turn metrics + structured access log (HU-1446). Each
        # exit branch calls ``_emit_turn`` with its outcome + guardrail-fire
        # bits; the helper records latency, increments the §3 counters, and
        # writes one JSON access-log line (no PHI). Defined once at entry so the
        # latency clock starts before auth + the ramp gate.
        _turn_t0 = time.perf_counter()

        def _emit_turn(
            pid: UUID,
            *,
            outcome: str,
            status_class: str | None = None,
            crisis: bool = False,
            consent_required: bool = False,
            real_user_refused: bool = False,
            ungrounded_claims: int = 0,
            alignment_disposition: str | None = None,
            ungrounded_by_category: dict[str, int] | None = None,
            risk_action: str | None = None,
            risk_flags: tuple[str, ...] = (),
            handoff_outcome: str | None = None,
        ) -> None:
            try:
                record_chat_turn(
                    ChatTurnOutcome(
                        outcome=outcome,
                        latency_s=time.perf_counter() - _turn_t0,
                        persona_id=pid,
                        status_class=status_class,
                        crisis=crisis,
                        consent_required=consent_required,
                        real_user_refused=real_user_refused,
                        ungrounded_claims=ungrounded_claims,
                        alignment_disposition=alignment_disposition,
                        ungrounded_by_category=ungrounded_by_category or {},
                        risk_action=risk_action,
                        risk_flags=risk_flags,
                        handoff_outcome=handoff_outcome,
                    )
                )
            except Exception:  # metrics must never break a clinical turn
                logger.exception("persona_chat metrics recording failed")

        if persona_id != principal.persona_id:
            _emit_turn(persona_id, outcome="forbidden", status_class="4xx")
            raise_forbidden()

        # --- Stage 0.7: real-user hard kill switch (HU-1462, MANDATORY) -------
        # PERSONA_CHAT_REAL_USER_TRAFFIC is the PRIMARY rollback path (launch
        # plan §4.2) — a hard boolean that overrides the ramp gate below. When
        # OFF (the default), every real-user turn returns HTTP 503
        # SERVICE_DISABLED, independent of key-revocation propagation.
        # Internal/synthetic traffic is unaffected so the test suite, probes,
        # and the rollback dry-run (§4.3) keep running while real grieving-user
        # traffic is hard-stopped. Crisis/handoff audit still records (§10.1
        # invariant 5): the crisis classifier runs in the refusal path so a
        # grieving user in crisis during a rollback is still routed to the
        # §7.4.1 handoff queue, and the 503 body carries 988 resources. The
        # ramp gate (PERSONA_CHAT_REAL_USER_MODE) is only reached when this
        # switch is ON — it is the staged-exposure lever, not the brake.
        chat_settings: Settings = application.state.settings
        traffic_class = traffic_class_from_header(real_user_traffic_class)
        if (
            not chat_settings.persona_chat_real_user_traffic_enabled
            and traffic_class == TrafficClass.REAL
        ):
            # Crisis/handoff audit still records even under a hard rollback —
            # a grieving user in crisis must still reach the queue + 988. The
            # classifier is the same shared G1 signal; the persona voice is
            # never reached (we raise 503 on every branch here).
            crisis_result = classify_user_message(
                body.message,
                classifier=application.state.crisis_classifier,
            )
            if crisis_result.is_crisis:
                _mark_crisis_session(application, body.conversation_id)
                handoff_view = _escalate_and_build_trace(
                    application,
                    message=body.message,
                    crisis_result=crisis_result,
                    persona_id=persona_id,
                    conversation_id=body.conversation_id,
                    risk_flags=[],
                )
                # The handoff acknowledgement (988 + "a person will join" when
                # paged) is the clinically correct 503 body for a crisis turn.
                refusal_message = handoff_view.user_acknowledgement
                _emit_turn(
                    persona_id,
                    outcome="crisis",
                    crisis=True,
                    handoff_outcome=handoff_view.outcome,
                    status_class="5xx",
                )
                _log_chat_trace(application, body.conversation_id, action="handoff")
            else:
                refusal_message = SERVICE_DISABLED_MESSAGE
                _emit_turn(
                    persona_id,
                    outcome="real_user_traffic_disabled",
                    real_user_refused=True,
                    status_class="5xx",
                )
                _log_chat_trace(application, body.conversation_id, action="refuse")
            REAL_USER_TRAFFIC_DISABLED.inc()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": {
                        "code": "SERVICE_DISABLED",
                        "status": 503,
                        "message": refusal_message,
                        "crisis_detected": crisis_result.is_crisis,
                        "resources_shown": True,
                    }
                },
            )

        # --- Stage 0.1: real-user ramp gate (HU-1444) ------------------------
        # The staged-exposure lever — only reached when the Stage 0.7 hard kill
        # switch (above) is ON. Real grieving-user traffic is refused unless the
        # runtime mode is canary/open AND (for canary) the persona is on the
        # allowlist. One env flip (PERSONA_CHAT_REAL_USER_MODE=off) refuses
        # grieving-user turns with a warm, non-persona response — never the
        # deceased-persona voice. Internal/synthetic traffic
        # (``X-Huible-Traffic-Class: internal``) is unaffected in every mode so
        # the test suite and probes keep running when the switch is off.
        # Absent/unknown header → ``real`` (the safe direction). Default to OFF
        # on ambiguous signal (Clinical Advisor + PM ratified, plan §3).
        real_user_mode = parse_real_user_mode(chat_settings.persona_chat_real_user_mode)
        if is_real_user_turn_refused(
            real_user_mode,
            traffic_class,
            persona_id,
            chat_settings.persona_chat_canary_personas_set,
        ):
            refusal_provider = str(getattr(application.state.llm_client, "provider", "unknown"))
            _record_turn(
                application, body.conversation_id, body.message, REAL_USER_MODE_OFF_RESPONSE
            )
            _emit_turn(persona_id, outcome="real_user_refused", real_user_refused=True)
            _log_chat_trace(application, body.conversation_id, action="refuse")
            return PersonaChatResponse(
                response=REAL_USER_MODE_OFF_RESPONSE,
                trace=ChatTrace(
                    provider=refusal_provider,
                    safety_event=SafetyEventView(
                        kind="real_user_mode_off",
                        signal="n/a",
                        affect="n/a",
                        matched=[],
                        resources_shown=True,
                    ),
                ),
            )

        # --- C2 coverage gate (HU-2245, CA floor HU-2244) ---------------------
        # Real-user persona-chat is admitted only inside the CA-seat coverage
        # window when PERSONA_CHAT_COVERAGE_ENFORCEMENT=on (armed at Stage-1
        # entry activation together with HANDOFF_COVERAGE_MODE=hours 08:00-22:00
        # America/New_York). Reached only after the kill switch AND the ramp
        # gate both admitted the turn. Out-of-window is a *scheduled* daily
        # state, not an emergency: the refusal is the warm non-persona 200
        # posture (same copy as the ramp gate — 503 stays reserved for the
        # rollback signal). The crisis classifier still runs in the refusal
        # path (§10.1 invariant 5): a grieving user in crisis outside the
        # window is still routed to the §7.4.1 handoff queue, which itself
        # knows the window and degrades honestly (never claims a person is
        # joining when nobody is on-shift). Internal/synthetic traffic is
        # unaffected.
        coverage_now = (
            application.state.chat_coverage_now() if application.state.chat_coverage_now else None
        )
        if (
            chat_settings.persona_chat_coverage_enforced
            and traffic_class == TrafficClass.REAL
            and not application.state.chat_coverage_window.is_open(coverage_now)
        ):
            crisis_result = classify_user_message(
                body.message,
                classifier=application.state.crisis_classifier,
            )
            if crisis_result.is_crisis:
                _mark_crisis_session(application, body.conversation_id)
                handoff_view = _escalate_and_build_trace(
                    application,
                    message=body.message,
                    crisis_result=crisis_result,
                    persona_id=persona_id,
                    conversation_id=body.conversation_id,
                    risk_flags=[],
                )
                coverage_message = handoff_view.user_acknowledgement
                _emit_turn(
                    persona_id,
                    outcome="crisis",
                    crisis=True,
                    handoff_outcome=handoff_view.outcome,
                )
                _log_chat_trace(application, body.conversation_id, action="handoff")
            else:
                coverage_message = REAL_USER_MODE_OFF_RESPONSE
                _record_turn(
                    application, body.conversation_id, body.message, REAL_USER_MODE_OFF_RESPONSE
                )
                _emit_turn(persona_id, outcome="coverage_refused", real_user_refused=True)
                _log_chat_trace(application, body.conversation_id, action="refuse")
            CHAT_COVERAGE_REFUSED.inc()
            refusal_provider = str(getattr(application.state.llm_client, "provider", "unknown"))
            return PersonaChatResponse(
                response=coverage_message,
                trace=ChatTrace(
                    provider=refusal_provider,
                    safety_event=SafetyEventView(
                        kind="coverage_closed",
                        signal="n/a",
                        affect="n/a",
                        matched=[],
                        resources_shown=True,
                    ),
                ),
            )

        try:
            relationship = body.requester_relationship()
        except ValueError as exc:
            _emit_turn(persona_id, outcome="validation_error", status_class="4xx")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "status": 400,
                        "message": str(exc),
                    }
                },
            ) from exc

        requester_tier = _resolve_relationship(relationship)
        binding: PersonaBinding | None = registry.get(persona_id, requester_tier)
        if binding is None:
            _emit_turn(persona_id, outcome="persona_not_found", status_class="4xx")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": {
                        "code": "PERSONA_NOT_FOUND",
                        "status": 404,
                        "message": f"No persona registered for id {persona_id}",
                    }
                },
            )

        llm: LLMClient = application.state.llm_client
        provider_label = str(getattr(llm, "provider", "unknown"))

        # --- §7.4.4 G8: load the session risk profile (once per turn) -------
        # The intake-derived risk flags are a property of the (session,
        # persona), not the message. Loading them up-front lets the G1 crisis
        # branch carry the real flags on its handoff ticket (matrix §2 audit
        # field: "risk flags present") AND lets the post-consent enforcement
        # branch act on them. The default profile is empty (no flags) so the
        # pre-real-user suite and the default persona-chat turn are unaffected.
        session_id = body.conversation_id or _mint_conversation_id()
        risk_profile: RiskProfileProvider = application.state.risk_profile
        session_risk_flags = risk_profile.get_flags(session_id, persona_id)

        # --- G1 + §7.4.1: synchronous crisis pre-check → human-handoff queue ---
        # A crisis signal must NEVER reach the persona voice (HU-1407 §7.1 G1).
        # The check is synchronous, pre-generation, and pre-retrieval: on a
        # positive crisis signal the ContextBuilder is not called at all (no
        # memory retrieval on a crisis turn), persona-voiced generation is
        # skipped, and a warm non-persona escalation response is returned with a
        # recorded safety_event on the trace.
        #
        # §7.4.1 (HU-1421): the crisis turn is *also* routed into the
        # human-handoff queue — an audited escalation ticket with a defined SLA,
        # a non-persona waiting UX, and a fail-safe that degrades to this same
        # G1 safe response when no human is available. The persona path is
        # unreachable from here by construction (we return on every branch).
        # Routing trigger is the G1 classifier signal, never persona-output.
        #
        # §7.4.4 G8 (matrix §4): G1 crisis always pre-empts flag enforcement —
        # a positive G1 signal short-circuits to the non-persona escalation
        # path regardless of any risk flag, and flags do not weaken G1. The
        # loaded risk flags still ride on the handoff ticket (audit row) so the
        # flag context is preserved for clinical review.
        crisis_result = classify_user_message(
            body.message,
            classifier=application.state.crisis_classifier,
        )
        if crisis_result.is_crisis:
            _mark_crisis_session(application, body.conversation_id)
            handoff_view = _escalate_and_build_trace(
                application,
                message=body.message,
                crisis_result=crisis_result,
                persona_id=persona_id,
                conversation_id=body.conversation_id,
                risk_flags=session_risk_flags,
            )
            # handoff_view.user_acknowledgement already carries the full G1
            # crisis resources (+ "a person will join" only when a responder
            # was actually paged).
            escalation = handoff_view.user_acknowledgement
            _record_turn(application, body.conversation_id, body.message, escalation)
            _emit_turn(
                persona_id,
                outcome="crisis",
                crisis=True,
                handoff_outcome=handoff_view.outcome,
            )
            _log_chat_trace(
                application,
                body.conversation_id,
                action="handoff",
                fired_flags=tuple(session_risk_flags),
            )
            return PersonaChatResponse(
                response=escalation,
                trace=ChatTrace(
                    provider=provider_label,
                    safety_event=SafetyEventView(
                        kind="crisis_escalation",
                        signal=crisis_result.signal.value,
                        affect=crisis_result.affect.value,
                        matched=list(crisis_result.matched),
                    ),
                    handoff=handoff_view,
                    session_meta=_session_meta(application, body.conversation_id),
                ),
            )

        # --- §7.4.3 G6: first-use reality-framing / consent gate ---------------
        # No persona-voiced reply may leave this path before the session has
        # acknowledged the consent card (HU-1423). The check runs AFTER the G1
        # crisis branch on purpose: crisis resources are a non-persona safety
        # response and must remain reachable on a first, un-consented turn
        # (safety wins over framing). The persona path — retrieval, generation,
        # the whole deceased-voice surface — is what is gated here.
        #
        # On a missing consent the turn fails fast with HTTP 409
        # CONSENT_REQUIRED and the card inline, so the client can render it and
        # POST /consent. The deceased persona never voices the consent: the
        # card is a non-persona system message and never reaches the generator.
        consent_gate: ConsentGate = application.state.consent_gate
        if not consent_gate.is_acknowledged(session_id, persona_id):
            card_provider: ConsentCardProvider = application.state.consent_card_provider
            card = card_provider.get_card(binding.persona.name)
            _emit_turn(
                persona_id,
                outcome="consent_required",
                consent_required=True,
                status_class="4xx",
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": {
                        "code": "CONSENT_REQUIRED",
                        "status": 409,
                        "message": (
                            "Reality-framing consent is required before this session can proceed."
                        ),
                        "conversation_id": session_id,
                        "acknowledge_url": f"/api/v1/chat/{persona_id}/consent",
                        "consent_card": ConsentCardView(
                            version=card.version,
                            title=card.title,
                            body=card.body,
                            acknowledge_instructions=card.acknowledge_instructions,
                        ).model_dump(),
                    }
                },
            )

        # --- §7.4.4 G8: risk-flag enforcement (act, not just record) --------
        # The Clinical Advisor's enforcement matrix converts the session's risk
        # flags + session-meta signals into a binding action with concrete
        # runtime effects. G1 has already cleared (crisis pre-empts), so the
        # report's ``is_crisis`` is structurally False here. The binding action
        # may short-circuit generation (pause_session / handoff / refuse_topic)
        # or apply generation-side constraints (tighten / reframe) that compose
        # with G3 (shared affect signal) and §7.4.2 (claim alignment).
        settings = application.state.settings
        session_signals = _risk_session_signals(
            application,
            message=body.message,
            conversation_id=body.conversation_id,
            dosage_cap_turns=settings.risk_dosage_cap_turns,
            crisis_classifier=application.state.crisis_classifier,
        )
        enforcement = enforce_risk_flags(
            session_risk_flags,
            session_signals=session_signals,
            is_crisis=False,
            message=body.message,
        )

        # Pre-generation short-circuits: persona voice is suppressed for the
        # flagged turn (matrix §1). Each branch surfaces the enforcement report
        # on the trace for the per-flag fire-count + per-action telemetry.
        if enforcement.action is not None and enforcement.short_circuits_generation:
            if enforcement.action.value == "pause_session":
                # proxy_user gets its own specialized pause copy (the actionable
                # next step is identity re-confirmation, not just a breather).
                response_text = (
                    PROXY_USER_PAUSE_RESPONSE
                    if RiskFlag.PROXY_USER in enforcement.fired_flags
                    else PAUSE_SESSION_RESPONSE
                )
                _record_turn(application, body.conversation_id, body.message, response_text)
                _emit_turn(
                    persona_id,
                    outcome="risk_pause",
                    risk_action="pause_session",
                    risk_flags=tuple(f.value for f in enforcement.fired_flags),
                )
                _log_chat_trace(
                    application,
                    body.conversation_id,
                    action="pause",
                    fired_flags=tuple(f.value for f in enforcement.fired_flags),
                )
                return PersonaChatResponse(
                    response=response_text,
                    trace=ChatTrace(
                        provider=provider_label,
                        session_meta=_session_meta(application, body.conversation_id),
                        risk_enforcement=_risk_enforcement_view(enforcement),
                    ),
                )
            if enforcement.action.value == "handoff":
                # Matrix §4: handoff composes with G1 — reuse the warm
                # non-persona posture + crisis-line display + §7.4.1 queue.
                # The trigger label distinguishes the risk-driven path from a
                # G1 crisis escalation on the audit row.
                trigger = (
                    "distress_trend_rising"
                    if "handoff" in enforcement.session_signal_actions
                    else enforcement.fired_flags[0].value
                    if enforcement.fired_flags
                    else "risk_flag"
                )
                handoff_view = _escalate_risk_and_build_trace(
                    application,
                    trigger=trigger,
                    persona_id=persona_id,
                    conversation_id=body.conversation_id,
                    risk_flags=session_risk_flags,
                )
                response_text = handoff_view.user_acknowledgement
                _record_turn(application, body.conversation_id, body.message, response_text)
                _emit_turn(
                    persona_id,
                    outcome="risk_handoff",
                    risk_action="handoff",
                    risk_flags=tuple(f.value for f in enforcement.fired_flags),
                    handoff_outcome=handoff_view.outcome,
                )
                _log_chat_trace(
                    application,
                    body.conversation_id,
                    action="handoff",
                    fired_flags=tuple(f.value for f in enforcement.fired_flags),
                )
                return PersonaChatResponse(
                    response=response_text,
                    trace=ChatTrace(
                        provider=provider_label,
                        handoff=handoff_view,
                        session_meta=_session_meta(application, body.conversation_id),
                        risk_enforcement=_risk_enforcement_view(enforcement),
                    ),
                )
            # refuse_topic: in-voice topic-redirect fallback (no LLM call).
            response_text = REFUSE_TOPIC_FALLBACK_RESPONSE
            _record_turn(application, body.conversation_id, body.message, response_text)
            _emit_turn(
                persona_id,
                outcome="risk_refuse",
                risk_action="refuse_topic",
                risk_flags=tuple(f.value for f in enforcement.fired_flags),
            )
            _log_chat_trace(
                application,
                body.conversation_id,
                action="refuse",
                fired_flags=tuple(f.value for f in enforcement.fired_flags),
            )
            return PersonaChatResponse(
                response=response_text,
                trace=ChatTrace(
                    provider=provider_label,
                    session_meta=_session_meta(application, body.conversation_id),
                    risk_enforcement=_risk_enforcement_view(enforcement),
                ),
            )

        # --- Default / G3-distress path: retrieve + render + generate --------
        # The shared affect signal grades sub-acute distress; the ContextBuilder
        # branches the prompt (G3 dynamic half) and the affect guard suppresses
        # any sarcastic/dismissive generation on the distress branch.
        #
        # §7.4.4 G8 composition: when ``tighten`` is in the required actions the
        # distress branch is forced on for the turn (flattens humor/levity even
        # without a distress signal — matrix §2 loss_of_child / minor_decedent /
        # recent_loss). When ``reframe`` is in the required actions a reality-
        # framing re-anchor addendum is appended to the system prompt (matrix
        # §1: re-anchor using the existing G2 framing asset; does not author
        # new framing). Both compose with the §7.4.2 alignment guard below.
        effective_affect = (
            UserAffect.DISTRESS if enforcement.forces_tighten else crisis_result.affect
        )
        ctx = await application.state.context_builder.build(
            persona=binding.persona,
            requester_tier=binding.requester_tier,
            backend=binding.backend,
            query_embedding_content=_embed(body.message),
            current_message=body.message,
            user_affect=effective_affect,
            conversation_history=_history(application, body.conversation_id),
        )

        prompt = ctx.render()
        system_prompt = ctx.system_prompt
        if enforcement.forces_reframe:
            system_prompt = system_prompt + "\n\n" + build_reframe_addendum(binding.persona.name)
        budget_fallback = False
        try:
            # conversation_id rides along for the per-conversation cost log
            # line emitted by metered/ceilinged providers (zai HU-1910); the
            # clients consume it for logging and never send it to the API.
            response_text = await llm.generate(
                prompt,
                system_prompt=system_prompt,
                conversation_id=body.conversation_id,
                # Rubric #3 (HU-1911): texting-length ceiling per turn; the
                # concision directive in the system prompt shapes style, this
                # hard-caps the hosted generation budget for persona turns.
                # HU-2231: per-persona cap derived from the persona's own
                # corpus length register when measured (fallback: the
                # global Chandler-tuned setting).
                max_tokens=reply_budget_tokens(
                    binding.persona.length_stats,
                    default=settings.persona_chat_max_tokens,
                ),
            )
        except LLMBudgetExceededError:
            # Board-approved degraded posture (HU-1774 decision sweep
            # 2026-08-18, item 3: "$50/mo hard cap; fake voice stays as
            # rollback"): when the OpenRouter monthly budget is exhausted
            # the turn is served by the deterministic fake voice instead of
            # erroring — the persona surface stays up while the operator
            # tops up or raises the cap. Only the budget error is caught;
            # transient hosted errors keep raising so monitoring sees them.
            logger.error(
                "persona-chat llm budget exhausted (persona=%s); serving fake-voice fallback",
                persona_id,
            )
            budget_fallback = True
            fallback = _FakeLLMClient(persona_name=binding.persona.name)
            response_text = await fallback.generate(prompt, system_prompt=system_prompt)
            provider_label = f"{provider_label}->fake(budget)"

        # G3 generation-time guard: on the distress branch (forced or graded),
        # replace a sarcastic / dismissive generation with a safe grounded
        # fallback. Conservative — only replaces on distress when a concrete
        # pattern fires.
        response_text, _suppressed = apply_affect_guard(response_text, affect=effective_affect)

        # §7.4.2 generation-time claim->ref alignment filter. The retrieval-side
        # G4 firewall guarantees the *prompt* only saw provenance-safe memory;
        # this is the generation-side backstop for a confabulating generator —
        # any factual / identity / advice claim in the reply must trace to a
        # retrieved ref (or the persona vault), or the turn is failed safely to
        # a claim-free reflection fallback. Runs on every persona-voiced turn
        # (crisis already returned); the report feeds the trace alignment view
        # for clinical review. See huible.safety.alignment.
        #
        # HU-2070: the grounding corpus is widened with the persona-scoped
        # G4-admissible memory set (same confidence / disclosure / era gates as
        # the prompt firewall) so a truthful reply naming an entity that lives
        # in the wider persona corpus — but outside this turn's retrieval
        # window — is no longer suppressed. Identity/advice policy claims and
        # fabricated entities absent from the whole persona corpus are still
        # caught. Scan failure degrades to turn-refs-only (strict) grounding.
        persona_scope_refs = await _persona_scope_grounding_refs(application, binding)
        alignment = apply_alignment_guard(
            response_text,
            refs=ctx.included_memories,
            persona=binding.persona,
            persona_scope_refs=persona_scope_refs,
            conversation_history=_history(application, body.conversation_id),
            current_message=body.message,
            # HU-1911: vary the suppression fallback per conversation so the
            # canned line is not verbatim-identical across sessions.
            fallback_seed=str(body.conversation_id),
        )

        # HU-2161 judge backstop on the suppression decision (§7.4.2 roadmap
        # hardening, pulled forward). The Phase-1 content-overlap filter has a
        # documented false-positive class — truthful canon-heavy replies naming
        # entities the whole persona corpus does not literally contain (the
        # HU-2070 recurrence) — and a suppression used to auto-page Sev-1, so a
        # canned-line bug would have paged a real human once seats exist. Now:
        # a suppression carrying judgeable (biographical / relationship) claims
        # is first adjudicated by the LLM judge against the persona record
        # digest. Judge-supported claims are cleared and the original reply is
        # restored (disposition back to ``passed``); a suppression that
        # survives the judge is a high-confidence confabulation. Policy-only
        # suppressions (identity / advice pattern violations) and fake/mock
        # generators skip the judge — the deterministic suite's strictness is
        # unchanged.
        judge_verdict = await _adjudicate_alignment_suppression(
            application,
            llm=llm,
            binding=binding,
            persona_scope_refs=persona_scope_refs,
            alignment=alignment,
            original_text=response_text,
            budget_fallback=budget_fallback,
        )
        response_text = alignment.text

        # §3 Sev-1 (A) — un-grounded persona claim leak (HU-1451 trigger #2),
        # judge-gated per HU-2161. A suppression pages a human ONLY when it is
        # high-confidence: a policy-pattern violation (identity / advice — the
        # vault can never legitimately contain them) or a judge-confirmed
        # confabulation. An *unconfirmed* suppression (content-overlap verdict
        # alone, judge unavailable/timeout) does NOT page: a Phase-1
        # content-overlap verdict is a suspect, not proof the generator
        # confabulated — the exact false-positive class that paged nothing only
        # because no seats were configured. Unconfirmed suppressions surface on
        # the ``huible_alignment_unconfirmed_suppressions_total`` counter and
        # the HuibleAlignmentLeak alert instead. The user-facing turn is
        # unaffected either way (the guard already substituted the claim-free
        # fallback); paging never alters it.
        if alignment.disposition == "suppressed":
            policy_only = all(
                c.category in ("identity", "advice") for c in alignment.ungrounded
            )
            confirmed = policy_only or (
                judge_verdict is not None and judge_verdict.outcome == "fabricated"
            )
            _log_alignment_suppression(
                application,
                conversation_id=body.conversation_id,
                alignment=alignment,
                judge_verdict=judge_verdict,
                policy_only=policy_only,
                confirmed=confirmed,
            )
            if confirmed:
                _page_sev1_fire_and_forget(
                    application,
                    trigger=PAGE_TRIGGER_UNGROUNDED_LEAK,
                    persona_id=str(persona_id),
                )
            else:
                record_alignment_unconfirmed_suppression()

        _record_turn(application, body.conversation_id, body.message, response_text)
        _emit_turn(
            persona_id,
            outcome="persona_budget_fallback" if budget_fallback else "persona",
            ungrounded_claims=alignment.ungrounded_count,
            alignment_disposition=alignment.disposition,
            ungrounded_by_category=alignment.category_counts(),
            risk_action=enforcement.action.value if enforcement.action else None,
            risk_flags=tuple(f.value for f in enforcement.fired_flags),
        )
        _persona_action = (
            enforcement.action.value
            if enforcement.action
            else "tighten"
            if enforcement.forces_tighten
            else "reframe"
            if enforcement.forces_reframe
            else "persona"
        )
        _log_chat_trace(
            application,
            body.conversation_id,
            action=_persona_action,
            fired_flags=tuple(f.value for f in enforcement.fired_flags),
            ungrounded=alignment.ungrounded_count,
            claim_count=alignment.claim_count,
            disposition=alignment.disposition,
        )

        # §3 Sev-1 (C) — consent-bypass defensive check (HU-1451 trigger #4).
        # The G6 consent gate at line ~955 is authoritative: it raises 409
        # before any persona voice when consent is unrecorded. This is a
        # defense-in-depth *post-hoc* re-check right before the persona reply
        # leaves the server — if the gate was somehow bypassed (backend
        # corruption, race, a future code path that skips it), a persona-voiced
        # turn reached a real user without recorded consent. That is §3 Sev-1
        # (C): page immediately, fire-and-forget. The turn itself is unaffected
        # (the guard already produced the response); this only raises the alarm.
        # No-op on the happy path (consent still recorded) — one extra cheap read.
        try:
            consent_gate_check: ConsentGate = application.state.consent_gate
            if not consent_gate_check.is_acknowledged(session_id, persona_id):
                _page_sev1_fire_and_forget(
                    application,
                    trigger=PAGE_TRIGGER_CONSENT_BYPASS,
                    persona_id=str(persona_id),
                )
        except Exception:  # pragma: no cover - defensive; never break the turn
            logger.exception("consent-bypass check failed; turn continues")

        return PersonaChatResponse(
            response=response_text,
            trace=ChatTrace(
                memory_refs=[str(node.id) for node in ctx.included_memories],
                provenance_tiers=sorted({node.tier.value for node in ctx.included_memories}),
                excluded_memory_refs=[
                    ExcludedMemoryRefView(id=ref.id, reason=ref.reason)
                    for ref in ctx.excluded_memory_refs
                ],
                activated_memories=[_view(node) for node in ctx.included_memories],
                exclusion_counts=dict(ctx.exclusion_counts),
                conversation_id=session_id,
                provider=provider_label,
                framing_version=ctx.framing_version,
                distress_grounding=ctx.distress_grounding,
                session_meta=_session_meta(application, body.conversation_id),
                alignment=AlignmentView(
                    claim_count=alignment.claim_count,
                    ungrounded_claim_count=alignment.ungrounded_count,
                    disposition=alignment.disposition,
                    ungrounded_by_category=alignment.category_counts(),
                    judge_adjudication=(
                        judge_verdict.outcome if judge_verdict is not None else None
                    ),
                ),
                risk_enforcement=_risk_enforcement_view(enforcement),
            ),
        )

    @application.post(
        "/api/v1/chat/{persona_id}/consent",
        response_model=ConsentAcknowledgeResponse,
        tags=["Chat"],
        summary="Acknowledge the G6 reality-framing / consent card for a session",
    )
    async def acknowledge_consent(
        persona_id: UUID,
        body: ConsentAcknowledgeRequest,
        principal: ApiKeyPrincipal = Depends(authenticate),
        registry: PersonaRegistry = Depends(get_persona_registry),
    ) -> ConsentAcknowledgeResponse:
        """Record first-use reality-framing consent for a session (§7.4.3 G6).

        The chat path (``POST /api/v1/chat/{persona_id}``) refuses to produce a
        persona reply until this is recorded. The consent binds to the session
        (``conversation_id``) and persona. The card is an onboarding/system
        message — the deceased persona never voices it (§7.1 H1); this endpoint
        only records the acknowledgment, it performs no generation.

        Auth: persona-scoped bearer key (401 when missing/unknown). The path
        ``persona_id`` must match the key's scope (403 otherwise). The persona
        must be registered (404 otherwise) so consent cannot be recorded for an
        unknown persona.

        This is the chat-path-owned recording surface. When the onboarding
        terminal lands it either calls this endpoint or writes to the same
        ``ConsentGate`` backend; the enforcement in ``persona_chat`` is the
        durable gate either way.
        """
        if persona_id != principal.persona_id:
            raise_forbidden()

        # Resolve any requester tier to confirm the persona is registered. The
        # relationship is not meaningful for consent (the card is the same for
        # every requester), but we require the persona to exist so consent
        # cannot be recorded against an unknown persona id.
        binding: PersonaBinding | None = registry.get(persona_id, RelationshipTier.FAMILY)
        if binding is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": {
                        "code": "PERSONA_NOT_FOUND",
                        "status": 404,
                        "message": f"No persona registered for id {persona_id}",
                    }
                },
            )

        card_provider: ConsentCardProvider = application.state.consent_card_provider
        card_version = body.card_version or card_provider.get_card(binding.persona.name).version

        gate: ConsentGate = application.state.consent_gate
        record = gate.record_acknowledgement(
            body.conversation_id, persona_id=persona_id, card_version=card_version
        )

        return ConsentAcknowledgeResponse(
            data=ConsentAcknowledgeData(
                acknowledged=True,
                conversation_id=record.session_id,
                persona_id=persona_id,
                card_version=record.card_version,
                acknowledged_at=record.acknowledged_at,
                acknowledgment_id=record.acknowledgment_id,
            )
        )

    # --- §7.4 ops gate: staffed-responder handoff surface (HU-1428) ---------
    # The responder work queue + SLA monitoring surface. Gated behind the
    # existing bearer auth (defense in depth): a dedicated responder auth model
    # is a future ops refinement; today any valid API key reaches this internal
    # ops surface. The queue is the same object the chat path escalates into, so
    # responders see live tickets the moment they are created. SLA breach
    # detection + outcome telemetry (AC #4) are computed from the audit log on
    # each read. The "available_responders > 0" production-wiring (AC #2) and
    # the named responder model + coverage hours (AC #1) are the remaining
    # clinical/ops prerequisites tracked on this issue.

    @application.get(
        "/api/v1/handoff/tickets",
        tags=["handoff"],
        summary="List pending handoff tickets — the staffed-responder work queue.",
    )
    async def list_pending_handoff_tickets(
        principal: ApiKeyPrincipal = Depends(authenticate),
    ) -> DataEnvelope:
        queue: HandoffQueue = application.state.handoff_queue
        now = datetime.now(UTC)
        # Ack-SLA Sev-1 escalation (HU-1450 item 5): every queue read re-pages
        # any ENQUEUED ticket past its per-ticket HANDOFF_SLA_TARGET_SECONDS
        # without an acknowledgement. This is the monitoring cadence that turns
        # the live breach signal into a re-page — the canary 900s (15-min) ack
        # SLA is the threshold. The >10%/1h aggregate stays the rate backstop.
        # Best-effort: a paging failure must never break the queue read.
        try:
            escalate_sla_breaches(
                queue,
                application.state.pager,
                window=application.state.coverage_window_label,
                now=now,
            )
        except Exception:  # pragma: no cover - defensive
            logger.exception("SLA-breach re-page failed; queue read continues")
        items = [_queue_item_view(t, with_sla=True, now=now) for t in queue.list_pending()]
        return DataEnvelope(data=items)

    @application.post(
        "/api/v1/handoff/tickets/{ticket_id}/resolve",
        tags=["handoff"],
        summary="Resolve a handoff ticket (responder action: claim + clinical note).",
    )
    async def resolve_handoff_ticket(
        ticket_id: str,
        body: HandoffResolveRequest,
        principal: ApiKeyPrincipal = Depends(authenticate),
    ) -> DataEnvelope:
        outcome_raw = (body.outcome or "").strip().lower()
        try:
            outcome = HandoffOutcome(outcome_raw)
        except ValueError:
            outcome = None
        if outcome not in (HandoffOutcome.ANSWERED, HandoffOutcome.ABANDONED):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": {
                        "code": "INVALID_OUTCOME",
                        "status": 400,
                        "message": "outcome must be 'answered' or 'abandoned'",
                    }
                },
            )
        queue: HandoffQueue = application.state.handoff_queue
        updated = queue.resolve(
            ticket_id,
            outcome=outcome,
            responder_id=body.responder_id,
            clinical_review_note=body.clinical_review_note,
        )
        if updated is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": {
                        "code": "TICKET_NOT_FOUND",
                        "status": 404,
                        "message": f"No handoff ticket for id {ticket_id}",
                    }
                },
            )
        return DataEnvelope(data=_queue_item_view(updated, with_sla=False))

    @application.get(
        "/api/v1/handoff/audit",
        tags=["handoff"],
        summary="Handoff audit log + SLA/outcome telemetry (the dashboard surface).",
    )
    async def handoff_audit(
        principal: ApiKeyPrincipal = Depends(authenticate),
    ) -> DataEnvelope:
        queue: HandoffQueue = application.state.handoff_queue
        now = datetime.now(UTC)
        log = queue.audit_log()
        telemetry = compute_handoff_telemetry(log, now=now)
        return DataEnvelope(
            data={
                "tickets": [_queue_item_view(t, with_sla=False) for t in log],
                "telemetry": _telemetry_view(telemetry),
            }
        )

    @application.get(
        "/api/v1/admin/real-user-mode",
        tags=["admin"],
        summary="Real-user ramp-gate + kill-switch state (Stage 0.1 + 0.7).",
    )
    async def real_user_mode_status(
        principal: ApiKeyPrincipal = Depends(authenticate),
    ) -> DataEnvelope:
        """Current ``PERSONA_CHAT_REAL_USER_MODE`` + ``PERSONA_CHAT_REAL_USER_TRAFFIC``.

        Reports the composing controls: the Stage 0.7 hard kill switch
        (``kill_switch`` on/off, HU-1462 — the primary rollback path), the
        Stage 0.1 ramp gate (``mode`` off/canary/open + canary allowlist size,
        HU-1444), and the C2 coverage gate
        (``coverage_enforcement`` + ``coverage_window`` + ``coverage_open_now``,
        HU-2245 — armed at Stage-1 entry). Read-only surface for the rollback
        dry-run (§4.3), the kill-switch drill, the coverage-gate entry
        verification, and monitoring to confirm every switch is armed at the
        expected stage (plan §4/§5). The switches are env-only at Stage 0
        — flipping any requires a container restart (settings are
        process-cached); live re-read is a follow-on.
        """
        admin_settings: Settings = application.state.settings
        mode = parse_real_user_mode(admin_settings.persona_chat_real_user_mode)
        kill_switch_on = admin_settings.persona_chat_real_user_traffic_enabled
        return DataEnvelope(
            data={
                "mode": str(mode),
                "is_off": mode == RealUserMode.OFF,
                "canary_persona_count": len(admin_settings.persona_chat_canary_personas_set),
                # Stage 0.7 hard kill switch (HU-1462).
                "kill_switch": "on" if kill_switch_on else "off",
                "kill_switch_enabled": kill_switch_on,
                # C2 coverage gate (HU-2245, CA floor HU-2244): armed state +
                # the window the chat path enforces for real-user turns.
                "coverage_enforcement": (
                    "on" if admin_settings.persona_chat_coverage_enforced else "off"
                ),
                "coverage_enforcement_enabled": admin_settings.persona_chat_coverage_enforced,
                "coverage_window": application.state.coverage_window_label,
                "coverage_open_now": (
                    application.state.chat_coverage_window.is_open(
                        application.state.chat_coverage_now()
                        if application.state.chat_coverage_now
                        else None
                    )
                ),
            }
        )

    @application.post(
        "/api/v1/admin/risk-intake",
        response_model=RiskIntakeResponse,
        tags=["admin"],
        summary="Stage 0.5 risk-profile intake for the canary cohort (§7.4.4 G8).",
    )
    async def record_risk_intake(
        body: RiskIntakeAssessmentRequest,
        principal: ApiKeyPrincipal = Depends(authenticate),
        registry: PersonaRegistry = Depends(get_persona_registry),
    ) -> RiskIntakeResponse:
        """Populate ``risk_flags`` for a canary (session, persona) so G8 is live.

        The minimal, consent-aware intake path (Stage 0.5, HU-1448). Merges
        the objective persona-derived flags (``minor_decedent`` /
        ``recent_loss`` from the registered persona config) with the user-
        gathered assessment flags (``loss_of_child`` / ``non_acceptance`` /
        ``proxy_user``) and writes them into the wired
        :class:`RiskProfileProvider`. On the next ``POST /chat/{persona_id}``
        turn the chat path reads them back via ``get_flags`` and §7.4.4 G8
        enforcement actually changes runtime behavior (dosage cap, reframe,
        refuse-topic, pause-session, handoff).

        Consent-aware (§7.4.3 G6 — no bypass): the (conversation_id,
        persona_id) pair must have a recorded reality-framing consent
        acknowledgment first (``POST /api/v1/chat/{persona_id}/consent``);
        otherwise this returns HTTP 409 ``CONSENT_REQUIRED``. The intake does
        not weaken the chat-path consent gate — it is defense in depth on the
        intake write surface.

        Auth: persona-scoped bearer key (401 when missing/unknown). The body
        ``persona_id`` must match the key's scope (403 otherwise). The persona
        must be registered (404 otherwise).

        Scope: canary cohort (≤10 invited users). No clinical-diagnosis
        fields; the full assessment instrument is Stage 2+.
        """
        if body.persona_id != principal.persona_id:
            raise_forbidden()

        # Resolve the persona config to derive objective flags + confirm it is
        # registered. The relationship tier is not meaningful for intake
        # (objective derivation is tier-independent), but we require the
        # persona to exist so flags cannot be recorded against an unknown id.
        binding: PersonaBinding | None = registry.get(body.persona_id, RelationshipTier.FAMILY)
        if binding is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": {
                        "code": "PERSONA_NOT_FOUND",
                        "status": 404,
                        "message": f"No persona registered for id {body.persona_id}",
                    }
                },
            )

        risk_profile: RiskProfileProvider = application.state.risk_profile
        consent_gate: ConsentGate = application.state.consent_gate
        service = RiskIntakeService(risk_profile, consent_gate=consent_gate)
        try:
            result = service.record_intake(
                session_id=body.conversation_id,
                persona_id=body.persona_id,
                persona=binding.persona,
                assessment=RiskIntakeAssessment(
                    loss_of_child=body.loss_of_child,
                    non_acceptance=body.non_acceptance,
                    proxy_user=body.proxy_user,
                ),
            )
        except ConsentNotRecordedError:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": {
                        "code": "CONSENT_REQUIRED",
                        "status": 409,
                        "message": (
                            "Reality-framing consent is required before intake "
                            "can be recorded for this session."
                        ),
                        "conversation_id": body.conversation_id,
                        "acknowledge_url": (f"/api/v1/chat/{body.persona_id}/consent"),
                    }
                },
            ) from None

        return RiskIntakeResponse(
            data=RiskIntakeData(
                persona_id=body.persona_id,
                conversation_id=result.session_id,
                consent_acknowledgment_id=result.consent_acknowledgment_id,
                persona_flags=result.persona_flags,
                session_flags=result.session_flags,
                derived_flags=result.derived_flags,
                assessed_flags=result.assessed_flags,
                all_flags=result.all_flags,
            )
        )

    @application.get(
        "/metrics",
        tags=["admin"],
        summary="Prometheus metrics for persona-chat + §7.4 guardrails (Stage 0.3, HU-1446).",
        include_in_schema=True,
    )
    async def prometheus_metrics() -> Response:
        """Aggregate scrape endpoint: chat turns/latency/errors + every §7.4
        guardrail counter (G1 crisis, G6 consent, §7.4.1 handoff outcomes,
        §7.4.2 un-grounded claims + dispositions, §7.4.4 enforcement actions,
        Stage 0.1 kill-switch refusals). No PHI — labels are aggregate-safe.

        Stage 0.8 (HU-1463): the scrape also mirrors the §3 SLO *gauges* —
        handoff SLA telemetry (degrade rate, pending breach, answered-within-SLA
        rate, queue depth) sourced from :func:`compute_handoff_telemetry` over
        the wired queue's audit log, and the ``/health`` probe status — so the
        launch-plan §3.1/§3.2 SLO table and §4.1 rollback triggers are
        observable from a scrape alone. The alert rules in
        ``examples/prometheus-alerts.yml`` page on these gauges. Computing them
        on scrape keeps the Prometheus view identical to the
        ``/api/v1/handoff/audit`` JSON dashboard.

        Unauthenticated by design (Prometheus convention); contains no user
        data, only monotonic counters + a latency histogram + SLO gauges. The
        §3 Sev-1 alerts page the 0.4 on-call once that roster is wired
        (HU-1447).
        """
        # Mirror the §3 SLO gauges before generating the exposition so this
        # scrape reflects the current queue + health state. Both calls are
        # best-effort: a failure to compute telemetry must never break a
        # scrape (the counters above still carry the signal).
        try:
            queue: HandoffQueue = application.state.handoff_queue
            telemetry = compute_handoff_telemetry(
                queue.audit_log(),
                now=datetime.now(UTC),
                # Rolling window (HU-1865): the §4.1 gauges page on *current*
                # queue health; the /api/v1/handoff/audit dashboard keeps the
                # all-time view.
                window_seconds=application.state.settings.handoff_telemetry_window_seconds,
            )
            record_handoff_telemetry(telemetry)
            # §7.4 alert-enablement signal (HU-1880): mirror the live queue
            # staffing so the degrade-rate page rule arms exactly at roster
            # staffing (pre-staffing degrades are the expected G1 fail-safe).
            record_handoff_responder_readiness(getattr(queue, "available_responders", 0))
        except Exception:  # pragma: no cover - defensive, scrape must not break
            logger.exception("handoff telemetry gauge update failed")
        try:
            health_status = (await _health_data(application)).status
            record_health_status(health_status)
        except Exception:  # pragma: no cover - defensive, scrape must not break
            logger.exception("health status gauge update failed")
        body, content_type = metrics_response()
        return Response(content=body, media_type=content_type)


# --- helpers ----------------------------------------------------------------


def _embed(message: str) -> list[float]:
    """Token-hashed embedding for the inbound message.

    Mirrors :func:`huible.conversation.simple_embedding` semantics so retrieval
    hits memories that share keywords with the inbound turn. Kept local to the
    API layer to avoid coupling the HTTP path to the demo conversation module.

    The vector is emitted at the ``memories.embedding_content`` schema dim
    (1536) so the HU-1435 dimension guard lets the pgvector cosine search run:
    a 64-dim Stage-1 query vector against the 1536-dim column silently skipped
    every search (activated memories always empty). Keyword-overlap semantics
    are unchanged — the token hash simply spreads over 1536 buckets, and the
    provisioned persona memories are stored with this same function/dim
    (HU-1909). A real embedding provider swaps both sides later via
    ``EMBEDDING_PROVIDER``.
    """
    from huible.conversation import simple_embedding

    return simple_embedding(message, dim=1536)


def _conversation_store(application: FastAPI) -> ConversationStore:
    """Return the wired :class:`ConversationStore` (in-memory or durable).

    Falls back to an :class:`InMemoryConversationStore` if nothing was wired —
    preserves the bare-app bootstrap path (``uvicorn huible.api.app:app``).
    """
    store = getattr(application.state, "conversation_store", None)
    if store is None:
        store = InMemoryConversationStore()
        application.state.conversation_store = store
    return store


#: TTL for the persona-scope grounding corpus cache (HU-2070). Memory content
#: changes only through ingestion (never through chat), so the cache trades at
#: most this much ingestion-to-groundable lag for skipping a backend scan +
#: re-tokenization on every persona-voiced turn.
_GROUNDING_SCOPE_CACHE_TTL_SECONDS = 60.0


async def _persona_scope_grounding_refs(
    application: FastAPI, binding: PersonaBinding
) -> list[MemoryNode] | None:
    """Fetch the persona-scoped G4-admissible refs for §7.4.2 grounding (HU-2070).

    Wraps :meth:`ContextBuilder.persona_scoped_grounding_refs` with a
    per-app TTL cache keyed by persona + disclosure scope + era boundary.
    Returns ``None`` (and logs) when the scan fails — the caller then runs
    the alignment guard with the turn's refs only, i.e. the stricter
    pre-HU-2070 behavior; degrading toward over-suppression, never toward
    letting un-grounded claims through.
    """
    cache: dict[tuple[str, str, str], tuple[float, list[MemoryNode]]] = (
        application.state.grounding_scope_cache
    )
    key = (
        str(binding.persona.id),
        binding.requester_tier.disclosure_scope.value,
        str(binding.persona.era_knowledge_boundary),
    )
    now = time.monotonic()
    cached = cache.get(key)
    if cached is not None and now - cached[0] < _GROUNDING_SCOPE_CACHE_TTL_SECONDS:
        return cached[1]
    try:
        refs = await application.state.context_builder.persona_scoped_grounding_refs(
            persona=binding.persona,
            requester_tier=binding.requester_tier,
            backend=binding.backend,
        )
    except Exception:
        logger.exception(
            "persona-scope grounding scan failed (persona=%s); "
            "falling back to turn-refs-only grounding",
            binding.persona.id,
        )
        return None
    cache[key] = (now, refs)
    return refs


async def _adjudicate_alignment_suppression(
    application: FastAPI,
    *,
    llm,
    binding: PersonaBinding,
    persona_scope_refs,
    alignment,
    original_text: str,
    budget_fallback: bool,
) -> JudgeVerdict | None:
    """Judge backstop for a suppressed §7.4.2 turn (HU-2161). Mutates nothing
    when no adjudication runs; otherwise prunes judge-supported claims off
    ``alignment.ungrounded`` and, when all flagged claims clear, restores the
    original reply (``disposition`` back to ``passed``).

    Returns the :class:`~huible.safety.judge.JudgeVerdict` when adjudication
    ran (including ``unavailable``), else ``None``:

    * ``None`` — the turn passed the filter outright, the suppression is
      policy-only (identity / advice pattern violations are deterministic and
      never judgeable), or the reply came from the budget-fallback fake voice
      (its suppressions are the deterministic fixture class).
    * ``supported`` — every flagged biographical / relationship claim is
      consistent with the persona record: claims cleared, reply restored.
    * ``fabricated`` — at least one flagged claim is judge-confirmed
      confabulation: suppression stands, §3 Sev-1 (A) page-worthy.
    * ``unavailable`` — no real judge ran (fake provider, timeout, error):
      suppression stands *unconfirmed* — never page-worthy.
    """
    if alignment.disposition != "suppressed":
        return None
    judgeable = [
        c
        for c in alignment.ungrounded
        if c.category not in ("identity", "advice")
    ]
    if not judgeable:
        return None
    if budget_fallback or not judge_eligible(llm):
        return None

    digest = build_canon_digest(
        persona_name=binding.persona.name,
        voice_instructions=binding.persona.voice_instructions,
        era_knowledge_boundary=binding.persona.era_knowledge_boundary,
        persona_scope_refs=persona_scope_refs,
    )
    verdict = await adjudicate_alignment_claims(
        llm=llm,
        persona_name=binding.persona.name,
        canon_digest=digest,
        claims=alignment.ungrounded,
    )
    if verdict.outcome == "supported":
        # Judge cleared every judgeable claim: drop them from the un-grounded
        # set. Any remaining policy (identity/advice) claims keep the
        # suppression; with none left the turn passes with its original text.
        cleared = {c.text for c in judgeable}
        alignment.ungrounded = [
            c for c in alignment.ungrounded if c.text not in cleared
        ]
        if not alignment.ungrounded:
            alignment.disposition = "passed"
            alignment.text = original_text
        record_alignment_judge_overturn()
        logger.warning(
            "alignment judge cleared flagged claims (persona=%s, %d cleared); "
            "original reply restored (judge reason: %s)",
            binding.persona.id,
            len(cleared),
            verdict.reason,
        )
    return verdict


def _log_alignment_suppression(
    application: FastAPI,
    *,
    conversation_id: str | None,
    alignment,
    judge_verdict: JudgeVerdict | None,
    policy_only: bool,
    confirmed: bool,
) -> None:
    """Record the suppression rationale (HU-2161 acceptance #2).

    Server-side WARNING with the flagged claim texts, categories, salient
    entities, and the judge outcome/reason — the audit trail clinical review
    needs to adjudicate whether a suppression was a true catch or a Phase-1
    content-overlap false positive.
    """
    claims_desc = "; ".join(
        f"[{c.category}] {c.text!r} (entities: {', '.join(c.salient_entities) or '-'})"
        for c in alignment.ungrounded
    )
    judge_desc = (
        "no adjudication"
        if judge_verdict is None
        else f"judge={judge_verdict.outcome} reason={judge_verdict.reason!r}"
    )
    logger.warning(
        "alignment suppression: session=%s policy_only=%s confirmed=%s %s | claims: %s",
        conversation_id,
        policy_only,
        confirmed,
        judge_desc,
        claims_desc,
    )


def _history(application: FastAPI, conversation_id: str | None) -> list[ConversationTurn]:
    """Return the conversation history window for a conversation id.

    Read through the wired :class:`ConversationStore` (in-memory default or the
    durable Postgres backend). New conversations start empty. Durability across
    restarts is the HU-1440 fix.
    """
    return _conversation_store(application).get_history(conversation_id)


def _record_turn(
    application: FastAPI, conversation_id: str | None, message: str, reply: str
) -> None:
    """Append the inbound + outbound turns to the conversation log."""
    store = _conversation_store(application)
    store.append_turn(conversation_id, ConversationTurn(speaker="user", content=message))
    store.append_turn(conversation_id, ConversationTurn(speaker="persona", content=reply))


def _session_meta(application: FastAPI, conversation_id: str | None) -> SessionMetaView:
    """Build per-session observability metadata for the trace (G7).

    Turn count is derived from the conversation history (every user + persona
    pair = one turn). Phase-1 emits the signal; it enforces nothing on it
    (HU-1407 §7.1 G7). The dosage gate lands post-Phase-1.
    """
    history = _history(application, conversation_id)
    # History holds [user, persona, user, persona, …] → turns = pairs rounded up.
    turn_count = max(1, (len(history) + 1) // 2)
    return SessionMetaView(turn_count=turn_count)


def _log_chat_trace(
    application: FastAPI,
    conversation_id: str | None,
    *,
    action: str,
    fired_flags: tuple[str, ...] = (),
    ungrounded: int | None = None,
    claim_count: int | None = None,
    disposition: str | None = None,
) -> None:
    """Emit one ``chat.trace`` stdout line per chat turn (HU-1442).

    The risk-enforcement, claim-alignment, and dosage-pause signals are
    otherwise response-only ``.trace`` fields with no server-side aggregation
    (rollout-plan flagged concern #3). This folds them into the same stdout
    stream as ``consent.record`` (via :class:`_JsonLineFormatter`) so the
    daily review can ``grep chat.trace`` across all five telemetry surfaces.
    """
    turn_count = _session_meta(application, conversation_id).turn_count
    flags = ",".join(fired_flags) if fired_flags else "-"
    ungrounded_field = (
        f"{ungrounded}/{claim_count}"
        if ungrounded is not None and claim_count is not None
        else "-/-"
    )
    logger.info(
        "chat.trace session=%s action=%s fired_flags=%s ungrounded=%s disposition=%s turn_count=%s",
        conversation_id or "-",
        action,
        flags,
        ungrounded_field,
        disposition or "n/a",
        turn_count,
    )


def _mark_crisis_session(application: FastAPI, conversation_id: str | None) -> None:
    """Record that this session has had at least one G1 crisis turn (§7.4.4 §3).

    The ``crisis_history`` session signal lowers the handoff threshold for the
    rest of the session (matrix §3): a repeat-crisis session tightens by
    default. Recorded through the wired :class:`ConversationStore` so the
    marker survives restarts (HU-1440).
    """
    _conversation_store(application).mark_crisis(conversation_id)


def _distress_trend_rising(
    application: FastAPI,
    *,
    message: str,
    conversation_id: str | None,
    classifier: CrisisClassifier,
) -> bool:
    """Derive the §3 "escalating distress trend" signal from session history.

    Deterministic derivation (matrix §3): re-grade the current message plus the
    prior user turns in the session window; when **two or more** of the most
    recent user turns (including the current one) grade as DISTRESS, the trend
    is rising. This is the conservative proxy for a real affect tracker — it
    fires on sustained distress, not a single distress turn, so a one-off
    distress message does not auto-escalate to handoff. The classifier is the
    same shared G1/G3 signal (HU-1407 §7.1 G3), so the trend is consistent
    with the per-turn affect already driving the G3 branch.
    """
    if not conversation_id:
        recent_user_messages = [message]
    else:
        history = _history(application, conversation_id)
        prior_user = [t.content for t in history if t.speaker == "user"]
        # Last 2 prior user turns + the current message → window of 3.
        recent_user_messages = [*prior_user[-2:], message]
    if not recent_user_messages:
        return False
    distress_count = sum(
        1
        for msg in recent_user_messages
        if classify_user_message(msg, classifier=classifier).affect is UserAffect.DISTRESS
    )
    return distress_count >= 2


def _risk_session_signals(
    application: FastAPI,
    *,
    message: str,
    conversation_id: str | None,
    dosage_cap_turns: int,
    crisis_classifier: CrisisClassifier,
) -> RiskSessionSignals:
    """Build the §3 session-signals view for the enforcement engine.

    Derives the three matrix-§3 signals from the conversation store:

    * **dosage** — ``turn_count`` from :func:`_session_meta`; the cap comes from
      settings (``risk_dosage_cap_turns``); ``0`` disables the cap-driven pause.
    * **crisis_history** — True iff the session has had a prior G1 crisis turn
      (tracked via :func:`_mark_crisis_session`).
    * **distress_trend_rising** — :func:`_distress_trend_rising` (≥2 distress
      turns in the recent window).
    """
    session_meta = _session_meta(application, conversation_id)
    crisis_history = _conversation_store(application).has_crisis_history(conversation_id)
    trend = _distress_trend_rising(
        application,
        message=message,
        conversation_id=conversation_id,
        classifier=crisis_classifier,
    )
    cap = dosage_cap_turns if dosage_cap_turns and dosage_cap_turns > 0 else None
    # ``turn_count`` for enforcement includes the turn *about to be served*
    # (session_meta counts only completed turns). The cap should trigger
    # BEFORE the turn that would exceed it, not after — so a session at the
    # cap gets one more turn, and the next one pauses.
    enforcement_turn_count = session_meta.turn_count + 1
    return RiskSessionSignals(
        turn_count=enforcement_turn_count,
        duration_seconds=session_meta.duration_seconds,
        dosage_cap_turns=cap,
        distress_trend_rising=trend,
        crisis_history=crisis_history,
    )


def _risk_enforcement_view(report) -> RiskEnforcementView:
    """Render an :class:`~huible.safety.risk.EnforcementReport` as a trace view."""
    return RiskEnforcementView(
        action=report.action.value,
        required_actions=sorted({a.value for a in report.required_actions}),
        fired_flags=[f.value for f in report.fired_flags],
        session_signal_actions=[a.value for a in report.session_signal_actions],
        pre_empted_by_crisis=report.pre_empted_by_crisis,
    )


def _escalate_risk_and_build_trace(
    application: FastAPI,
    *,
    trigger: str,
    persona_id: UUID,
    conversation_id: str | None,
    risk_flags: list[str],
) -> HandoffTicketView:
    """Route a §7.4.4 risk-driven handoff into the queue and build the trace view.

    Matrix §4 composition: the risk-driven ``handoff`` reuses the G1 warm
    non-persona posture + crisis-line display + the §7.4.1 queue. The only
    difference from :func:`_escalate_and_build_trace` is the audit row's
    ``trigger_signal`` — it carries a ``risk:`` prefix so clinical review
    distinguishes a G1-crisis escalation from a risk-flag-driven one. Fail-safe
    is identical (queue errors degrade to the G1 safe response).
    """
    queue: HandoffQueue = application.state.handoff_queue
    result = escalate_risk_to_human(
        trigger=trigger,
        queue=queue,
        persona_id=str(persona_id),
        conversation_id=conversation_id,
        risk_flags=risk_flags,
        resources=application.state.crisis_resources or None,
    )
    _page_on_enqueue(application, result.ticket)
    return _ticket_view(result.ticket, response_text=result.response_text)


def _escalate_and_build_trace(
    application: FastAPI,
    *,
    message: str,
    crisis_result,
    persona_id: UUID,
    conversation_id: str | None,
    risk_flags: list[str],
) -> HandoffTicketView:
    """Route a G1 crisis turn into the human-handoff queue and build the trace view.

    §7.4.1 (HU-1421): the queue is the escalation-to-human path. It always runs
    so §10.1 invariant 5 ("audit every escalation") holds even when no
    responder is staffed — the InMemoryHandoffQueue default (0 responders)
    records every ticket as ``degraded`` and the user still gets the G1
    non-persona safe response.

    Fail-safe (HU-1407 §10.1 #2): if the queue raises, ``escalate_to_human``
    degrades to the G1 safe response and records the ticket as ``degraded``. The
    persona path is unreachable from here.
    """
    queue: HandoffQueue = application.state.handoff_queue
    result = escalate_to_human(
        message,
        crisis_result=crisis_result,
        queue=queue,
        persona_id=str(persona_id),
        conversation_id=conversation_id,
        risk_flags=risk_flags,
        resources=application.state.crisis_resources or None,
    )
    _page_on_enqueue(application, result.ticket)
    return _ticket_view(result.ticket, response_text=result.response_text)


def _page_on_enqueue(application: FastAPI, ticket) -> None:
    """Page the on-call immediately on a crisis handoff outcome (HU-1450 + HU-1451).

    Two distinct paging paths, both fire-and-forget and never throttled behind
    the >10%/1h aggregate:

    * ``ENQUEUED`` → the primary Sev-1 trigger: a responder was assigned, so
      page the on-call at :data:`~huible.api.paging.PAGE_SEVERITY_CRISIS` so the
      responder *actually* sees the ticket on a real device. Paging is additive
      on top of ``ENQUEUED`` (§10.1 #2 fail-safe stays authoritative).
    * ``DEGRADED`` → §3 Sev-1 (B) (HU-1451 trigger #3): the net failed — no
      responder was available / the enqueue failed. A grieving user in crisis
      was NOT helped by a human, which is itself a Sev-1 operational failure
      requiring immediate ceiling intervention. Paged at ``sev-1`` with the
      :data:`~huible.api.paging.PAGE_TRIGGER_DEGRADED_NET` label (distinct from
      the crisis-enqueue page; kill-switch-eligible).

    Paging is best-effort: a transport failure must never break a clinical
    turn. Every pager implementation swallows transport errors and falls back
    to the log line; the failure count it returns is recorded on the
    ``huible_paging_failures_total{trigger}`` counter (AC #3). The catch here
    is defense-in-depth so an unexpected pager also degrades silently.
    """
    pager: Pager = application.state.pager
    window = application.state.coverage_window_label
    try:
        if ticket.outcome is HandoffOutcome.ENQUEUED:
            failures = pager.page(
                ticket,
                severity=PAGE_SEVERITY_CRISIS,
                window=window,
                trigger=PAGE_TRIGGER_CRISIS_ENQUEUE,
            )
            record_paging_failures(PAGE_TRIGGER_CRISIS_ENQUEUE, failures)
        elif ticket.outcome is HandoffOutcome.DEGRADED:
            failures = page_degraded_net(pager, ticket=ticket, window=window)
            record_paging_failures(PAGE_TRIGGER_DEGRADED_NET, failures)
    except Exception:  # pragma: no cover - defensive; paging must never break a turn
        logger.exception("handoff.page failed; clinical turn continues unaffected")


def _page_sev1_fire_and_forget(
    application: FastAPI,
    *,
    trigger: str,
    ticket=None,
    persona_id: str | None = None,
) -> None:
    """Fire-and-forget Sev-1 page for the persona-path triggers (HU-1451 #2/#4).

    Used by the un-grounded-claim-leak (#2) and consent-bypass (#4) triggers,
    which are detected on the persona-voiced path where there is no handoff
    ticket. Delegates to :func:`huible.api.paging.page_sev1_signal`, records
    real-channel failures on the ``huible_paging_failures_total`` counter, and
    swallows every error so a page never breaks a clinical turn.
    """
    pager: Pager = application.state.pager
    window = application.state.coverage_window_label
    try:
        failures = page_sev1_signal(
            pager,
            ticket=ticket,
            trigger=trigger,
            window=window,
            persona_id=persona_id,
        )
        record_paging_failures(trigger, failures)
    except Exception:  # pragma: no cover - defensive; paging must never break a turn
        logger.exception("Sev-1 page failed; clinical turn continues unaffected")


def _ticket_view(ticket, *, response_text: str) -> HandoffTicketView:
    """Render a handoff ticket + the user-facing acknowledgement as a trace view."""
    return HandoffTicketView(
        ticket_id=ticket.id,
        outcome=ticket.outcome.value,
        trigger_signal=ticket.trigger_signal,
        affect=ticket.affect,
        risk_flags=list(ticket.risk_flags),
        matched_patterns=list(ticket.matched_patterns),
        sla_target_seconds=ticket.sla_target_seconds,
        created_at=ticket.created_at,
        responder_id=ticket.responder_id,
        degrade_reason=ticket.degrade_reason,
        clinical_review_note=ticket.clinical_review_note,
        resources_shown=True,
        user_acknowledgement=response_text,
    )


def _queue_item_view(
    ticket, *, with_sla: bool, now: datetime | None = None
) -> HandoffQueueItemView:
    """Render a handoff ticket as a staffed-responder work-queue row (HU-1428).

    ``with_sla`` attaches the live SLA status for pending rows (the breach
    countdown). Historical/audit rows pass ``with_sla=False`` since the live
    countdown is meaningless once the ticket is resolved.
    """
    sla_view: HandoffSLAStatusView | None = None
    if with_sla:
        status_ = sla_status(ticket, now=now)
        sla_view = HandoffSLAStatusView(
            breached=status_.breached,
            seconds_since_created=status_.seconds_since_created,
            seconds_to_sla=status_.seconds_to_sla,
            seconds_overdue=status_.seconds_overdue,
        )
    return HandoffQueueItemView(
        id=ticket.id,
        ticket_id=ticket.id,
        outcome=ticket.outcome.value,
        trigger_signal=ticket.trigger_signal,
        affect=ticket.affect,
        persona_id=ticket.persona_id,
        conversation_id=ticket.conversation_id,
        risk_flags=list(ticket.risk_flags),
        matched_patterns=list(ticket.matched_patterns),
        sla_target_seconds=ticket.sla_target_seconds,
        created_at=ticket.created_at,
        resolved_at=ticket.resolved_at,
        responder_id=ticket.responder_id,
        degrade_reason=ticket.degrade_reason,
        clinical_review_note=ticket.clinical_review_note,
        sla_status=sla_view,
    )


def _telemetry_view(telemetry) -> HandoffTelemetryView:
    """Render the SLA/outcome telemetry dataclass as the dashboard view."""
    return HandoffTelemetryView(
        total=telemetry.total,
        by_outcome=dict(telemetry.by_outcome),
        pending=telemetry.pending,
        answered=telemetry.answered,
        degraded=telemetry.degraded,
        abandoned=telemetry.abandoned,
        pending_breached=telemetry.pending_breached,
        answered_breached_sla=telemetry.answered_breached_sla,
        degrade_rate=telemetry.degrade_rate,
        pending_breach_rate=telemetry.pending_breach_rate,
        answered_breach_rate=telemetry.answered_breach_rate,
    )


def _mint_conversation_id() -> str:
    import uuid as _uuid

    return str(_uuid.uuid4())


def _view(node) -> ActivatedMemoryView:
    """Render a provenance-safe view of an included memory node."""
    confidence = (node.metadata or {}).get(CONFIDENCE_LEVEL_METADATA_KEY)
    return ActivatedMemoryView(
        id=node.id,
        content=node.content,
        content_type=node.content_type.value,
        disclosure_scope=node.disclosure_scope.value,
        confidence_level=str(confidence) if confidence is not None else "unknown",
        activation_score=0.0,
    )


#: Bare default app so ``uvicorn huible.api.app:app`` boots. Chat will 401
#: until a key store + persona registry are seeded (correct behavior).
app = create_app()

"""FastAPI ASGI app for the Huible memory-driven persona engine.

Server foundation (HU-1403) plus the M2 chat wiring (HU-1401 / HU-1406). Exposes:

* ``GET /health`` — top-level liveness / readiness probe with service +
  version + DB/pgvector connectivity (HU-1403).
* ``GET /api/v1/health`` — same probe under the versioned prefix (HU-1401).
* ``POST /api/v1/chat`` — text-in -> text-out persona chat via the
  PersonaGeneratorClient speaking voice (HU-1401).
* ``POST /api/v1/chat/{persona_id}`` — persona-scoped chat wired to the
  runtime LLM client (HU-1406 Phase-1 integration milestone:
  text -> retrieval -> LLM -> text). Returns a structured ``trace``.

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

import json
import logging
import time
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

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
from huible.api.schemas import (
    ActivatedMemoryView,
    AlignmentView,
    ChatRequest,
    ChatResponse,
    ChatResponseData,
    ChatTrace,
    ConsentAcknowledgeData,
    ConsentAcknowledgeRequest,
    ConsentAcknowledgeResponse,
    ConsentCardView,
    ExcludedMemoryRefView,
    HandoffTicketView,
    HealthCheck,
    HealthResponse,
    PersonaChatRequest,
    PersonaChatResponse,
    RiskEnforcementView,
    SafetyEventView,
    SessionMetaView,
)
from huible.api.settings import Settings, get_settings
from huible.llm.client import LLMClient, build_llm_client
from huible.memory.protocol import MemoryBackend
from huible.memory.store import PostgresMemoryBackend
from huible.persona.context import (
    CONFIDENCE_LEVEL_METADATA_KEY,
    ContextBuilder,
    ConversationTurn,
    RelationshipTier,
)
from huible.persona.generator import PersonaGeneratorClient, make_generator_client
from huible.safety import (
    PAUSE_SESSION_RESPONSE,
    PROXY_USER_PAUSE_RESPONSE,
    REFUSE_TOPIC_FALLBACK_RESPONSE,
    ConsentCardProvider,
    ConsentGate,
    CrisisClassifier,
    DefaultConsentCard,
    DeterministicCrisisClassifier,
    HandoffQueue,
    InMemoryConsentGate,
    InMemoryHandoffQueue,
    InMemoryRiskProfile,
    RiskFlag,
    RiskProfileProvider,
    RiskSessionSignals,
    UserAffect,
    apply_affect_guard,
    apply_alignment_guard,
    build_reframe_addendum,
    classify_user_message,
    enforce_risk_flags,
    escalate_risk_to_human,
    escalate_to_human,
)

logger = logging.getLogger(__name__)

__all__ = ["app", "configure_logging", "create_app"]

#: Disclosure-scope (request wire) -> requester RelationshipTier (context layer).
_DISCLOSURE_TO_TIER: dict[str, RelationshipTier] = {
    tier.disclosure_scope.value: tier for tier in RelationshipTier
}

#: Relationship name (request wire) -> requester RelationshipTier (context layer).
_RELATIONSHIP_TO_TIER: dict[str, RelationshipTier] = {
    tier.value: tier for tier in RelationshipTier
}


def _resolve_requester_tier(disclosure_tier: str) -> RelationshipTier:
    """Map a request ``disclosure_tier`` to the requester RelationshipTier."""
    tier = _DISCLOSURE_TO_TIER.get(disclosure_tier)
    if tier is None:  # pragma: no cover - schema pre-validates, defensive only
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "status": 400,
                    "message": f"Invalid disclosure_tier: {disclosure_tier!r}",
                }
            },
        )
    return tier


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


def configure_logging(settings: Settings) -> None:
    """Configure structured (JSON-line) root logging from settings.

    Idempotent: only attaches a structured handler once. Called from the app
    lifespan on startup, so the test suite (which does not boot the lifespan)
    is unaffected.
    """
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    root = logging.getLogger()
    if not any(isinstance(h.formatter, _JsonLineFormatter) for h in root.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(_JsonLineFormatter())
        root.addHandler(handler)
    root.setLevel(level)
    logging.getLogger("uvicorn").setLevel(level)


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


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Startup/shutdown: configure logging and manage the memory backend."""
    settings: Settings = application.state.settings
    configure_logging(settings)
    application.state.memory_backend = await _init_memory_backend(settings)
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
    application.state.llm_client = llm_client or build_llm_client(
        resolved_settings.to_llm_config()
    )
    application.state.context_builder = context_builder or ContextBuilder()
    # Runtime clinical guardrails (HU-1413 / HU-1407 §7.3). The crisis
    # classifier is the G1 synchronous pre-generation check AND the shared G3
    # affect signal. The deterministic impl is the default; tests inject a
    # pinned instance. ``crisis_resources`` makes the warm-escalation line a
    # config swap (regional line / human-handoff queue) rather than a re-build.
    application.state.crisis_classifier = crisis_classifier or DeterministicCrisisClassifier()
    application.state.crisis_resources = crisis_resources or {}
    # §7.4.1 human-handoff (crisis escalation) queue. The default is the
    # in-memory queue with ``available_responders=0`` from settings — the
    # honest pre-real-user posture: every escalation degrades to the G1
    # non-persona safe response (never drops, never the persona voice) and is
    # still recorded for audit, until a staffed responder roster exists. A real
    # backend (Postgres / Redis / external paging) drops in via the
    # HandoffQueue Protocol pre-real-launch. There is intentionally no
    # "disable handoff" knob: §10.1 invariant 5 requires auditing *every*
    # escalation, so the queue always runs and the responder count is the only
    # operational lever.
    application.state.handoff_queue = handoff_queue or InMemoryHandoffQueue(
        available_responders=resolved_settings.handoff_available_responders,
        responder_id_pool=tuple(resolved_settings.handoff_responder_pool_list),
        sla_target_seconds=resolved_settings.handoff_sla_target_seconds,
    )
    # §7.4.3 G6 first-use reality-framing / consent gate. The gate enforces that
    # no persona-voiced reply leaves the chat path before the session has
    # acknowledged the consent card (HU-1423). The default backend is the
    # in-memory gate (key-free pre-real-users); a real backend (Postgres /
    # Redis / the onboarding-terminal's session store) drops in here
    # pre-real-launch. The card content is injectable: the DefaultConsentCard
    # ships the Onboarding Agent's drafted reality-framing + consent copy
    # (HU-1429); a clinically-revised revision from HU-1430 swaps in via
    # consent_card_provider without touching the gate. The deceased persona
    # never voices the consent (§7.1 H1) — the card is a non-persona system
    # message, structurally disjoint from generation.
    application.state.consent_gate = consent_gate or InMemoryConsentGate()
    application.state.consent_card_provider = consent_card_provider or DefaultConsentCard()
    # §7.4.4 G8 risk-flag enforcement. The risk profile is the intake-derived
    # source of the per-session + per-persona risk flags (loss_of_child,
    # minor_decedent, recent_loss, non_acceptance, proxy_user). The default
    # InMemoryRiskProfile is empty (no flags) so the pre-real-user suite runs
    # key-free and the default persona-chat turn is unaffected; tests inject a
    # seeded instance to exercise each flag → action path. Pre-real-launch the
    # onboarding / intake path populates this (memory-content-derived
    # loss_of_child, persona-age-derived minor_decedent, death-date-derived
    # recent_loss, intake-assessment non_acceptance, identity-verification
    # proxy_user) without touching the chat endpoint.
    application.state.risk_profile = risk_profile or InMemoryRiskProfile()
    application.state.start_time = start_time if start_time is not None else time.time()
    # Default: no DB wired. The lifespan constructs the real backend on startup
    # when an asyncpg DATABASE_URL is configured; health reads this attribute.
    application.state.memory_backend: MemoryBackend | None = None

    _register_routes(application)
    return application


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
        response_model=ChatResponse,
        tags=["Chat"],
        summary="Persona chat (text-in -> text-out)",
    )
    async def chat(
        body: ChatRequest,
        principal: ApiKeyPrincipal = Depends(authenticate),
        registry: PersonaRegistry = Depends(get_persona_registry),
    ) -> ChatResponse:
        """Persona chat endpoint.

        Wiring: inbound message -> ContextBuilder -> PersonaGeneratorClient
        -> reply. The ContextBuilder hard-excludes LOW / QUARANTINE /
        missing-confidence memories, so the reply is grounded in provenance-safe
        HIGH/MEDIUM L1 memory only.

        Auth: persona-scoped bearer key (401 when missing/unknown). If the
        request specifies ``persona_id`` it must match the key's scope (403).
        """
        target_persona_id = body.persona_id or principal.persona_id
        if target_persona_id != principal.persona_id:
            raise_forbidden()

        try:
            disclosure_tier = body.requester_disclosure()
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

        requester_tier = _resolve_requester_tier(disclosure_tier)
        binding: PersonaBinding | None = registry.get(target_persona_id, requester_tier)
        if binding is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": {
                        "code": "PERSONA_NOT_FOUND",
                        "status": 404,
                        "message": f"No persona registered for id {target_persona_id}",
                    }
                },
            )

        ctx = await application.state.context_builder.build(
            persona=binding.persona,
            requester_tier=binding.requester_tier,
            backend=binding.backend,
            query_embedding_content=_embed(body.message),
            conversation_history=_history(application, body.conversation_id),
            current_message=body.message,
        )

        prompt = ctx.render()
        generator: PersonaGeneratorClient = application.state.generator
        reply = await generator.generate(prompt)

        _record_turn(application, body.conversation_id, body.message, reply)

        return ChatResponse(
            data=ChatResponseData(
                reply=reply,
                conversation_id=body.conversation_id or _mint_conversation_id(),
                activated_memories=[_view(node) for node in ctx.included_memories],
                exclusion_counts=dict(ctx.exclusion_counts),
            )
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
        if persona_id != principal.persona_id:
            raise_forbidden()

        try:
            relationship = body.requester_relationship()
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

        requester_tier = _resolve_relationship(relationship)
        binding: PersonaBinding | None = registry.get(persona_id, requester_tier)
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
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": {
                        "code": "CONSENT_REQUIRED",
                        "status": 409,
                        "message": (
                            "Reality-framing consent is required before this "
                            "session can proceed."
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
        )

        prompt = ctx.render()
        system_prompt = ctx.system_prompt
        if enforcement.forces_reframe:
            system_prompt = system_prompt + "\n\n" + build_reframe_addendum(
                binding.persona.name
            )
        response_text = await llm.generate(prompt, system_prompt=system_prompt)

        # G3 generation-time guard: on the distress branch (forced or graded),
        # replace a sarcastic / dismissive generation with a safe grounded
        # fallback. Conservative — only replaces on distress when a concrete
        # pattern fires.
        response_text, _suppressed = apply_affect_guard(
            response_text, affect=effective_affect
        )

        # §7.4.2 generation-time claim->ref alignment filter. The retrieval-side
        # G4 firewall guarantees the *prompt* only saw provenance-safe memory;
        # this is the generation-side backstop for a confabulating generator —
        # any factual / identity / advice claim in the reply must trace to a
        # retrieved ref (or the persona vault), or the turn is failed safely to
        # a claim-free reflection fallback. Runs on every persona-voiced turn
        # (crisis already returned); the report feeds the trace alignment view
        # for clinical review. See huible.safety.alignment.
        alignment = apply_alignment_guard(
            response_text, refs=ctx.included_memories, persona=binding.persona
        )
        response_text = alignment.text

        _record_turn(application, body.conversation_id, body.message, response_text)

        return PersonaChatResponse(
            response=response_text,
            trace=ChatTrace(
                memory_refs=[str(node.id) for node in ctx.included_memories],
                provenance_tiers=sorted({node.tier.value for node in ctx.included_memories}),
                excluded_memory_refs=[
                    ExcludedMemoryRefView(id=ref.id, reason=ref.reason)
                    for ref in ctx.excluded_memory_refs
                ],
                provider=provider_label,
                framing_version=ctx.framing_version,
                distress_grounding=ctx.distress_grounding,
                session_meta=_session_meta(application, body.conversation_id),
                alignment=AlignmentView(
                    claim_count=alignment.claim_count,
                    ungrounded_claim_count=alignment.ungrounded_count,
                    disposition=alignment.disposition,
                    ungrounded_by_category=alignment.category_counts(),
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
        card_version = body.card_version or card_provider.get_card(
            binding.persona.name
        ).version

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


# --- helpers ----------------------------------------------------------------


def _embed(message: str) -> list[float]:
    """Token-hashed embedding for the inbound message.

    Mirrors :func:`huible.conversation.simple_embedding` semantics so retrieval
    hits memories that share keywords with the inbound turn. Kept local to the
    API layer to avoid coupling the HTTP path to the demo conversation module.
    """
    import hashlib

    dim = 64
    words = [w.strip(".,!?;:\"'()[]") for w in message.lower().split() if len(w) > 2]
    vec = [0.0] * dim
    for word in words:
        h = int(hashlib.md5(word.encode("utf-8")).hexdigest(), 16)
        vec[h % dim] += 1.0
    norm = sum(x * x for x in vec) ** 0.5
    if norm > 1e-6:
        vec = [x / norm for x in vec]
    return vec


def _history(application: FastAPI, conversation_id: str | None) -> list[ConversationTurn]:
    """Return the conversation history window for a conversation id.

    State is held in-process on ``app.state`` keyed by conversation id. New
    conversations start empty. This is the M2 in-process default; a persistent
    store lands with the conversation-service follow-up.
    """
    if not conversation_id:
        return []
    store: dict[str, list[ConversationTurn]] = getattr(application.state, "conversations", {})
    return list(store.get(conversation_id, []))


def _record_turn(
    application: FastAPI, conversation_id: str | None, message: str, reply: str
) -> None:
    """Append the inbound + outbound turns to the in-process conversation log."""
    if not conversation_id:
        return
    if not hasattr(application.state, "conversations"):
        application.state.conversations = {}
    history = application.state.conversations.setdefault(conversation_id, [])
    history.append(ConversationTurn(speaker="user", content=message))
    history.append(ConversationTurn(speaker="persona", content=reply))


def _session_meta(
    application: FastAPI, conversation_id: str | None
) -> SessionMetaView:
    """Build per-session observability metadata for the trace (G7).

    Turn count is derived from the in-process conversation log (every user +
    persona pair = one turn). Phase-1 emits the signal; it enforces nothing on
    it (HU-1407 §7.1 G7). The dosage gate lands post-Phase-1.
    """
    if not conversation_id or not hasattr(application.state, "conversations"):
        return SessionMetaView(turn_count=1)
    history: list[ConversationTurn] = application.state.conversations.get(conversation_id, [])
    # History holds [user, persona, user, persona, …] → turns = pairs rounded up.
    turn_count = max(1, (len(history) + 1) // 2)
    return SessionMetaView(turn_count=turn_count)


def _mark_crisis_session(application: FastAPI, conversation_id: str | None) -> None:
    """Record that this session has had at least one G1 crisis turn (§7.4.4 §3).

    The ``crisis_history`` session signal lowers the handoff threshold for the
    rest of the session (matrix §3): a repeat-crisis session tightens by
    default. Held in-process on ``app.state.crisis_sessions``; a cross-session
    backend lands pre-real-launch alongside the real conversation store.
    """
    if not conversation_id:
        return
    if not hasattr(application.state, "crisis_sessions"):
        application.state.crisis_sessions: set[str] = set()
    application.state.crisis_sessions.add(conversation_id)


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
        history: list[ConversationTurn] = getattr(
            application.state, "conversations", {}
        ).get(conversation_id, [])
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

    Derives the three matrix-§3 signals from in-process session state:

    * **dosage** — ``turn_count`` from :func:`_session_meta`; the cap comes from
      settings (``risk_dosage_cap_turns``); ``0`` disables the cap-driven pause.
    * **crisis_history** — True iff the session has had a prior G1 crisis turn
      (tracked via :func:`_mark_crisis_session`).
    * **distress_trend_rising** — :func:`_distress_trend_rising` (≥2 distress
      turns in the recent window).
    """
    session_meta = _session_meta(application, conversation_id)
    crisis_history = bool(
        conversation_id
        and getattr(application.state, "crisis_sessions", set())
        and conversation_id in application.state.crisis_sessions
    )
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
    return _ticket_view(result.ticket, response_text=result.response_text)


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

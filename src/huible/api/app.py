"""FastAPI ASGI app for the Huible memory-driven persona engine.

Server foundation (HU-1403) plus the M2 chat wiring (HU-1401). Exposes:

* ``GET /health`` — top-level liveness / readiness probe with service +
  version + DB/pgvector connectivity (HU-1403).
* ``GET /api/v1/health`` — same probe under the versioned prefix (HU-1401).
* ``POST /api/v1/chat`` — text-in -> text-out persona chat (HU-1401).

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
      -> PersonaGeneratorClient (the speaking voice, HU-1400)
      -> reply

The chat path reads **only** HIGH/MEDIUM L1 memories. The ContextBuilder is the
only sanctioned bridge and hard-excludes LOW / QUARANTINE / missing-confidence
memories before the generator ever sees them. The response surfaces the
admissible activated memories and exclusion counts so callers and tests can
prove the contamination guard fired.

The remaining spec endpoints (memories CRUD, retrieve, quarantine adjudication)
land incrementally; health + chat are the M2 priority per the issue.

Construction is dependency-injected via :func:`create_app` so tests wire a
seeded key store, persona registry, and (mock) generator without touching
production code paths. The module-level :data:`app` is a bare default instance
so ``uvicorn huible.api.app:app`` boots and ``/health`` returns 200; chat will
401 until keys are seeded (correct behavior).
"""

from __future__ import annotations

import json
import logging
import time
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

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
    ChatRequest,
    ChatResponse,
    ChatResponseData,
    HealthCheck,
    HealthResponse,
)
from huible.api.settings import Settings, get_settings
from huible.memory.protocol import MemoryBackend
from huible.memory.store import PostgresMemoryBackend
from huible.persona.context import (
    CONFIDENCE_LEVEL_METADATA_KEY,
    ContextBuilder,
    ConversationTurn,
    RelationshipTier,
)
from huible.persona.generator import PersonaGeneratorClient, make_generator_client

logger = logging.getLogger(__name__)

__all__ = ["app", "configure_logging", "create_app"]

#: Disclosure-scope (request wire) -> requester RelationshipTier (context layer).
_DISCLOSURE_TO_TIER: dict[str, RelationshipTier] = {
    tier.disclosure_scope.value: tier for tier in RelationshipTier
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
    context_builder: ContextBuilder | None = None,
    settings: Settings | None = None,
    start_time: float | None = None,
) -> FastAPI:
    """Build a FastAPI app with injected dependencies.

    All parameters are optional; sensible defaults let ``uvicorn
    huible.api.app:app`` boot (chat will 401 without seeded keys). Tests pass a
    seeded key store + persona registry and usually the deterministic mock
    generator.
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
    application.state.context_builder = context_builder or ContextBuilder()
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

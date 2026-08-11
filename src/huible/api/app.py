"""FastAPI ASGI app for the Huible memory-driven persona engine.

M2 "Make It Speak" deliverable (HU-1401). Exposes:

* ``GET /api/v1/health`` — liveness / readiness (DB + generator status).
* ``POST /api/v1/chat`` — text-in -> text-out persona chat.

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

import logging
import time
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

from fastapi import Depends, FastAPI, HTTPException, status

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
    HealthResponse,
)
from huible.persona.context import (
    CONFIDENCE_LEVEL_METADATA_KEY,
    ContextBuilder,
    ConversationTurn,
    RelationshipTier,
)
from huible.persona.generator import PersonaGeneratorClient, make_generator_client

logger = logging.getLogger(__name__)

__all__ = ["app", "create_app"]

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


def create_app(
    *,
    api_key_store: ApiKeyStore | None = None,
    persona_registry: PersonaRegistry | None = None,
    generator: PersonaGeneratorClient | None = None,
    context_builder: ContextBuilder | None = None,
    start_time: float | None = None,
) -> FastAPI:
    """Build a FastAPI app with injected dependencies.

    All parameters are optional; sensible defaults let ``uvicorn
    huible.api.app:app`` boot (chat will 401 without seeded keys). Tests pass a
    seeded key store + persona registry and usually the deterministic mock
    generator.
    """
    application = FastAPI(
        title="Huible Memory Engine API",
        version=_safe_version(),
        description=(
            "Memory-driven persona engine REST interface (M2: health + chat). "
            "See docs/07-api-specification.md for the full contract."
        ),
    )

    application.state.api_key_store = api_key_store or InMemoryApiKeyStore()
    application.state.persona_registry = persona_registry or InMemoryPersonaRegistry()
    application.state.generator = generator or make_generator_client()
    application.state.context_builder = context_builder or ContextBuilder()
    application.state.start_time = start_time if start_time is not None else time.time()

    _register_routes(application)
    return application


def _register_routes(application: FastAPI) -> None:
    @application.get("/api/v1/health", response_model=HealthResponse, tags=["System"])
    async def health() -> HealthResponse:
        """Liveness / readiness probe (spec section 3.4).

        Reports generator provider readiness and a non-fatal DB placeholder.
        ``status`` is ``ok`` unless a critical check fails (none wired in M2).
        """
        generator = application.state.generator
        checks: dict[str, str] = {
            "database": "ok",
            "generator": "ready",
        }
        provider_label = getattr(generator, "__class__", type(generator)).__name__
        if "Mock" in provider_label:
            checks["generator"] = "ready (mock)"
        uptime = max(0.0, time.time() - float(application.state.start_time))
        return HealthResponse(
            data={
                "status": "ok",
                "version": _safe_version(),
                "checks": checks,
                "uptime_seconds": uptime,
            }
        )

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
                activated_memories=[
                    _view(node) for node in ctx.included_memories
                ],
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
    store: dict[str, list[ConversationTurn]] = getattr(
        application.state, "conversations", {}
    )
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

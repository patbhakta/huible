"""Pydantic request/response schemas for the Huible REST API.

Envelopes follow ``docs/07-api-specification.md``: successful bodies return
``{"data": ...}``. The chat endpoint is the M2 priority and is documented on
HU-1401; its request shape is:

``{"message": str, "persona_id?: UUID, "disclosure_tier?: str, "conversation_id?: str}``

and its response is:

``{"data": {"reply": str, "activated_memories?: list, "conversation_id": str}}``

The ``disclosure_tier`` field mirrors the spec's ``/retrieve`` contract
(``private | family | close_friends | all_contacts``) and is resolved to the
requester's :class:`~huible.persona.context.RelationshipTier` by the route
layer. When omitted it defaults to ``family`` (spec default).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

__all__ = [
    "ActivatedMemoryView",
    "ChatRequest",
    "ChatResponse",
    "ChatResponseData",
    "ChatTrace",
    "DataEnvelope",
    "HealthCheck",
    "HealthResponse",
    "PersonaChatRequest",
    "PersonaChatResponse",
    "RelationshipTierLiteral",
]

#: Admissible ``disclosure_tier`` request values (spec section 3.2).
RelationshipTierLiteral = str  # validated explicitly to map to RelationshipTier

_DISCLOSURE_TIERS: frozenset[str] = frozenset(
    {"private", "family", "close_friends", "all_contacts"}
)


class ChatRequest(BaseModel):
    """Body of ``POST /api/v1/chat``."""

    message: str = Field(..., min_length=1, description="Inbound user message.")
    persona_id: UUID | None = Field(
        default=None,
        description="Target persona. Defaults to the API key's scoped persona.",
    )
    disclosure_tier: str | None = Field(
        default=None,
        description=(
            "Requester relationship expressed as the max disclosure scope they "
            "may see: private | family | close_friends | all_contacts. "
            "Default: family."
        ),
    )
    conversation_id: str | None = Field(
        default=None,
        description="Opaque conversation id. Echoed back; new id minted when absent."
    )

    def requester_disclosure(self) -> str:
        """Return the disclosure tier, defaulting to ``family`` (spec)."""
        tier = (self.disclosure_tier or "family").strip().lower()
        if tier not in _DISCLOSURE_TIERS:
            raise ValueError(
                f"disclosure_tier must be one of {sorted(_DISCLOSURE_TIERS)}, got {tier!r}"
            )
        return tier


class ActivatedMemoryView(BaseModel):
    """Provenance-safe view of one memory that surfaced in the reply context.

    Only HIGH/MEDIUM L1 memories ever appear here — the ContextBuilder drops
    LOW/QUARANTINE before generation. Exposed so callers (and tests) can prove
    the contamination guard fired.
    """

    id: UUID
    content: str
    content_type: str
    disclosure_scope: str
    confidence_level: str
    activation_score: float


class ChatResponseData(BaseModel):
    """The ``data`` payload of a chat response."""

    reply: str
    conversation_id: str
    activated_memories: list[ActivatedMemoryView] = Field(default_factory=list)
    exclusion_counts: dict[str, int] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    """Full chat response envelope."""

    data: ChatResponseData


class HealthCheck(BaseModel):
    status: str
    version: str
    checks: dict[str, str] = Field(default_factory=dict)
    uptime_seconds: float = 0.0


class HealthResponse(BaseModel):
    data: HealthCheck


class DataEnvelope(BaseModel):
    """Generic ``{"data": ...}`` envelope for future endpoints."""

    data: Any


# --- Persona-scoped chat (HU-1406) -----------------------------------------


#: Admissible ``relationship`` request values. Maps 1:1 to the context
#: builder's :class:`~huible.persona.context.RelationshipTier`, which in turn
#: determines the disclosure scope a requester may see (INV-DS).
_PERSONA_RELATIONSHIPS: frozenset[str] = frozenset(
    {"intimate", "family", "close_friend", "acquaintance"}
)


class PersonaChatRequest(BaseModel):
    """Body of ``POST /api/v1/chat/{persona_id}`` (HU-1406).

    A minimal text-in contract for the Phase-1 integration milestone: the
    inbound ``message`` plus an optional ``relationship`` that selects the
    requester's disclosure tier (default ``family``).
    """

    message: str = Field(..., min_length=1, description="Inbound user message.")
    relationship: str | None = Field(
        default=None,
        description=(
            "Requester relationship to the persona: intimate | family | "
            "close_friend | acquaintance. Default: family."
        ),
    )

    def requester_relationship(self) -> str:
        """Return the relationship tier, defaulting to ``family`` (spec)."""
        tier = (self.relationship or "family").strip().lower()
        if tier not in _PERSONA_RELATIONSHIPS:
            raise ValueError(
                f"relationship must be one of {sorted(_PERSONA_RELATIONSHIPS)}, got {tier!r}"
            )
        return tier


class ChatTrace(BaseModel):
    """Structured retrieval/generation trace for audit + future F-tests.

    ``memory_refs`` and ``provenance_tiers`` describe only the memories that
    passed the provenance firewall (HIGH/MEDIUM confidence, in-era, in-scope).
    LOW / QUARANTINE confidence memories are dropped by the context builder
    before generation and therefore never appear here.
    """

    memory_refs: list[str] = Field(default_factory=list)
    provenance_tiers: list[str] = Field(default_factory=list)
    provider: str


class PersonaChatResponse(BaseModel):
    """Full persona chat response (HU-1406).

    Top-level ``response`` + ``trace`` contract (not enveloped in ``data``) so
    later fidelity benchmarks can consume the trace payload directly.
    """

    response: str
    trace: ChatTrace

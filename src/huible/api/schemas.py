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
    "AlignmentView",
    "ChatRequest",
    "ChatResponse",
    "ChatResponseData",
    "ChatTrace",
    "ConsentAcknowledgeData",
    "ConsentAcknowledgeRequest",
    "ConsentAcknowledgeResponse",
    "ConsentCardView",
    "DataEnvelope",
    "ExcludedMemoryRefView",
    "HandoffTicketView",
    "HealthCheck",
    "HealthResponse",
    "PersonaChatRequest",
    "PersonaChatResponse",
    "RelationshipTierLiteral",
    "SafetyEventView",
    "SessionMetaView",
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
    requester's disclosure tier (default ``family``). An optional
    ``conversation_id`` threads the in-process session log so the G7 dosage
    observability signal can be emitted on the trace.
    """

    message: str = Field(..., min_length=1, description="Inbound user message.")
    relationship: str | None = Field(
        default=None,
        description=(
            "Requester relationship to the persona: intimate | family | "
            "close_friend | acquaintance. Default: family."
        ),
    )
    conversation_id: str | None = Field(
        default=None,
        description="Opaque conversation id. Threads the in-process session log (G7).",
    )

    def requester_relationship(self) -> str:
        """Return the relationship tier, defaulting to ``family`` (spec)."""
        tier = (self.relationship or "family").strip().lower()
        if tier not in _PERSONA_RELATIONSHIPS:
            raise ValueError(
                f"relationship must be one of {sorted(_PERSONA_RELATIONSHIPS)}, got {tier!r}"
            )
        return tier


class ExcludedMemoryRefView(BaseModel):
    """A memory that was filtered out by the provenance firewall (G4 audit).

    Exposed so fidelity tests can assert exclusions in *both* directions: an
    admissible memory appears in ``memory_refs`` while a known-non-admissible
    memory appears here with its exclusion reason (HU-1407 §7.1 G4).
    """

    id: str
    reason: str


class SafetyEventView(BaseModel):
    """Recorded safety event for clinician/human review (G1).

    A non-null ``safety_event`` on the trace means the G1 crisis path fired:
    the user message carried a crisis signal, persona-voiced generation was
    skipped, and the warm non-persona escalation response was returned. This is
    a monitored safety event, not a normal turn.
    """

    kind: str = Field(
        description="Safety-event kind. Phase-1: 'crisis_escalation' (G1)."
    )
    signal: str = Field(description="Classifier signal category (e.g. 'clear').")
    affect: str = Field(description="Graded user affect (e.g. 'crisis').")
    matched: list[str] = Field(
        default_factory=list,
        description="Pattern snippets that fired (audit only; never shown to user).",
    )
    resources_shown: bool = Field(
        default=True,
        description="Whether crisis-line resources were surfaced in the response.",
    )


class SessionMetaView(BaseModel):
    """Per-session observability metadata (G7 — spec now, gate post-Phase-1).

    Emitted on every trace so the dosage/over-use signal (H5/PGD) exists when
    the dosage gate lands, with no re-instrumentation and no silent
    observability gap (HU-1407 §7.1 G7). Phase-1 enforces nothing on it.
    """

    turn_count: int = Field(default=1, ge=1)
    started_at: str | None = None
    duration_seconds: float = 0.0


class HandoffTicketView(BaseModel):
    """Human-handoff escalation record surfaced on the trace (§7.4.1).

    Non-null ``handoff`` on the trace means a G1 crisis turn was routed into the
    human-handoff queue (HU-1421). Every field the Clinical Advisor requires on
    the audit log (HU-1407 §10.1 invariant 5) is present here: trigger signal,
    risk flags, timestamp, SLA target, outcome, and (after a responder action) a
    free-text clinical-review note. This is a monitored safety artifact, not a
    normal chat field — it exists for clinical review and operational SLA
    monitoring.

    ``user_acknowledgement`` is the warm, non-persona text that was shown to the
    user on this turn (resources + "a person will join" only when a responder
    was actually paged).
    """

    ticket_id: str = Field(description="Unique escalation ticket id (audit key).")
    outcome: str = Field(
        description=(
            "Escalation outcome: 'enqueued' (responder paged), 'degraded' "
            "(no human available within SLA → G1 safe response), 'answered' or "
            "'abandoned' (set by a responder after the turn)."
        )
    )
    trigger_signal: str = Field(
        description="G1/G2-derived classifier signal that routed the turn (never persona-output)."
    )
    affect: str = Field(description="Graded user affect at the crisis turn (e.g. 'crisis').")
    risk_flags: list[str] = Field(
        default_factory=list,
        description="Intake risk flags present (G8 surface) — observability + routing context.",
    )
    matched_patterns: list[str] = Field(
        default_factory=list,
        description="Classifier pattern snippets that fired (audit only; never shown to user).",
    )
    sla_target_seconds: int = Field(
        description="Configured SLA target (seconds) for a responder to acknowledge."
    )
    created_at: str = Field(description="ISO-8601 UTC timestamp the ticket was opened.")
    responder_id: str | None = Field(
        default=None, description="Staffed responder id paged (null when degraded)."
    )
    degrade_reason: str | None = Field(
        default=None,
        description=(
            "Why the turn degraded instead of enqueuing (no_responder_available | queue_error:*). "
            "Audit/ops only; never shown to the user."
        ),
    )
    clinical_review_note: str | None = Field(
        default=None,
        description="Free-text clinical-review note recorded by a responder on resolve().",
    )
    resources_shown: bool = Field(
        default=True, description="Whether crisis-line resources were surfaced in the response."
    )
    user_acknowledgement: str = Field(
        default="",
        description="Warm non-persona acknowledgement text shown to the user this turn.",
    )


class AlignmentView(BaseModel):
    """Generation-time claim->ref alignment report surfaced on the trace (§7.4.2).

    Non-null on every persona-voiced turn (null on crisis turns, where no
    generation ran). The alignment filter extracts claims (identity,
    biographical, relationship, advice) from the persona reply, aligns each
    against the memories that passed the G4 firewall + the persona vault, and
    suppresses the turn safely when any un-grounded claim is present. This
    view is the telemetry surface the Clinical Advisor requires: aggregate
    ``ungrounded_claim_count`` / total ``claim_count`` for the un-grounded-claim
    rate, and ``disposition`` for the suppress-vs-pass distribution.
    """

    claim_count: int = Field(
        default=0,
        ge=0,
        description="Total claims extracted from the generated reply this turn.",
    )
    ungrounded_claim_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Claims with no supporting retrieved ref / vault token. The "
            "numerator of the un-grounded-claim rate."
        ),
    )
    disposition: str = Field(
        default="passed",
        description=(
            "'passed' when every claim was grounded (reply shown verbatim); "
            "'suppressed' when at least one un-grounded claim replaced the "
            "reply with the safe alignment fallback."
        ),
    )
    ungrounded_by_category: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Un-grounded claim counts keyed by category: identity | "
            "biographical | relationship | advice. Drives category-level "
            "clinical review."
        ),
    )


class ChatTrace(BaseModel):
    """Structured retrieval/generation trace for audit + future F-tests.
    passed the provenance firewall (HIGH/MEDIUM confidence, in-era, in-scope).
    LOW / QUARANTINE confidence memories are dropped by the context builder
    before generation and therefore never appear here; their ids and exclusion
    reasons surface in ``excluded_memory_refs`` (G4 both-directions).

    Runtime-clinical fields (HU-1413 / HU-1407 §7.3):

    * ``safety_event`` — non-null when the G1 crisis path fired this turn.
    * ``framing_version`` — the immutable reality-framing revision that held
      during generation (G2 immutability is unit-tested against this).
    * ``distress_grounding`` — True when the G3 dynamic distress branch ran.
    * ``session_meta`` — per-session dosage observability (G7).
    * ``risk_flags`` — reserved intake risk-flag surface (G8, observability only
      at Phase-1; enforcement is a gating clinical review item pre-real-users).
    * ``handoff`` — non-null when the turn was routed to the human-handoff queue
      (§7.4.1). Carries the full audit row (trigger signal, risk flags,
      timestamp, SLA target, outcome, clinical-review note).
    * ``alignment`` — non-null on every persona-voiced turn (§7.4.2). Carries
      the claim->ref alignment report: claim / un-grounded counts,
      per-category un-grounded counts, and the disposition applied this turn.
      Drives the un-grounded-claim rate + disposition telemetry the Clinical
      Advisor requires. ``None`` on crisis turns (no generation ran).
    """

    memory_refs: list[str] = Field(default_factory=list)
    provenance_tiers: list[str] = Field(default_factory=list)
    excluded_memory_refs: list[ExcludedMemoryRefView] = Field(default_factory=list)
    provider: str
    safety_event: SafetyEventView | None = None
    framing_version: int = 0
    distress_grounding: bool = False
    session_meta: SessionMetaView = Field(default_factory=SessionMetaView)
    risk_flags: list[str] = Field(
        default_factory=list,
        description=(
            "Reserved intake risk-flag surface (G8): loss_of_child, "
            "minor_decedent, recent_loss, non_acceptance, proxy_user. "
            "Observability-only at Phase-1."
        ),
    )
    handoff: HandoffTicketView | None = Field(
        default=None,
        description=(
            "Human-handoff escalation record (§7.4.1). Non-null when the G1 "
            "crisis turn was routed to the staffed-responder queue. Carries the "
            "full audit row for clinical review."
        ),
    )
    alignment: AlignmentView | None = Field(
        default=None,
        description=(
            "Generation-time claim->ref alignment report (§7.4.2). Non-null on "
            "every persona-voiced turn; null on crisis turns. Exposes the "
            "un-grounded-claim rate and disposition for clinical review."
        ),
    )


class PersonaChatResponse(BaseModel):
    """Full persona chat response (HU-1406).

    Top-level ``response`` + ``trace`` contract (not enveloped in ``data``) so
    later fidelity benchmarks can consume the trace payload directly.
    """

    response: str
    trace: ChatTrace


# --- G6 entry-framing / consent card (HU-1423, §7.4.3) ---------------------


class ConsentCardView(BaseModel):
    """Reality-framing / consent card content surfaced to the client (§7.4.3 G6).

    The card is an onboarding/system message — it is **never** voiced by the
    deceased persona and never passed through the generator. When the chat path
    refuses a turn for lack of consent (HTTP 409 ``CONSENT_REQUIRED``), the card
    is included inline so the client can render it and then call the acknowledge
    endpoint. The Onboarding Agent owns the clinically-reviewed wording; this
    view is the wire shape the card provider fills in.
    """

    version: int = Field(
        description="Monotonically-increasing card revision (drift / audit pin)."
    )
    title: str = Field(description="Short card heading shown to the user.")
    body: str = Field(description="Reality-framing + consent copy shown to the user.")
    acknowledge_instructions: str = Field(
        description="How the client records the acknowledgment (the consent endpoint path)."
    )


class ConsentAcknowledgeRequest(BaseModel):
    """Body of ``POST /api/v1/chat/{persona_id}/consent`` (§7.4.3 G6).

    Records that the user acknowledged the reality-framing / consent card for
    this session. ``conversation_id`` is the session key the consent binds to
    (the same id threaded through ``POST /chat/{persona_id}``).
    """

    conversation_id: str = Field(
        ...,
        min_length=1,
        description="Session id the acknowledgment binds to (same as chat conversation_id).",
    )
    card_version: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Card revision the user acknowledged. Defaults to the provider's "
            "current revision when omitted."
        ),
    )


class ConsentAcknowledgeData(BaseModel):
    """The ``data`` payload of a successful acknowledge response."""

    acknowledged: bool = Field(default=True)
    conversation_id: str
    persona_id: UUID
    card_version: int
    acknowledged_at: str = Field(description="ISO-8601 UTC timestamp.")
    acknowledgment_id: str = Field(description="Audit key for the recorded consent.")


class ConsentAcknowledgeResponse(BaseModel):
    """Full acknowledge response envelope."""

    data: ConsentAcknowledgeData

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

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

__all__ = [
    "ActivatedMemoryView",
    "AlignmentView",
    "ByokKeyPutRequest",
    "ByokKeyView",
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
    "HandoffQueueItemView",
    "HandoffResolveRequest",
    "HandoffSLAStatusView",
    "HandoffTelemetryView",
    "HandoffTicketView",
    "HealthCheck",
    "HealthResponse",
    "PersonaChatRequest",
    "PersonaChatResponse",
    "RelationshipTierLiteral",
    "RiskEnforcementView",
    "SafetyEventView",
    "SessionMetaView",
    "UsageDailyRowView",
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
        default=None, description="Opaque conversation id. Echoed back; new id minted when absent."
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

    kind: str = Field(description="Safety-event kind. Phase-1: 'crisis_escalation' (G1).")
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


class HandoffSLAStatusView(BaseModel):
    """Live SLA status for one handoff ticket (§7.4 ops gate / HU-1428 AC #4).

    The breach-alert signal for the staffed-responder work queue: a pending row
    past its SLA means a grieving user is waiting beyond target and the on-call
    responder must be paged.
    """

    breached: bool = Field(description="True when the ticket is past its SLA target now.")
    seconds_since_created: int = Field(
        ge=0, description="Seconds elapsed since the ticket was opened."
    )
    seconds_to_sla: int = Field(
        description=(
            "Countdown to the SLA boundary in seconds. Positive while within "
            "target, zero at the boundary, negative once breached."
        )
    )
    seconds_overdue: int = Field(
        ge=0, description="Overdue magnitude in seconds (0 when within SLA)."
    )


class HandoffQueueItemView(BaseModel):
    """A staffed-responder work-queue row (the ``GET /handoff/tickets`` item).

    The full audit row plus the live SLA status, so a responder opening the
    queue sees both who/why (trigger signal, risk flags, affect) and how urgent
    (breach countdown). Used for pending tickets; the audit endpoint reuses it
    without the SLA status for historical rows.
    """

    id: str = Field(
        description=(
            "Unique escalation ticket id (alias of ticket_id; carried on every "
            "audit row so §10.1 consumers reading `id` see the audit key, "
            "HU-1926 finding 2)."
        )
    )
    ticket_id: str = Field(description="Unique escalation ticket id (audit key).")
    outcome: str = Field(
        description="Escalation outcome: enqueued | degraded | answered | abandoned."
    )
    trigger_signal: str = Field(
        description="Classifier signal that routed the turn (risk:* prefix = §7.4.4 risk-driven)."
    )
    affect: str = Field(description="Graded user affect at the escalation turn.")
    persona_id: str = Field(description="Persona the escalation occurred on.")
    conversation_id: str | None = Field(
        default=None, description="Session the escalation occurred in, if any."
    )
    risk_flags: list[str] = Field(default_factory=list, description="Intake risk flags present.")
    matched_patterns: list[str] = Field(
        default_factory=list, description="Classifier pattern snippets that fired (audit only)."
    )
    sla_target_seconds: int = Field(
        description="Configured SLA target (seconds) for acknowledgement."
    )
    created_at: str = Field(description="ISO-8601 UTC timestamp the ticket was opened.")
    resolved_at: str | None = Field(
        default=None, description="ISO-8601 UTC timestamp the ticket was finalized, if any."
    )
    responder_id: str | None = Field(
        default=None, description="Staffed responder id paged / claiming the ticket."
    )
    degrade_reason: str | None = Field(
        default=None, description="Why the turn degraded (no_responder_available | queue_error:*)."
    )
    clinical_review_note: str | None = Field(
        default=None, description="Free-text clinical-review note recorded on resolve()."
    )
    sla_status: HandoffSLAStatusView | None = Field(
        default=None,
        description=(
            "Live SLA status. Populated for pending (enqueued) rows; null for "
            "historical/resolved rows."
        ),
    )


class HandoffTelemetryView(BaseModel):
    """Aggregate SLA + outcome telemetry over the handoff audit log (HU-1428 AC #4).

    The dashboard surface the Clinical Advisor signs off against before lifting
    the real-user hold: degrade rate must trend to ~0 once staffed, and the
    answered-within-SLA rate must clear the agreed threshold.
    """

    total: int = Field(ge=0, description="Total tickets ever created.")
    by_outcome: dict[str, int] = Field(
        default_factory=dict, description="Ticket counts keyed by outcome value."
    )
    pending: int = Field(ge=0, description="Open (enqueued) tickets now.")
    answered: int = Field(ge=0, description="Tickets finalized as answered.")
    degraded: int = Field(ge=0, description="Tickets that degraded (fail-safe fired).")
    abandoned: int = Field(ge=0, description="Tickets finalized as abandoned.")
    pending_breached: int = Field(
        ge=0, description="Open tickets past SLA now — the live alert count."
    )
    answered_breached_sla: int = Field(
        ge=0, description="Resolved tickets whose wait exceeded SLA — historical miss count."
    )
    degrade_rate: float = Field(
        ge=0.0, le=1.0, description="Degraded / total — the fail-safe firing share."
    )
    pending_breach_rate: float = Field(
        ge=0.0, le=1.0, description="pending_breached / pending — live breach pressure."
    )
    answered_breach_rate: float = Field(
        ge=0.0, le=1.0, description="answered_breached_sla / answered — responder miss rate."
    )


class HandoffResolveRequest(BaseModel):
    """Body of ``POST /api/v1/handoff/tickets/{ticket_id}/resolve`` (responder action)."""

    outcome: str = Field(description="Finalization outcome: 'answered' or 'abandoned'.")
    responder_id: str | None = Field(
        default=None,
        description="Staffed responder id claiming/finalizing the ticket.",
    )
    clinical_review_note: str | None = Field(
        default=None,
        description="Free-text clinical-review note recorded on the ticket.",
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
    judge_adjudication: str | None = Field(
        default=None,
        description=(
            "HU-2161 LLM-judge backstop outcome on a turn the content-overlap "
            "filter flagged: 'supported' (judge cleared the flagged "
            "biographical/relationship claims; original reply restored), "
            "'fabricated' (judge confirmed confabulation; suppression stands "
            "and is Sev-1 page-worthy), 'unavailable' (no real judge ran; "
            "suppression stands unconfirmed — never paged). Null when no "
            "adjudication ran (turn passed the filter outright)."
        ),
    )


class CapabilityGuardView(BaseModel):
    """Post-generation capability-leak guard report on the trace (HU-2675).

    Non-null on every persona-voiced turn where the W3 competence wall fired
    (``competence_wall`` true); null elsewhere — the guard never runs on an
    in-domain turn. The guard detects base-model assistant-register output
    (code fluency, teaching register, capability boasts, bare encyclopedia
    answers untraceable to persona memory) and replaces it with an in-voice
    deflection fallback. This view feeds the W6 micro-tell baseline: a
    ``replaced`` disposition on a wall-fired turn is the recorded footprint
    of a generator capability leak that was stopped before it reached the
    user.
    """

    fired: bool = Field(
        default=False,
        description="True when a concrete capability-leak marker fired this turn.",
    )
    disposition: str = Field(
        default="passed",
        description=(
            "'passed' when the reply showed verbatim; 'replaced' when a leak "
            "marker fired and the reply was swapped for the in-voice "
            "deflection fallback."
        ),
    )
    markers: list[str] = Field(
        default_factory=list,
        description=(
            "Concrete marker names that fired: assistant-register classes "
            "(code_block, code_fluency, teaching_register, capability_boast, "
            "assistant_register) or 'bare_answer' (short out-of-corpus "
            "factual answer). Empty when clean."
        ),
    )


class RiskEnforcementView(BaseModel):
    """G8 risk-flag enforcement report surfaced on the trace (§7.4.4).

    Non-null on every persona-chat turn AND every risk-flag short-circuit
    (``refuse_topic`` / ``handoff`` / ``pause_session``). Carries the binding
    action the chat path took, the full required-actions set (so clinical
    review sees the union of effects, not just the binding one), the flags
    that fired, and the session-signal contributions — so the per-flag fire
    count + per-action distribution the Clinical Advisor requires (matrix §5)
    are read directly off the trace.

    ``pre_empted_by_crisis`` is recorded when a G1 crisis signal overrode the
    report (matrix §4) — in that case ``action`` is ``continue`` and no flag
    enforcement applied (the G1 path took over). This lets clinical review
    distinguish "no flags fired" from "G1 pre-empted flag enforcement" in the
    telemetry.
    """

    action: str = Field(
        description=(
            "Binding (most-restrictive) enforcement action taken this turn: "
            "continue | tighten | reframe | refuse_topic | handoff | pause_session."
        )
    )
    required_actions: list[str] = Field(
        default_factory=list,
        description=(
            "Full union of enforcement effects for the turn (may include "
            "tighten + reframe additively even when the binding action is "
            "refuse_topic, etc.). Drives the per-action distribution telemetry."
        ),
    )
    fired_flags: list[str] = Field(
        default_factory=list,
        description=(
            "Intake risk flags that fired this turn (loss_of_child | "
            "minor_decedent | recent_loss | non_acceptance | proxy_user). "
            "Drives the per-flag fire-count telemetry."
        ),
    )
    session_signal_actions: list[str] = Field(
        default_factory=list,
        description=(
            "Enforcement actions contributed by session-level signals "
            "(dosage-cap pause_session, distress-trend handoff/tighten, "
            "crisis-history tighten). Empty when no session signal fired."
        ),
    )
    pre_empted_by_crisis: bool = Field(
        default=False,
        description=(
            "True when a G1 crisis signal overrode flag enforcement this "
            "turn (matrix §4). Distinguishes 'no flags' from 'G1 pre-empted'."
        ),
    )


class WorkingMemoryView(BaseModel):
    """W4 working-memory observability (HU-2309 v1.8 §1.7.2 / M-0R-B).

    Evidence-only surface for the TencentDB Arm A lane this turn: what the
    recall returned (strategy + payload size) and whether the completed turn
    was captured back to the store. ``None`` when the lane is disabled.
    """

    strategy: str = Field(
        default="",
        description="Gateway read path used (v4-arm-a, or a v3 fallback label).",
    )
    chars: int = Field(
        default=0,
        description="Size of the working-memory block rendered into the prompt.",
    )
    synced: bool = Field(
        default=False,
        description="Whether the completed turn was captured to the store.",
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
    * ``risk_enforcement`` — non-null on every turn where G8 risk-flag /
      session-meta enforcement was evaluated (§7.4.4). Carries the binding
      action the chat path took (continue / tighten / reframe / refuse_topic
      / handoff / pause_session), the full required-actions set, the flags
      that fired, and the session-signal contributions. Drives the per-flag
      fire-count + per-action distribution telemetry the Clinical Advisor
      requires. ``None`` on the G1 crisis path (G1 pre-empts flag enforcement
      per matrix §4; the safety_event on that trace is the audit surface).
    """

    memory_refs: list[str] = Field(default_factory=list)
    provenance_tiers: list[str] = Field(default_factory=list)
    excluded_memory_refs: list[ExcludedMemoryRefView] = Field(default_factory=list)
    # HU-1926 chat-surface consolidation: the persona-scoped path is the single
    # chat surface, so the trace carries the full grounding views (contents +
    # confidence + disclosure scope) and exclusion counts that the retired
    # generic /api/v1/chat envelope used to expose — plus the session id the
    # turn bound to (echo surface for clients that thread conversation_id).
    activated_memories: list[ActivatedMemoryView] = Field(default_factory=list)
    exclusion_counts: dict[str, int] = Field(default_factory=dict)
    # W3 competence wall (HU-2309 v1.8 §1.7.2): True when the turn was
    # out-of-domain (no admissible memory above the activation floor) and was
    # served deflection-pattern exemplars instead of free-styling on
    # base-model skills. E0-replay OOD evidence reads this flag.
    competence_wall: bool = Field(
        default=False,
        description="Deflection-exemplar wall fired on an out-of-domain turn.",
    )
    working_memory: WorkingMemoryView | None = Field(
        default=None,
        description=(
            "W4 TencentDB working-memory lane observability (M-0R-B). Null "
            "when the lane is disabled; populated on persona-voiced turns "
            "when armed."
        ),
    )
    conversation_id: str | None = Field(
        default=None,
        description="Session id the turn bound to (echoed; minted when absent).",
    )
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
    risk_enforcement: RiskEnforcementView | None = Field(
        default=None,
        description=(
            "G8 risk-flag enforcement report (§7.4.4). Non-null on every turn "
            "where risk-flag / session-meta enforcement was evaluated; null on "
            "the G1 crisis path (G1 pre-empts flag enforcement). Exposes the "
            "binding action, required-actions set, fired flags, and session-"
            "signal contributions for the per-flag fire-count + per-action "
            "distribution telemetry."
        ),
    )
    capability_guard: CapabilityGuardView | None = Field(
        default=None,
        description=(
            "Post-generation capability-leak guard report (HU-2675). Non-null "
            "when the W3 competence wall fired this turn; null on in-domain "
            "turns (the guard never runs there). Carries the disposition and "
            "the concrete leak markers that fired, feeding the W6 micro-tell "
            "baseline."
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

    version: int = Field(description="Monotonically-increasing card revision (drift / audit pin).")
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


# --- Stage 0.5 risk-profile intake (HU-1448, §7.4.4) ------------------------


class RiskIntakeAssessmentRequest(BaseModel):
    """Body of ``POST /api/v1/admin/risk-intake`` — the canary-cohort intake form.

    Captures only the user-gathered assessment booleans the G8 enforcement
    matrix acts on (matrix §2). The objective persona-record flags
    (``minor_decedent`` / ``recent_loss``) are derived server-side from the
    registered persona config and are not part of this request. No clinical-
    diagnosis fields (Stage 2+ owns the full assessment instrument).
    """

    conversation_id: str = Field(
        ...,
        min_length=1,
        description=(
            "Session id the intake binds to (same as chat conversation_id). "
            "Must have a recorded G6 consent acknowledgment for (conversation_id, "
            "persona_id) before this call — intake does not bypass consent."
        ),
    )
    persona_id: UUID = Field(
        ...,
        description="The canary persona the intake is being recorded against.",
    )
    loss_of_child: bool = Field(
        default=False,
        description="The deceased is the user's child (matrix §2 loss_of_child).",
    )
    non_acceptance: bool = Field(
        default=False,
        description=(
            "The reality-framing has not landed; the user is asserting literal "
            "presence / reunion (matrix §2 non_acceptance)."
        ),
    )
    proxy_user: bool = Field(
        default=False,
        description=(
            "Identity verification failed for this session; the person at the "
            "keyboard is not the intended requester (matrix §2 proxy_user, "
            "intrinsically per-session)."
        ),
    )


class RiskIntakeData(BaseModel):
    """The ``data`` payload of a successful intake response — audit view."""

    persona_id: UUID
    conversation_id: str = Field(description="Session id the intake was recorded against.")
    consent_acknowledgment_id: str | None = Field(
        default=None,
        description="Audit key of the G6 consent record that authorized this intake.",
    )
    persona_flags: list[str] = Field(
        default_factory=list,
        description=(
            "Intake-derived flags written at persona scope (apply to every "
            "session for this persona). Includes objective derivation "
            "(minor_decedent / recent_loss) + assessment (loss_of_child / "
            "non_acceptance)."
        ),
    )
    session_flags: list[str] = Field(
        default_factory=list,
        description=(
            "Intake-derived flags written at session scope (proxy_user). "
            "Cleared/refreshed per intake for this (session, persona)."
        ),
    )
    derived_flags: list[str] = Field(
        default_factory=list,
        description=(
            "Subset of persona_flags derived objectively from the persona "
            "record (minor_decedent / recent_loss). Consent-independent."
        ),
    )
    assessed_flags: list[str] = Field(
        default_factory=list,
        description=(
            "Subset of flags gathered from the user assessment "
            "(loss_of_child / non_acceptance / proxy_user). Consent-gated."
        ),
    )
    all_flags: list[str] = Field(
        default_factory=list,
        description=(
            "Sorted union of every flag written this intake (both scopes). "
            "This is what G8 enforcement will read on the next chat turn."
        ),
    )


class RiskIntakeResponse(BaseModel):
    """Full intake response envelope."""

    data: RiskIntakeData


# --- Usage metering (HU-2243 Sprint 1) ---------------------------------------


class UsageDailyRowView(BaseModel):
    """One (day, api_key_id, persona_id) daily aggregate of LLM usage.

    The metering read surface (``GET /api/v1/usage/daily``): requests,
    tokens in/out, modeled cost at reference rates, average LLM latency,
    and distinct-conversation count — the minimum aggregate that feeds
    valuation data, plan pricing, and B2B API billing (founder four-reasons).
    ``api_key_id`` is a SHA-256 digest of the caller's bearer key, never
    the raw key.
    """

    day: date = Field(..., description="UTC calendar day of the aggregate.")
    org_id: str | None = Field(
        default=None,
        description="Tenant org attribution (NULL until keys gain org bindings).",
    )
    api_key_id: str = Field(..., description="SHA-256 digest prefix of the caller API key.")
    persona_id: str = Field(..., description="Persona the metered turns spoke as.")
    requests: int = Field(..., description="Metered LLM requests (rows) in the group.")
    tokens_in: int = Field(..., description="Prompt tokens summed.")
    tokens_out: int = Field(..., description="Completion tokens summed.")
    modeled_cost_usd: float = Field(
        ...,
        description="Modeled cost at reference rates (or provider-reported when available).",
    )
    avg_latency_ms: float = Field(..., description="Mean LLM latency in milliseconds.")
    conversations: int = Field(..., description="Distinct conversation ids in the group.")
    key_source: str = Field(
        default="shared",
        description=(
            "Which provider key served the group's turns (HU-2243 key "
            "separation / BYOK): 'byok' client-supplied key, 'product' "
            "dedicated product key, 'shared' internals key."
        ),
    )


# --- BYOK vault management (HU-2243 Sprint 3) -----------------------------------


class ByokKeyPutRequest(BaseModel):
    """Register/replace the caller's provider key in the BYOK vault.

    The raw key is sealed (AES-256-GCM under ``BYOK_VAULT_MASTER_KEY``)
    before storage; no endpoint ever returns it — only the fingerprint.
    """

    provider_key: str = Field(
        ...,
        min_length=8,
        max_length=4096,
        description="The client's own provider API key (sealed at rest).",
    )


class ByokKeyView(BaseModel):
    """One registered BYOK key (fingerprint only — never the raw key)."""

    provider: str = Field(..., description="Provider the key is registered for.")
    key_fingerprint: str = Field(
        ...,
        description="SHA-256/16 fingerprint of the sealed key (non-secret).",
    )
    updated_at: datetime = Field(..., description="When the key was last replaced.")

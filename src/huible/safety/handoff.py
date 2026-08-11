"""Human-handoff (crisis escalation) queue — G1 handoff path (§7.4.1).

Clinical source: the Clinical Advisor's ``clinical-guardrails`` spec §7.4 #1
(advisory issue HU-1407) and the fail-safe invariants recorded in §10.1 of the
same document. This is a **hard pre-real-user clinical gate** on real
persona-chat traffic — it is not Phase-1 runtime-gate polish.

The Phase-1 build ([HU-1413]) ships the in-band crisis-line display via
:func:`huible.safety.crisis.build_crisis_response`. This module covers the
**escalation-to-a-real-human** path that must exist before any grieving,
real-user traffic flows over ``POST /chat/{persona_id}`` ([HU-1406]).

§10.1 clinical fail-safe invariants (every one is unit-tested):

1. **Reachable human, not a print.** "Crisis-line display + a queue row no one
   reads" is the failure mode §7.4 #1 exists to prevent. The SLA is defined and
   surfaced on every ticket; the named surface / staffing model is the scoping
   note's job to record (AC #1). If no staffing model exists yet, the queue's
   ``available_responders`` defaults to **0**, which forces every escalation to
   ``DEGRADED`` — the clinically correct fail-safe, never a silent drop.
2. **Fail-safe default = degrade, never drop.** If no human is available within
   SLA, the turn degrades to the G1 non-persona safe response with crisis
   resources, **never** proceeds to persona voice. Queue failure (raise) also
   degrades; the persona path is unreachable from a crisis turn.
3. **Routing trigger is G1/G2-derived, not persona-output.** Handoff is
   triggered by the :class:`~huible.safety.crisis.CrisisClassifier` signal, never
   by the persona's own generated text. :func:`escalate_to_human` takes the
   :class:`~huible.safety.crisis.CrisisResult` as its routing input.
4. **In-session UX is non-persona while waiting.** The persona does not "keep
   the user company" during escalation. The waiting acknowledgement is warm,
   non-persona, resources-visible, and on ``ENQUEUED`` says explicitly that a
   person will join / who to reach now. On ``DEGRADED`` it never claims a person
   is joining.
5. **Audit every escalation** with: trigger signal, risk flags present,
   timestamp, SLA target, outcome (``enqueued`` / ``degraded`` / ``answered`` /
   ``abandoned``), and a free-text clinical-review field. Every ticket carries
   these fields; the audit log is the queue itself (``audit_log()``).

Like :mod:`huible.safety.crisis`, the queue backend is a :class:`Protocol`
(:class:`HandoffQueue`) with a deterministic in-memory default
(:class:`InMemoryHandoffQueue`) so the pre-real-user suite runs key-free. A real
backend (Postgres table / Redis stream / external ticketing such as PagerDuty)
drops in pre-real-launch without touching callers.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from huible.safety.crisis import (
    CrisisResult,
    build_crisis_response,
)

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_HANDOFF_SLA_SECONDS",
    "HandoffOutcome",
    "HandoffQueue",
    "HandoffResult",
    "HandoffTicket",
    "InMemoryHandoffQueue",
    "build_handoff_acknowledgement",
    "escalate_to_human",
]

#: Default SLA target for a human to acknowledge an escalation (5 minutes).
#: Surfaced on every ticket and monitored; the scoping note (AC #1) records the
#: operational reality (who staffs it, hours). Pre-real-users this is a target,
#: not a guarantee — and the fail-safe degrades when it cannot be met.
DEFAULT_HANDOFF_SLA_SECONDS: int = 300


class HandoffOutcome(StrEnum):
    """Outcome of an escalation ticket.

    * :attr:`ENQUEUED` — a ticket was created and a responder was paged. The
      user-facing acknowledgement tells the user a person will join.
    * :attr:`DEGRADED` — no responder was available within SLA (or the queue
      failed). The turn degrades to the G1 non-persona safe response; the
      persona path is never taken. The ticket is still recorded for audit.
    * :attr:`ANSWERED` — a clinician/responder marked an enqueued ticket as
      resolved (set via :meth:`HandoffQueue.resolve`).
    * :attr:`ABANDONED` — the user left / the session ended before a responder
      acknowledged (set via :meth:`HandoffQueue.resolve`).
    """

    ENQUEUED = "enqueued"
    DEGRADED = "degraded"
    ANSWERED = "answered"
    ABANDONED = "abandoned"


def _now_iso() -> str:
    """Return the current UTC timestamp as an ISO-8601 string."""
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class HandoffTicket:
    """A single human-handoff escalation record (the audit row).

    Carries every field the Clinical Advisor requires on the audit log (§10.1
    invariant 5): trigger signal, risk flags present, timestamp, SLA target,
    outcome, and a free-text clinical-review field. Tickets are created by
    :meth:`HandoffQueue.enqueue` and finalized by
    :meth:`HandoffQueue.resolve`.
    """

    id: str
    persona_id: str
    conversation_id: str | None
    trigger_signal: str
    affect: str
    matched_patterns: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    sla_target_seconds: int = DEFAULT_HANDOFF_SLA_SECONDS
    created_at: str = field(default_factory=_now_iso)
    outcome: HandoffOutcome = HandoffOutcome.ENQUEUED
    responder_id: str | None = None
    clinical_review_note: str | None = None
    #: Free-text reason recorded when the queue degraded instead of enqueuing
    #: (no responder available, queue error). Never shown to the user; surfaced
    #: on the audit row for clinical/ops review.
    degrade_reason: str | None = None


@runtime_checkable
class HandoffQueue(Protocol):
    """Pluggable backend for human-handoff escalation tickets.

    The default :class:`InMemoryHandoffQueue` is deterministic and key-free so
    the pre-real-user suite runs in CI. A real backend (Postgres / Redis /
    PagerDuty-style external paging) drops in here pre-real-launch without
    touching the chat endpoint.
    """

    def enqueue(self, ticket: HandoffTicket) -> HandoffTicket:
        """Assign the ticket a final ``outcome`` (and a responder when enqueued).

        Implementations decide whether a responder is available within SLA:

        * available → return the ticket with ``outcome = ENQUEUED`` and a
          populated ``responder_id``;
        * unavailable → return with ``outcome = DEGRADED`` and a
          ``degrade_reason`` (the chat endpoint then degrades to the G1 safe
          response — never the persona voice).

        The ticket is always persisted to the audit log regardless of outcome.
        """
        ...

    def get(self, ticket_id: str) -> HandoffTicket | None: ...

    def list_pending(self) -> list[HandoffTicket]:
        """Open tickets (``ENQUEUED`` only) — the staffed-responder work queue."""
        ...

    def audit_log(self) -> list[HandoffTicket]:
        """Every ticket ever created, in insertion order (the clinical audit log)."""
        ...

    def resolve(
        self,
        ticket_id: str,
        *,
        outcome: HandoffOutcome,
        responder_id: str | None = None,
        clinical_review_note: str | None = None,
    ) -> HandoffTicket | None:
        """Finalize an open ticket (clinician/responder action).

        Sets ``outcome`` to :attr:`HandoffOutcome.ANSWERED` or
        :attr:`HandoffOutcome.ABANDONED` and records the free-text
        ``clinical_review_note``. Returns the updated ticket, or ``None`` if no
        ticket matches.
        """
        ...


class InMemoryHandoffQueue:
    """Deterministic in-memory handoff queue (pre-real-users default).

    Staffing model: ``available_responders`` is the count of simultaneous
    responders on call. When ``0`` (the fail-safe default) every escalation
    degrades to the G1 non-persona safe response — the clinically correct
    posture when no staffing roster exists yet. A staffed roster raises this
    count; the named surface / hours / SLA monitoring is recorded in the
    scoping note (AC #1) and lands as a pre-real-launch ops dependency.

    Responder ids are pulled round-robin from ``responder_id_pool``. When the
    pool is empty but ``available_responders > 0``, a synthetic responder id is
    minted so the "a person will join" acknowledgement is still truthful.

    This backend is intentionally synchronous and side-effect-light so the
    chat endpoint's G1 path stays synchronous and pre-generation (§7.1 G1).
    """

    def __init__(
        self,
        *,
        available_responders: int = 0,
        responder_id_pool: tuple[str, ...] = (),
        sla_target_seconds: int = DEFAULT_HANDOFF_SLA_SECONDS,
    ) -> None:
        if available_responders < 0:
            raise ValueError("available_responders must be >= 0")
        self._available_responders = available_responders
        self._responder_id_pool = tuple(responder_id_pool)
        self._sla_target_seconds = sla_target_seconds
        self._tickets: dict[str, HandoffTicket] = {}
        self._order: list[str] = []
        self._robin = 0

    @property
    def available_responders(self) -> int:
        return self._available_responders

    @property
    def sla_target_seconds(self) -> int:
        return self._sla_target_seconds

    def enqueue(self, ticket: HandoffTicket) -> HandoffTicket:
        # Always stamp the configured SLA on the audit row (monitored target).
        ticket.sla_target_seconds = self._sla_target_seconds
        if self._available_responders > 0:
            ticket.outcome = HandoffOutcome.ENQUEUED
            ticket.responder_id = self._next_responder_id()
            ticket.degrade_reason = None
        else:
            # Fail-safe: no responder available within SLA → degrade. The chat
            # endpoint will return the G1 non-persona safe response; the persona
            # path is never taken. The ticket is still recorded for audit.
            ticket.outcome = HandoffOutcome.DEGRADED
            ticket.responder_id = None
            ticket.degrade_reason = "no_responder_available"
        self._tickets[ticket.id] = ticket
        self._order.append(ticket.id)
        logger.info(
            "handoff.enqueue ticket=%s outcome=%s signal=%s responder=%s",
            ticket.id,
            ticket.outcome.value,
            ticket.trigger_signal,
            ticket.responder_id,
        )
        return ticket

    def get(self, ticket_id: str) -> HandoffTicket | None:
        return self._tickets.get(ticket_id)

    def list_pending(self) -> list[HandoffTicket]:
        return [
            self._tickets[i]
            for i in self._order
            if self._tickets[i].outcome is HandoffOutcome.ENQUEUED
        ]

    def audit_log(self) -> list[HandoffTicket]:
        return [self._tickets[i] for i in self._order]

    def resolve(
        self,
        ticket_id: str,
        *,
        outcome: HandoffOutcome,
        responder_id: str | None = None,
        clinical_review_note: str | None = None,
    ) -> HandoffTicket | None:
        ticket = self._tickets.get(ticket_id)
        if ticket is None:
            return None
        if outcome not in (HandoffOutcome.ANSWERED, HandoffOutcome.ABANDONED):
            raise ValueError(
                f"resolve() outcome must be answered or abandoned, got {outcome!r}"
            )
        ticket.outcome = outcome
        if responder_id is not None:
            ticket.responder_id = responder_id
        ticket.clinical_review_note = clinical_review_note
        return ticket

    def _next_responder_id(self) -> str:
        if self._responder_id_pool:
            rid = self._responder_id_pool[self._robin % len(self._responder_id_pool)]
            self._robin += 1
            return rid
        self._robin += 1
        return f"on-call-{self._robin}"


@dataclass(slots=True, frozen=True)
class HandoffResult:
    """Outcome of :func:`escalate_to_human` for one crisis turn.

    ``response_text`` is the full user-facing, non-persona text (the G1 crisis
    resources, plus — only when a responder was paged — a warm "a person will
    join" acknowledgement). ``ticket`` is the audit row. ``degraded`` mirrors
    ``ticket.outcome is DEGRADED`` for call-site convenience.
    """

    response_text: str
    ticket: HandoffTicket
    degraded: bool


def build_handoff_acknowledgement(
    *,
    outcome: HandoffOutcome,
    sla_target_seconds: int,
    responder_id: str | None = None,
) -> str:
    """Return the warm, non-persona handoff acknowledgement text (§10.1 #4).

    ``ENQUEUED`` → explicit "a person will join / here is who is reaching out"
    with the SLA target so the user knows the wait shape. ``DEGRADED`` → empty
    string: the chat endpoint has *already* returned the G1 crisis resources
    via :func:`build_crisis_response`, and the fail-safe contract is to **never**
    claim a person is joining when none was paged.

    The text is deliberately not voiced by the deceased persona (§7.1 H1).
    """
    if outcome is HandoffOutcome.ENQUEUED:
        who = f"({responder_id})" if responder_id else ""
        minutes = max(1, round(sla_target_seconds / 60))
        return (
            "\n\nI've also asked a person to reach out to you right now. "
            f"Someone should join you shortly — our target is within {minutes} minute"
            f"{'s' if minutes != 1 else ''}. "
            "You are not alone in this; please stay with us."
            + (f" {who}" if who else "")
        )
    return ""


def escalate_to_human(
    message: str,
    *,
    crisis_result: CrisisResult,
    queue: HandoffQueue,
    persona_id: str,
    conversation_id: str | None,
    risk_flags: list[str] | None = None,
    resources: dict[str, str] | None = None,
    sla_target_seconds: int = DEFAULT_HANDOFF_SLA_SECONDS,
) -> HandoffResult:
    """Route a G1-flagged turn into the human-handoff queue (§7.4.1).

    This is the intake path: a persona-chat turn flagged acute-distress (per the
    G1 crisis classifier) is turned into an audited escalation ticket and a
    user-facing, non-persona response. It is called **only** from the G1 branch
    of the chat endpoint — after the synchronous crisis classifier has fired and
    before any persona-voiced generation. The persona path is unreachable from
    here by construction (callers return ``response_text`` directly).

    Fail-safe contract (§10.1 #2): if the queue raises, the escalation
    **degrades** to the G1 non-persona safe response with crisis resources — it
    never propagates the error to the caller in a way that would let the request
    fall through to the persona voice. The degraded ticket is still recorded.

    The returned ``response_text`` is always the G1 crisis resources; on
    ``ENQUEUED`` it additionally carries the warm "a person will join"
    acknowledgement. On ``DEGRADED`` it carries only the resources (no false
    claim of a person joining).
    """
    base_response = build_crisis_response(resources=resources)

    ticket = HandoffTicket(
        id=f"hh-{uuid.uuid4().hex[:16]}",
        persona_id=persona_id,
        conversation_id=conversation_id,
        trigger_signal=crisis_result.signal.value,
        affect=crisis_result.affect.value,
        matched_patterns=list(crisis_result.matched),
        risk_flags=list(risk_flags or []),
        sla_target_seconds=sla_target_seconds,
    )

    try:
        finalized = queue.enqueue(ticket)
    except Exception as exc:  # pragma: no cover - defensive, backend-specific
        # Fail-safe: a broken queue degrades to G1, never drops and never
        # proceeds to persona voice. Record the ticket as degraded for audit.
        logger.exception("handoff queue raised; degrading to G1 safe response")
        ticket.outcome = HandoffOutcome.DEGRADED
        ticket.responder_id = None
        ticket.degrade_reason = f"queue_error:{type(exc).__name__}"
        finalized = ticket

    acknowledgement = build_handoff_acknowledgement(
        outcome=finalized.outcome,
        sla_target_seconds=finalized.sla_target_seconds,
        responder_id=finalized.responder_id,
    )

    return HandoffResult(
        response_text=base_response + acknowledgement,
        ticket=finalized,
        degraded=finalized.outcome is HandoffOutcome.DEGRADED,
    )

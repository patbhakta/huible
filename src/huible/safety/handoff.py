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
from zoneinfo import ZoneInfo

from huible.safety.crisis import (
    CrisisResult,
    build_crisis_response,
)

logger = logging.getLogger(__name__)

__all__ = [
    "COVERAGE_ALWAYS",
    "COVERAGE_HOURS",
    "DEFAULT_HANDOFF_SLA_SECONDS",
    "CoverageWindow",
    "HandoffOutcome",
    "HandoffQueue",
    "HandoffResult",
    "HandoffTicket",
    "InMemoryHandoffQueue",
    "build_handoff_acknowledgement",
    "escalate_risk_to_human",
    "escalate_to_human",
]

#: Default SLA target for a human to acknowledge an escalation (5 minutes).
#: Surfaced on every ticket and monitored; the scoping note (AC #1) records the
#: operational reality (who staffs it, hours). Pre-real-users this is a target,
#: not a guarantee — and the fail-safe degrades when it cannot be met.
DEFAULT_HANDOFF_SLA_SECONDS: int = 300

#: Coverage mode constant: responders are on-call 24/7 (no time-of-day gate).
#: The default — preserves the single-lever behaviour where ``available_responders``
#: is the only operational knob.
COVERAGE_ALWAYS: str = "always"

#: Coverage mode constant: responders are on-call only during bounded hours
#: (``open_hour``..``close_hour`` in ``tz_name``). Escalations outside that
#: window degrade to the G1 safe response rather than promising an off-shift
#: person (§10.1 #2/#4). Recorded in the §7.4.1 coverage-hours decision (AC #1).
COVERAGE_HOURS: str = "hours"


@dataclass(slots=True, frozen=True)
class CoverageWindow:
    """When staffed responders are on-call (the §7.4.1 coverage-hours gate).

    A funding-independent plumbing layer over the :class:`HandoffQueue`: even
    when ``available_responders > 0``, an escalation arriving outside the
    configured coverage window degrades to the G1 non-persona safe response —
    it never claims a person is joining when nobody is on-shift (§10.1 #2/#4).
    The default :attr:`COVERAGE_ALWAYS` mode is 24/7 and never degrades on
    time-of-day, so today's single-lever behaviour is unchanged until ops
    configures a bounded window. This is generic coverage-window awareness, so
    it is low-regret regardless of which responder option the board funds.

    Hours are whole-hour boundaries expressed in :attr:`tz_name`. ``open_hour``
    is inclusive (``0``-``23``) and ``close_hour`` is exclusive (``1``-``24``,
    where ``24`` means midnight at the end of the day). A window that wraps
    past midnight (``open_hour > close_hour``, e.g. ``22``->``6`` night cover)
    is supported. ``open_hour == close_hour`` is rejected as a zero-width
    misconfiguration; the full-day case is ``open_hour=0, close_hour=24``.
    """

    mode: str = COVERAGE_ALWAYS
    tz_name: str = "UTC"
    open_hour: int = 0
    close_hour: int = 24

    def __post_init__(self) -> None:
        if self.mode not in (COVERAGE_ALWAYS, COVERAGE_HOURS):
            raise ValueError(
                f"coverage mode must be {COVERAGE_ALWAYS!r} or {COVERAGE_HOURS!r}, "
                f"got {self.mode!r}"
            )
        if self.mode == COVERAGE_HOURS:
            if not 0 <= self.open_hour <= 23:
                raise ValueError(f"open_hour must be in [0, 23], got {self.open_hour}")
            if not 1 <= self.close_hour <= 24:
                raise ValueError(f"close_hour must be in [1, 24], got {self.close_hour}")
            if self.open_hour == self.close_hour:
                raise ValueError(
                    "open_hour equals close_hour (zero-width window); "
                    "use mode 'always' for 24/7 cover or widen the range"
                )

    def is_open(self, now: datetime | None = None) -> bool:
        """Whether ``now`` falls inside the coverage window.

        ``now`` is an aware datetime (UTC by convention) and defaults to the
        current UTC time. It is converted to the window's timezone before the
        hour comparison, so a single ops config (e.g.
        ``America/New_York`` 09:00-17:00) is evaluated correctly regardless of
        where the server runs. In :attr:`COVERAGE_ALWAYS` mode this is always
        ``True`` without inspecting the time.
        """
        if self.mode == COVERAGE_ALWAYS:
            return True
        moment = (now or datetime.now(UTC)).astimezone(ZoneInfo(self.tz_name))
        hour = moment.hour
        if self.open_hour < self.close_hour:
            return self.open_hour <= hour < self.close_hour
        # Wraps past midnight (e.g. 22→6): open at/after open OR before close.
        return hour >= self.open_hour or hour < self.close_hour


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


def _parse_ticket_time(ts: str) -> datetime:
    """Parse a ticket's ISO-8601 timestamp into an aware UTC datetime.

    ``HandoffTicket.created_at`` is stamped via :func:`_now_iso`, so it is
    always offset-aware; this makes the coverage-hours evaluation deterministic
    against the escalation's own timestamp rather than wall-clock ``now``.
    """
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


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
    #: ISO-8601 UTC timestamp stamped when a responder finalizes the ticket via
    #: :meth:`HandoffQueue.resolve`. ``None`` until resolved. Paired with
    #: :attr:`created_at` it yields the responder wait time, which the SLA
    #: monitoring (AC #4) uses to compute the answered-within-SLA rate.
    resolved_at: str | None = None


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
        :attr:`HandoffOutcome.ABANDONED`, records the free-text
        ``clinical_review_note``, and stamps ``resolved_at`` (the SLA-monitoring
        wait-time endpoint). Returns the updated ticket, or ``None`` if no
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

    The optional :class:`CoverageWindow` adds a second, time-of-day gate: even
    when responders are staffed, an escalation outside the coverage window
    degrades to the G1 safe response (``degrade_reason="outside_coverage_hours"``)
    rather than promising an off-shift person. The default ``always`` window
    never degrades on time-of-day, preserving the single-lever behaviour.

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
        coverage: CoverageWindow | None = None,
    ) -> None:
        if available_responders < 0:
            raise ValueError("available_responders must be >= 0")
        self._available_responders = available_responders
        self._responder_id_pool = tuple(responder_id_pool)
        self._sla_target_seconds = sla_target_seconds
        self._coverage = coverage or CoverageWindow()
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
        if self._available_responders <= 0:
            # Fail-safe: no responder on the roster → degrade (§10.1 #2). The
            # chat endpoint returns the G1 non-persona safe response; the persona
            # path is never taken. The ticket is still recorded for audit. This
            # is checked first so the fail-safe reason stays authoritative even
            # if the escalation is also outside coverage hours.
            ticket.outcome = HandoffOutcome.DEGRADED
            ticket.responder_id = None
            ticket.degrade_reason = "no_responder_available"
        elif not self._coverage.is_open(_parse_ticket_time(ticket.created_at)):
            # Coverage-hours gate: responders exist but are off-shift right now.
            # Degrade the same way — never claim a person is joining when nobody
            # is on-call (§10.1 #2/#4). Recorded distinctly so ops can tell
            # "no roster" from "after hours" on the audit/dashboard surface.
            ticket.outcome = HandoffOutcome.DEGRADED
            ticket.responder_id = None
            ticket.degrade_reason = "outside_coverage_hours"
        else:
            ticket.outcome = HandoffOutcome.ENQUEUED
            ticket.responder_id = self._next_responder_id()
            ticket.degrade_reason = None
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
        # Stamp the resolution timestamp so SLA monitoring (AC #4) can compute
        # the responder wait time (resolved_at - created_at) for the
        # answered-within-SLA rate. Plain assignment keeps the in-memory backend
        # synchronous and side-effect-light (§7.1 G1).
        ticket.resolved_at = _now_iso()
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


def _enqueue_with_fail_safe(
    ticket: HandoffTicket,
    *,
    queue: HandoffQueue,
    base_response: str,
) -> HandoffResult:
    """Route ``ticket`` through ``queue`` with the §10.1 #2 fail-safe.

    Shared by the G1-driven (:func:`escalate_to_human`) and risk-driven
    (:func:`escalate_risk_to_human`) paths. If the queue raises, the ticket is
    recorded as ``DEGRADED`` with a ``queue_error:*`` reason and the call
    still returns a G1-safe ``response_text`` — the persona path is
    unreachable from either entry point by construction (callers return
    ``response_text`` directly).
    """
    try:
        finalized = queue.enqueue(ticket)
    except Exception as exc:  # pragma: no cover - defensive, backend-specific
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

    return _enqueue_with_fail_safe(ticket, queue=queue, base_response=base_response)


def escalate_risk_to_human(
    *,
    trigger: str,
    queue: HandoffQueue,
    persona_id: str,
    conversation_id: str | None,
    risk_flags: list[str] | None = None,
    resources: dict[str, str] | None = None,
    sla_target_seconds: int = DEFAULT_HANDOFF_SLA_SECONDS,
) -> HandoffResult:
    """Route a risk-flag-driven escalation into the human-handoff queue (§7.4.4 / matrix §3).

    Matrix §4 composition: the risk-driven ``handoff`` action reuses the G1
    warm non-persona posture + crisis-line display (Phase-1
    :func:`build_crisis_response`) and routes into the same §7.4.1
    (:mod:`huible.safety.handoff`) queue. The only difference from the G1
    path is the audit row's ``trigger_signal`` — it carries a ``risk:`` prefix
    so clinical review distinguishes a G1-crisis escalation from a risk-flag-
    driven one (e.g. escalating distress trend on a ``recent_loss`` session).

    Called **only** from the §7.4.4 enforcement branch of the chat endpoint
    when the binding action is :attr:`~huible.safety.risk.EnforcementAction.HANDOFF`
    — after G1 has already cleared (G1 pre-empts per matrix §4) and after the
    consent gate. The persona path is unreachable from here by construction.

    Fail-safe contract (§10.1 #2): identical to :func:`escalate_to_human` —
    queue errors degrade to the G1 safe response, never drop, never persona.

    ``trigger`` is a short stable label for the audit row (e.g.
    ``"distress_trend_rising"``). ``affect`` is recorded as ``"distress"`` —
    a risk-driven handoff is distress-spectrum by definition (a G1-crisis
    affect would have pre-empted via :func:`escalate_to_human`).
    """
    base_response = build_crisis_response(resources=resources)

    ticket = HandoffTicket(
        id=f"hh-{uuid.uuid4().hex[:16]}",
        persona_id=persona_id,
        conversation_id=conversation_id,
        trigger_signal=f"risk:{trigger}",
        affect="distress",
        matched_patterns=[],
        risk_flags=list(risk_flags or []),
        sla_target_seconds=sla_target_seconds,
    )

    return _enqueue_with_fail_safe(ticket, queue=queue, base_response=base_response)

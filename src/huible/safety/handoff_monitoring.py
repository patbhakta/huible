"""SLA monitoring + outcome telemetry for the human-handoff queue (§7.4 ops gate).

Clinical source: the ``clinical-guardrails`` spec §7.4 #1 / §10.1 invariant 1
("Reachable human, not a print") and invariant 5 ("audit every escalation").
The queue itself ([HU-1421] / :mod:`huible.safety.handoff`) defines the SLA on
every ticket and the fail-safe that degrades when no responder is staffed. This
module is the **operational observability layer** that turns those audit rows
into the dashboard + breach-alerting signal a staffed-responder operation needs
before any real grieving-user traffic flows (HU-1428 AC #4).

Two complementary signals:

* **Live SLA status** (:func:`sla_status`) — for a currently-open (``ENQUEUED``)
  ticket, is it past its SLA right now? This is the breach-alert signal: a
  pending ticket over SLA means a grieving user is waiting past the target and
  the on-call responder must be paged. ``seconds_to_sla`` is the countdown (the
  dashboard headline); negative values are the overdue magnitude.
* **Outcome telemetry** (:func:`compute_handoff_telemetry`) — aggregate counts
  and rates over the audit log: outcome distribution (``enqueued`` /
  ``degraded`` / ``answered`` / ``abandoned``), the degrade rate (the fail-safe
  firing share — must trend to ~0 once staffed), the pending breach rate (open
  tickets past SLA), and the answered-within-SLA rate (resolved tickets whose
  ``resolved_at - created_at`` was within target). Together these are the SLA
  dashboard the Clinical Advisor signs off against before lifting the real-user
  hold.

The functions are pure and take a ticket sequence (e.g. ``queue.audit_log()`` or
``queue.list_pending()``) plus an injectable ``now`` so the suite is
deterministic and key-free. They depend only on the :class:`HandoffTicket` data
model, so a real backend (Postgres / Redis / external paging) gets the same
monitoring for free once it populates the same fields.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from huible.safety.handoff import HandoffOutcome, HandoffTicket

__all__ = [
    "HandoffTelemetry",
    "SLAStatus",
    "compute_handoff_telemetry",
    "sla_status",
    "was_answered_within_sla",
]


def _parse_iso(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp into an aware UTC datetime.

    :meth:`HandoffTicket` stamps ``created_at`` / ``resolved_at`` via
    :func:`datetime.now(UTC).isoformat`, so they are always offset-aware.
    """
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


@dataclass(slots=True, frozen=True)
class SLAStatus:
    """Live SLA status for one ticket at a point in time.

    Meaningful primarily for currently-open (``ENQUEUED``) tickets — the breach
    signal that drives on-call alerting. For already-resolved tickets the age is
    historical; use :func:`was_answered_within_sla` for the resolution-time view.
    """

    breached: bool
    seconds_since_created: int
    seconds_to_sla: int

    @property
    def seconds_overdue(self) -> int:
        """Overdue magnitude in seconds (``0`` when within SLA)."""
        return -self.seconds_to_sla if self.breached else 0


def sla_status(ticket: HandoffTicket, *, now: datetime | None = None) -> SLAStatus:
    """Return the live SLA status of ``ticket`` relative to ``now`` (UTC).

    ``now`` defaults to the current UTC time; tests inject a pinned value. The
    SLA target is read from the ticket (``sla_target_seconds``) so a per-queue
    configured target is honored. ``seconds_to_sla`` is the countdown — positive
    while within target, zero at the boundary, negative once breached (the
    dashboard surfaces ``seconds_overdue`` for breached rows).
    """
    now = now or datetime.now(UTC)
    created = _parse_iso(ticket.created_at)
    age = max(0, int((now - created).total_seconds()))
    breached = age > ticket.sla_target_seconds
    return SLAStatus(
        breached=breached,
        seconds_since_created=age,
        seconds_to_sla=ticket.sla_target_seconds - age,
    )


def was_answered_within_sla(ticket: HandoffTicket) -> bool | None:
    """Whether an answered ticket was acknowledged within its SLA.

    Returns ``True`` / ``False`` only for tickets with a ``resolved_at``
    (i.e. finalized via :meth:`HandoffQueue.resolve`); ``None`` otherwise (the
    ticket is still open or was degraded/abandoned without a resolution
    timestamp). The wait time is ``resolved_at - created_at``.
    """
    if ticket.resolved_at is None:
        return None
    wait = (
        _parse_iso(ticket.resolved_at) - _parse_iso(ticket.created_at)
    ).total_seconds()
    return wait <= ticket.sla_target_seconds


@dataclass(slots=True, frozen=True)
class HandoffTelemetry:
    """Aggregate SLA + outcome telemetry over a set of handoff tickets.

    All rates are in ``[0.0, 1.0]`` and defined to be ``0.0`` when their
    denominator is zero (e.g. no pending tickets yet). The Clinical Advisor's
    pre-real-user sign-off (HU-1428 AC #4 / AC #5) reads this surface: the
    degrade rate must be ~0 once staffed, and the answered-within-SLA rate must
    clear the agreed threshold before real-user traffic flows.
    """

    total: int
    by_outcome: dict[str, int]
    pending: int
    answered: int
    degraded: int
    abandoned: int
    #: Open (``ENQUEUED``) tickets past SLA right now — the live alert count.
    pending_breached: int
    #: Resolved tickets whose wait exceeded SLA — historical miss count.
    answered_breached_sla: int
    #: Degraded / total — the fail-safe firing share.
    degrade_rate: float
    #: pending_breached / pending — live breach pressure.
    pending_breach_rate: float
    #: answered_breached_sla / answered — historical responder miss rate.
    answered_breach_rate: float


def _safe_rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def compute_handoff_telemetry(
    tickets: Iterable[HandoffTicket],
    *,
    now: datetime | None = None,
) -> HandoffTelemetry:
    """Compute aggregate SLA + outcome telemetry over ``tickets``.

    ``tickets`` is any iterable of :class:`HandoffTicket` (typically
    ``queue.audit_log()``). ``now`` is injected for deterministic breach
    detection on open tickets (defaults to current UTC).
    """
    now = now or datetime.now(UTC)
    by_outcome: dict[str, int] = {}
    pending = answered = degraded = abandoned = 0
    pending_breached = answered_breached_sla = 0
    total = 0
    for ticket in tickets:
        total += 1
        by_outcome[ticket.outcome.value] = by_outcome.get(ticket.outcome.value, 0) + 1
        if ticket.outcome is HandoffOutcome.ENQUEUED:
            pending += 1
            if sla_status(ticket, now=now).breached:
                pending_breached += 1
        elif ticket.outcome is HandoffOutcome.ANSWERED:
            answered += 1
            if was_answered_within_sla(ticket) is False:
                answered_breached_sla += 1
        elif ticket.outcome is HandoffOutcome.DEGRADED:
            degraded += 1
        elif ticket.outcome is HandoffOutcome.ABANDONED:
            abandoned += 1
    return HandoffTelemetry(
        total=total,
        by_outcome=by_outcome,
        pending=pending,
        answered=answered,
        degraded=degraded,
        abandoned=abandoned,
        pending_breached=pending_breached,
        answered_breached_sla=answered_breached_sla,
        degrade_rate=_safe_rate(degraded, total),
        pending_breach_rate=_safe_rate(pending_breached, pending),
        answered_breach_rate=_safe_rate(answered_breached_sla, answered),
    )

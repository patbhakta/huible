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

Degrade-reason breakdown (HU-1428 AC #2 / Condition 3 stage-gate): the
:class:`HandoffTelemetry` also splits the degraded count by ``degrade_reason``
into ``outside_coverage_hours`` (an escalation arrived off-shift under the
Tier-2 coverage window) vs ``no_responder_available`` (no roster seat). The
outside-coverage share — :attr:`HandoffTelemetry.outside_coverage_degrade_rate`
— is the Condition-3 signal: a sustained non-zero rate means grieving users are
hitting the closed window and coverage should be extended to Option B
(``HANDOFF_COVERAGE_MODE=always``). It is the input to the coverage-pressure
Sev-1 escalation in :mod:`huible.api.paging`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from huible.safety.handoff import HandoffOutcome, HandoffTicket

__all__ = [
    "DEGRADE_REASON_NO_RESPONDER",
    "DEGRADE_REASON_OUTSIDE_COVERAGE",
    "HandoffTelemetry",
    "SLAStatus",
    "compute_handoff_telemetry",
    "count_outside_coverage_degrades",
    "sla_status",
    "was_answered_within_sla",
]


#: Degrade reasons stamped by :meth:`HandoffQueue.enqueue` (mirrors the literals
#: in :mod:`huible.safety.handoff` / the durable store). Surfaced here so the
#: telemetry + escalation logic does not hard-code magic strings at the call
#: site. See HU-1428 AC #2 / Condition 3.
DEGRADE_REASON_OUTSIDE_COVERAGE: str = "outside_coverage_hours"
DEGRADE_REASON_NO_RESPONDER: str = "no_responder_available"


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
    #: Degraded because the escalation arrived outside the Tier-2 coverage
    #: window (``degrade_reason="outside_coverage_hours"``). The Condition-3
    #: stage-gate signal: grieving users hitting the closed window.
    degraded_outside_coverage: int
    #: Degraded because no responder was on the roster
    #: (``degrade_reason="no_responder_available"``). Pre-staffing / roster gap.
    degraded_no_responder: int
    #: Degraded / total — the fail-safe firing share.
    degrade_rate: float
    #: pending_breached / pending — live breach pressure.
    pending_breach_rate: float
    #: answered_breached_sla / answered — historical responder miss rate.
    answered_breach_rate: float
    #: degraded_outside_coverage / total — the share of all escalations that
    #: degraded because they arrived off-shift. The Condition-3 headline: a
    #: sustained non-zero rate is the signal to extend coverage hours (Option B
    #: / ``HANDOFF_COVERAGE_MODE=always``). ``0.0`` under Option B or pre-staffing.
    outside_coverage_degrade_rate: float


def _safe_rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def count_outside_coverage_degrades(
    tickets: Iterable[HandoffTicket],
) -> int:
    """Count degraded tickets that arrived outside the Tier-2 coverage window.

    The Condition-3 stage-gate input (HU-1428 AC #2): the number of grieving
    users who hit the closed window and degraded to the G1 safe response. This
    is the pressure signal the coverage-pressure Sev-1 escalation
    (:func:`huible.api.paging.escalate_coverage_pressure`) thresholds against.

    Pure over ``ticket.degrade_reason`` (stamped by every
    :meth:`HandoffQueue.enqueue` backend). Tickets that degraded for any other
    reason (no roster seat, queue error) are not counted here — only the
    off-shift signal.
    """
    return sum(
        1
        for t in tickets
        if t.outcome is HandoffOutcome.DEGRADED
        and t.degrade_reason == DEGRADE_REASON_OUTSIDE_COVERAGE
    )


def compute_handoff_telemetry(
    tickets: Iterable[HandoffTicket],
    *,
    now: datetime | None = None,
    window_seconds: int | None = None,
) -> HandoffTelemetry:
    """Compute aggregate SLA + outcome telemetry over ``tickets``.

    ``tickets`` is any iterable of :class:`HandoffTicket` (typically
    ``queue.audit_log()``). ``now`` is injected for deterministic breach
    detection on open tickets (defaults to current UTC).

    ``window_seconds`` restricts the aggregation to tickets **created** within
    the trailing window (``created_at >= now - window_seconds``); ``None``
    (the default) aggregates all-time. The alerting path passes a rolling
    window (``HANDOFF_TELEMETRY_WINDOW_SECONDS``, default 24h) so the §4.1
    gauges reflect *current* queue health: an all-time cumulative
    ``degrade_rate`` is permanently pinned above zero by a single historical
    degrade — observed in production 2026-08-18 when one pre-staffing
    ``no_responder_available`` degrade (the expected fail-safe while the
    roster is unstaffed) held ``HuibleHandoffDegradeRate`` at 100% and paged
    for 25 minutes with no path back to quiet (HU-1865). The
    ``/api/v1/handoff/audit`` dashboard keeps the all-time view so the full
    audit trail remains visible to the Clinical Advisor sign-off.
    """
    now = now or datetime.now(UTC)
    if window_seconds is not None:
        if window_seconds <= 0:
            raise ValueError(
                f"window_seconds must be positive, got {window_seconds!r}"
            )
        cutoff = now - timedelta(seconds=window_seconds)
        tickets = (
            t for t in tickets if _parse_iso(t.created_at) >= cutoff
        )
    by_outcome: dict[str, int] = {}
    pending = answered = degraded = abandoned = 0
    pending_breached = answered_breached_sla = 0
    degraded_outside_coverage = degraded_no_responder = 0
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
            if ticket.degrade_reason == DEGRADE_REASON_OUTSIDE_COVERAGE:
                degraded_outside_coverage += 1
            elif ticket.degrade_reason == DEGRADE_REASON_NO_RESPONDER:
                degraded_no_responder += 1
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
        degraded_outside_coverage=degraded_outside_coverage,
        degraded_no_responder=degraded_no_responder,
        degrade_rate=_safe_rate(degraded, total),
        pending_breach_rate=_safe_rate(pending_breached, pending),
        answered_breach_rate=_safe_rate(answered_breached_sla, answered),
        outside_coverage_degrade_rate=_safe_rate(degraded_outside_coverage, total),
    )

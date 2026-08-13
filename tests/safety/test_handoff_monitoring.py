"""Unit tests for handoff SLA monitoring + outcome telemetry (HU-1428 AC #4).

Covers the operational-observability layer over the §7.4.1 handoff queue:

* **Live SLA status** — within / at-boundary / breached countdowns, and the
  overdue magnitude.
* **Answered-within-SLA** — the ``resolved_at`` wait-time view (True / False /
  None for unresolved).
* **Telemetry** — outcome distribution, degrade rate, pending breach rate, and
  answered breach rate over a mixed audit log.
* **resolved_at stamping** — the queue stamps ``resolved_at`` on resolve() so
  the answered-within-SLA signal is computable end-to-end.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from huible.safety import (
    HandoffOutcome,
    HandoffTicket,
    InMemoryHandoffQueue,
    compute_handoff_telemetry,
    sla_status,
    was_answered_within_sla,
)

NOW = datetime(2026, 8, 11, 18, 0, 0, tzinfo=UTC)


def _ticket(
    *,
    outcome: HandoffOutcome = HandoffOutcome.ENQUEUED,
    sla_target_seconds: int = 300,
    created_at: datetime = NOW,
    resolved_at: datetime | None = None,
    degrade_reason: str | None = None,
) -> HandoffTicket:
    t = HandoffTicket(
        id=f"hh-{created_at.isoformat()}",
        persona_id="p1",
        conversation_id="c1",
        trigger_signal="crisis",
        affect="crisis",
        sla_target_seconds=sla_target_seconds,
    )
    t.created_at = created_at.isoformat()
    t.outcome = outcome
    t.degrade_reason = degrade_reason
    if resolved_at is not None:
        t.resolved_at = resolved_at.isoformat()
    return t


# --- live SLA status --------------------------------------------------------


class TestSLAStatus:
    def test_within_sla_has_positive_countdown(self):
        t = _ticket(created_at=NOW - timedelta(seconds=60))  # 60s old, 300s SLA
        s = sla_status(t, now=NOW)
        assert s.breached is False
        assert s.seconds_since_created == 60
        assert s.seconds_to_sla == 240
        assert s.seconds_overdue == 0

    def test_at_boundary_is_not_breached(self):
        t = _ticket(created_at=NOW - timedelta(seconds=300))  # exactly SLA
        s = sla_status(t, now=NOW)
        assert s.breached is False
        assert s.seconds_to_sla == 0
        assert s.seconds_overdue == 0

    def test_breached_reports_overdue_magnitude(self):
        t = _ticket(created_at=NOW - timedelta(seconds=420))  # 120s over 300s SLA
        s = sla_status(t, now=NOW)
        assert s.breached is True
        assert s.seconds_since_created == 420
        assert s.seconds_to_sla == -120
        assert s.seconds_overdue == 120

    def test_honors_per_ticket_sla_target(self):
        t = _ticket(sla_target_seconds=120, created_at=NOW - timedelta(seconds=180))
        s = sla_status(t, now=NOW)
        assert s.breached is True
        assert s.seconds_overdue == 60


# --- answered within SLA ----------------------------------------------------


class TestAnsweredWithinSLA:
    def test_answered_quickly_is_within_sla(self):
        t = _ticket(
            outcome=HandoffOutcome.ANSWERED,
            created_at=NOW - timedelta(seconds=300),
            resolved_at=NOW - timedelta(seconds=200),
        )
        assert was_answered_within_sla(t) is True  # 100s wait <= 300s

    def test_answered_late_breaches_sla(self):
        t = _ticket(
            outcome=HandoffOutcome.ANSWERED,
            created_at=NOW - timedelta(seconds=600),
            resolved_at=NOW - timedelta(seconds=120),
        )
        assert was_answered_within_sla(t) is False  # 480s wait > 300s

    def test_unresolved_returns_none(self):
        t = _ticket(outcome=HandoffOutcome.ENQUEUED)
        assert was_answered_within_sla(t) is None


# --- telemetry --------------------------------------------------------------


class TestHandoffTelemetry:
    def test_empty_audit_log_is_zeroed(self):
        tel = compute_handoff_telemetry([], now=NOW)
        assert tel.total == 0
        assert tel.pending == 0
        assert tel.degrade_rate == 0.0
        assert tel.pending_breach_rate == 0.0
        assert tel.answered_breach_rate == 0.0

    def test_outcome_distribution_and_degrade_rate(self):
        tickets = [
            _ticket(outcome=HandoffOutcome.DEGRADED),
            _ticket(outcome=HandoffOutcome.DEGRADED),
            _ticket(outcome=HandoffOutcome.ANSWERED, resolved_at=NOW),
            _ticket(outcome=HandoffOutcome.ABANDONED, resolved_at=NOW),
            _ticket(outcome=HandoffOutcome.ENQUEUED),
        ]
        tel = compute_handoff_telemetry(tickets, now=NOW)
        assert tel.total == 5
        assert tel.by_outcome == {
            "degraded": 2,
            "answered": 1,
            "abandoned": 1,
            "enqueued": 1,
        }
        assert tel.degraded == 2
        assert tel.answered == 1
        assert tel.abandoned == 1
        assert tel.pending == 1
        assert tel.degrade_rate == 0.4

    def test_pending_breach_rate(self):
        # Two open tickets: one fresh (within SLA), one stale (breached).
        tickets = [
            _ticket(outcome=HandoffOutcome.ENQUEUED, created_at=NOW - timedelta(seconds=60)),
            _ticket(outcome=HandoffOutcome.ENQUEUED, created_at=NOW - timedelta(seconds=400)),
        ]
        tel = compute_handoff_telemetry(tickets, now=NOW)
        assert tel.pending == 2
        assert tel.pending_breached == 1
        assert tel.pending_breach_rate == 0.5

    def test_answered_breach_rate(self):
        # Two answered: one within SLA, one over SLA.
        tickets = [
            _ticket(
                outcome=HandoffOutcome.ANSWERED,
                created_at=NOW - timedelta(seconds=300),
                resolved_at=NOW - timedelta(seconds=240),
            ),
            _ticket(
                outcome=HandoffOutcome.ANSWERED,
                created_at=NOW - timedelta(seconds=600),
                resolved_at=NOW - timedelta(seconds=60),
            ),
        ]
        tel = compute_handoff_telemetry(tickets, now=NOW)
        assert tel.answered == 2
        assert tel.answered_breached_sla == 1
        assert tel.answered_breach_rate == 0.5


# --- degrade-reason breakdown (HU-1428 AC #2 / Condition 3 stage-gate) ------


class TestDegradeReasonBreakdown:
    def test_outside_coverage_degrades_are_counted(self):
        # Two off-shift degrades + one no-roster degrade + one answered.
        tickets = [
            _ticket(outcome=HandoffOutcome.DEGRADED, degrade_reason="outside_coverage_hours"),
            _ticket(outcome=HandoffOutcome.DEGRADED, degrade_reason="outside_coverage_hours"),
            _ticket(outcome=HandoffOutcome.DEGRADED, degrade_reason="no_responder_available"),
            _ticket(outcome=HandoffOutcome.ANSWERED, resolved_at=NOW),
        ]
        tel = compute_handoff_telemetry(tickets, now=NOW)
        assert tel.degraded == 3
        assert tel.degraded_outside_coverage == 2
        assert tel.degraded_no_responder == 1
        # outside_coverage_degrade_rate = 2 off-shift / 4 total
        assert tel.outside_coverage_degrade_rate == 0.5

    def test_no_outside_coverage_degrades_is_zero_rate(self):
        # Pre-staffing / Option B: every degrade is no-roster, none off-shift.
        tickets = [
            _ticket(outcome=HandoffOutcome.DEGRADED, degrade_reason="no_responder_available"),
            _ticket(outcome=HandoffOutcome.ENQUEUED),
        ]
        tel = compute_handoff_telemetry(tickets, now=NOW)
        assert tel.degraded_outside_coverage == 0
        assert tel.degraded_no_responder == 1
        assert tel.outside_coverage_degrade_rate == 0.0

    def test_empty_audit_log_has_zeroed_breakdown(self):
        tel = compute_handoff_telemetry([], now=NOW)
        assert tel.degraded_outside_coverage == 0
        assert tel.degraded_no_responder == 0
        assert tel.outside_coverage_degrade_rate == 0.0

    def test_count_outside_coverage_degrades_helper(self):
        from huible.safety.handoff_monitoring import count_outside_coverage_degrades

        tickets = [
            _ticket(outcome=HandoffOutcome.DEGRADED, degrade_reason="outside_coverage_hours"),
            _ticket(outcome=HandoffOutcome.DEGRADED, degrade_reason="no_responder_available"),
            _ticket(outcome=HandoffOutcome.DEGRADED, degrade_reason="outside_coverage_hours"),
            _ticket(outcome=HandoffOutcome.ENQUEUED),
            _ticket(outcome=HandoffOutcome.ANSWERED, resolved_at=NOW),
        ]
        assert count_outside_coverage_degrades(tickets) == 2
        assert count_outside_coverage_degrades([]) == 0


# --- resolved_at stamping on resolve() --------------------------------------


class TestResolvedAtStamping:
    def test_resolve_stamps_resolved_at(self):
        q = InMemoryHandoffQueue(available_responders=1, responder_id_pool=("pat",))
        ticket = HandoffTicket(
            id="hh-stamp",
            persona_id="p1",
            conversation_id="c1",
            trigger_signal="crisis",
            affect="crisis",
        )
        enqueued = q.enqueue(ticket)
        assert enqueued.resolved_at is None
        resolved = q.resolve(
            "hh-stamp",
            outcome=HandoffOutcome.ANSWERED,
            clinical_review_note="user stabilized",
        )
        assert resolved is not None
        assert resolved.resolved_at is not None
        # The stamp is a parseable ISO-8601 timestamp at/after creation.
        assert was_answered_within_sla(resolved) is True

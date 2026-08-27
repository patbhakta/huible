"""Unit tests for the human-handoff (crisis escalation) queue (HU-1421 / §7.4.1).

Covers every §10.1 fail-safe invariant as a deterministic unit before the
end-to-end wiring is exercised in ``tests/api/test_chat_guardrails.py``:

* **#1 reachable human / SLA defined + monitored** — every ticket carries the
  configured SLA; the staffing model is explicit (``available_responders``).
* **#2 fail-safe default = degrade, never drop** — 0 responders degrades to the
  G1 safe response and never claims a person is joining; queue errors degrade.
* **#3 routing trigger is G1-derived, not persona-output** —
  :func:`escalate_to_human` takes the :class:`CrisisResult` as its routing
  input; a non-crisis message is never routed.
* **#4 in-session UX is non-persona while waiting** — the acknowledgement is
  non-persona, resources-visible, and only says "a person will join" on enqueue.
* **#5 audit every escalation** — every ticket carries trigger signal, risk
  flags, timestamp, SLA target, outcome, and a clinical-review field.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from huible.safety import (
    COVERAGE_ALWAYS,
    COVERAGE_HOURS,
    DEFAULT_HANDOFF_SLA_SECONDS,
    CoverageWindow,
    HandoffOutcome,
    HandoffTicket,
    InMemoryHandoffQueue,
    build_handoff_acknowledgement,
    escalate_to_human,
    parse_coverage_days,
)
from huible.safety.crisis import CrisisResult, CrisisSignal, UserAffect, classify_user_message


def _crisis() -> CrisisResult:
    return classify_user_message("I want to die")


def _dt(*, hour: int, minute: int = 0) -> datetime:
    """A pinned UTC datetime on a fixed summer date for coverage-window tests."""
    return datetime(2026, 7, 1, hour, minute, tzinfo=UTC)


# --- #1: SLA defined + monitored, staffing model explicit -------------------


class TestSLAandStaffing:
    def test_default_sla_constant(self):
        assert DEFAULT_HANDOFF_SLA_SECONDS == 300

    def test_every_ticket_carries_sla_target(self):
        q = InMemoryHandoffQueue(sla_target_seconds=420)
        r = escalate_to_human(
            "I want to die",
            crisis_result=_crisis(),
            queue=q,
            persona_id="p1",
            conversation_id="c1",
        )
        assert r.ticket.sla_target_seconds == 420
        # The audit row surfaces the monitored SLA target.
        assert q.audit_log()[0].sla_target_seconds == 420

    def test_staffing_model_is_explicit_and_defaults_to_zero(self):
        q = InMemoryHandoffQueue()
        assert q.available_responders == 0
        # The honest pre-real-user posture: no roster → degrade.
        with pytest.raises(ValueError):
            InMemoryHandoffQueue(available_responders=-1)

    def test_responder_pool_round_robin(self):
        q = InMemoryHandoffQueue(
            available_responders=2, responder_id_pool=("pat", "lee")
        )
        a = escalate_to_human(
            "I want to die", crisis_result=_crisis(), queue=q, persona_id="p", conversation_id="c"
        )
        b = escalate_to_human(
            "I want to die", crisis_result=_crisis(), queue=q, persona_id="p", conversation_id="c2"
        )
        assert {a.ticket.responder_id, b.ticket.responder_id} == {"pat", "lee"}
        assert a.ticket.responder_id != b.ticket.responder_id

    def test_synthetic_responder_id_when_pool_empty_but_staffed(self):
        q = InMemoryHandoffQueue(available_responders=1)
        r = escalate_to_human(
            "I want to die", crisis_result=_crisis(), queue=q, persona_id="p", conversation_id="c"
        )
        # Truthful "a person will join" requires a responder id exists.
        assert r.ticket.responder_id is not None
        assert r.ticket.responder_id.startswith("on-call-")


# --- #2: fail-safe default = degrade, never drop ----------------------------


class TestFailSafeDegrade:
    def test_zero_responders_degrades(self):
        q = InMemoryHandoffQueue()
        r = escalate_to_human(
            "I want to die",
            crisis_result=_crisis(),
            queue=q,
            persona_id="p1",
            conversation_id="c1",
        )
        assert r.degraded is True
        assert r.ticket.outcome is HandoffOutcome.DEGRADED
        assert r.ticket.degrade_reason == "no_responder_available"
        assert r.ticket.responder_id is None

    def test_degraded_response_still_carries_crisis_resources(self):
        q = InMemoryHandoffQueue()
        r = escalate_to_human(
            "I want to die",
            crisis_result=_crisis(),
            queue=q,
            persona_id="p1",
            conversation_id="c1",
        )
        # The G1 safe response is the floor — resources always surfaced.
        assert "988" in r.response_text
        assert "still be here" in r.response_text

    def test_degraded_response_never_claims_a_person_joining(self):
        """§10.1 #2/#4: degrade must never claim a person is joining when none was paged."""
        q = InMemoryHandoffQueue()
        r = escalate_to_human(
            "I want to die",
            crisis_result=_crisis(),
            queue=q,
            persona_id="p1",
            conversation_id="c1",
        )
        assert "person to reach out" not in r.response_text
        assert "person will join" not in r.response_text
        # Non-persona: no deceased-voice markers.
        assert "[fake-llm:" not in r.response_text

    def test_queue_error_degrades_and_records_reason(self):
        class _BrokenQueue(InMemoryHandoffQueue):
            def enqueue(self, ticket: HandoffTicket) -> HandoffTicket:
                raise RuntimeError("backend down")

        q = _BrokenQueue()
        r = escalate_to_human(
            "I want to die",
            crisis_result=_crisis(),
            queue=q,
            persona_id="p1",
            conversation_id="c1",
        )
        assert r.degraded is True
        assert r.ticket.outcome is HandoffOutcome.DEGRADED
        assert r.ticket.degrade_reason == "queue_error:RuntimeError"
        # The user still gets the G1 safe response, never a persona turn.
        assert "988" in r.response_text

    def test_degraded_ticket_still_recorded_for_audit(self):
        q = InMemoryHandoffQueue()
        escalate_to_human(
            "I want to die",
            crisis_result=_crisis(),
            queue=q,
            persona_id="p1",
            conversation_id="c1",
        )
        log = q.audit_log()
        assert len(log) == 1
        assert log[0].outcome is HandoffOutcome.DEGRADED


# --- #3: routing trigger is G1-derived, not persona-output ------------------


class TestRoutingTrigger:
    def test_non_crisis_signal_is_not_the_routing_input(self):
        """escalate_to_human routes on the crisis result, not on text content alone.

        A non-crisis CrisisResult (NONE) is a valid call (callers guard with
        ``is_crisis``), but the ticket faithfully records the trigger signal —
        the queue never invents a crisis signal from the message text.
        """
        q = InMemoryHandoffQueue()
        neutral = CrisisResult(signal=CrisisSignal.NONE, affect=UserAffect.NEUTRAL)
        r = escalate_to_human(
            "I am fine",
            crisis_result=neutral,
            queue=q,
            persona_id="p1",
            conversation_id="c1",
        )
        assert r.ticket.trigger_signal == "none"
        assert r.ticket.affect == "neutral"


# --- #4: in-session UX is non-persona while waiting ------------------------


class TestWaitingUX:
    def test_enqueued_acknowledgement_is_non_persona_and_warm(self):
        q = InMemoryHandoffQueue(available_responders=1)
        r = escalate_to_human(
            "I want to die",
            crisis_result=_crisis(),
            queue=q,
            persona_id="p1",
            conversation_id="c1",
        )
        assert r.ticket.outcome is HandoffOutcome.ENQUEUED
        # Resources always visible.
        assert "988" in r.response_text
        # Explicit "a person will join" only when a responder was paged.
        assert "person to reach out" in r.response_text
        # Non-persona: deceased never voices the escalation.
        assert "[fake-llm:" not in r.response_text

    def test_build_acknowledgement_empty_on_degrade(self):
        """The acknowledgement helper is empty on degrade (no false 'person joining')."""
        assert (
            build_handoff_acknowledgement(
                outcome=HandoffOutcome.DEGRADED, sla_target_seconds=300
            )
            == ""
        )

    def test_build_acknowledgement_carries_sla_on_enqueue(self):
        text = build_handoff_acknowledgement(
            outcome=HandoffOutcome.ENQUEUED, sla_target_seconds=600, responder_id="pat"
        )
        assert "person to reach out" in text
        assert "10 minutes" in text  # 600s → 10 min
        assert "pat" in text


# --- #5: audit every escalation (all required fields) ----------------------


class TestAuditFields:
    def test_ticket_carries_all_required_audit_fields(self):
        """§10.1 #5: trigger signal, risk flags, timestamp, SLA target, outcome, clinical note."""
        q = InMemoryHandoffQueue(sla_target_seconds=180)
        r = escalate_to_human(
            "I want to die",
            crisis_result=_crisis(),
            queue=q,
            persona_id="p1",
            conversation_id="c-42",
            risk_flags=["recent_loss", "non_acceptance"],
        )
        t = r.ticket
        assert t.id.startswith("hh-")
        assert t.persona_id == "p1"
        assert t.conversation_id == "c-42"
        # trigger signal + affect
        assert t.trigger_signal == "crisis"
        assert t.affect == "crisis"
        # risk flags present
        assert t.risk_flags == ["recent_loss", "non_acceptance"]
        # matched classifier patterns (audit only)
        assert len(t.matched_patterns) >= 1
        # timestamp present + ISO-parseable
        assert t.created_at
        from datetime import datetime

        datetime.fromisoformat(t.created_at)  # raises if malformed
        # SLA target
        assert t.sla_target_seconds == 180
        # outcome
        assert t.outcome in (
            HandoffOutcome.ENQUEUED,
            HandoffOutcome.DEGRADED,
            HandoffOutcome.ANSWERED,
            HandoffOutcome.ABANDONED,
        )

    def test_resolve_records_clinical_review_note(self):
        q = InMemoryHandoffQueue(available_responders=1, responder_id_pool=("pat",))
        r = escalate_to_human(
            "I want to die",
            crisis_result=_crisis(),
            queue=q,
            persona_id="p1",
            conversation_id="c1",
        )
        finalized = q.resolve(
            r.ticket.id,
            outcome=HandoffOutcome.ANSWERED,
            responder_id="pat",
            clinical_review_note="user stabilized; referred to ongoing care",
        )
        assert finalized is not None
        assert finalized.outcome is HandoffOutcome.ANSWERED
        assert finalized.clinical_review_note == "user stabilized; referred to ongoing care"
        # The note is visible on the audit row.
        assert q.get(r.ticket.id).clinical_review_note.startswith("user stabilized")

    def test_resolve_rejects_non_terminal_outcome(self):
        q = InMemoryHandoffQueue(available_responders=1)
        r = escalate_to_human(
            "I want to die",
            crisis_result=_crisis(),
            queue=q,
            persona_id="p1",
            conversation_id="c1",
        )
        with pytest.raises(ValueError):
            q.resolve(r.ticket.id, outcome=HandoffOutcome.ENQUEUED)

    def test_list_pending_excludes_degraded_and_resolved(self):
        q = InMemoryHandoffQueue()
        r1 = escalate_to_human(
            "I want to die", crisis_result=_crisis(), queue=q, persona_id="p", conversation_id="c1"
        )
        # All degraded → no pending.
        assert q.list_pending() == []
        assert len(q.audit_log()) == 1
        # Resolve path doesn't apply to degraded tickets, but the audit row stays.
        assert r1.ticket.outcome is HandoffOutcome.DEGRADED

    def test_audit_log_is_insertion_ordered_and_complete(self):
        q = InMemoryHandoffQueue(available_responders=1, responder_id_pool=("pat",))
        for i in range(3):
            escalate_to_human(
                "I want to die",
                crisis_result=_crisis(),
                queue=q,
                persona_id="p",
                conversation_id=f"c{i}",
            )
        log = q.audit_log()
        assert [t.conversation_id for t in log] == ["c0", "c1", "c2"]
        assert all(t.outcome is HandoffOutcome.ENQUEUED for t in log)


# --- Protocol conformance --------------------------------------------------


class TestProtocolConformance:
    def test_in_memory_queue_satisfies_protocol(self):
        from huible.safety import HandoffQueue

        assert isinstance(InMemoryHandoffQueue(), HandoffQueue)


# --- coverage-hours gate (§7.4.1 coverage window) --------------------------


class TestCoverageWindow:
    def test_default_is_always_mode(self):
        w = CoverageWindow()
        assert w.mode == COVERAGE_ALWAYS
        assert w.mode == "always"

    def test_always_mode_is_open_at_any_hour(self):
        w = CoverageWindow()  # default always
        for h in range(24):
            assert w.is_open(_dt(hour=h)) is True

    def test_hours_mode_open_within_window(self):
        w = CoverageWindow(mode=COVERAGE_HOURS, open_hour=9, close_hour=17)
        assert w.is_open(_dt(hour=9)) is True  # open boundary inclusive
        assert w.is_open(_dt(hour=12)) is True
        assert w.is_open(_dt(hour=16, minute=59)) is True

    def test_hours_mode_closed_outside_window(self):
        w = CoverageWindow(mode=COVERAGE_HOURS, open_hour=9, close_hour=17)
        assert w.is_open(_dt(hour=8)) is False
        assert w.is_open(_dt(hour=17)) is False  # close boundary exclusive
        assert w.is_open(_dt(hour=23)) is False

    def test_wraparound_window_covers_overnight(self):
        w = CoverageWindow(mode=COVERAGE_HOURS, open_hour=22, close_hour=6)
        assert w.is_open(_dt(hour=22)) is True
        assert w.is_open(_dt(hour=3)) is True
        assert w.is_open(_dt(hour=5, minute=59)) is True
        assert w.is_open(_dt(hour=6)) is False  # close boundary exclusive
        assert w.is_open(_dt(hour=12)) is False

    def test_full_day_hours_window_is_always_open(self):
        w = CoverageWindow(mode=COVERAGE_HOURS, open_hour=0, close_hour=24)
        for h in range(24):
            assert w.is_open(_dt(hour=h)) is True

    def test_timezone_is_applied_to_evaluation(self):
        # 09:00 America/New_York (UTC-4 in summer) == 13:00 UTC.
        w = CoverageWindow(
            mode=COVERAGE_HOURS, tz_name="America/New_York", open_hour=9, close_hour=17
        )
        assert w.is_open(datetime(2026, 7, 1, 13, 0, tzinfo=UTC)) is True  # 9am EDT
        assert w.is_open(datetime(2026, 7, 1, 21, 0, tzinfo=UTC)) is False  # 5pm EDT

    def test_zero_width_window_rejected(self):
        with pytest.raises(ValueError):
            CoverageWindow(mode=COVERAGE_HOURS, open_hour=9, close_hour=9)

    def test_invalid_mode_rejected(self):
        with pytest.raises(ValueError):
            CoverageWindow(mode="sometimes")

    def test_out_of_range_hours_rejected(self):
        with pytest.raises(ValueError):
            CoverageWindow(mode=COVERAGE_HOURS, open_hour=24, close_hour=6)
        with pytest.raises(ValueError):
            CoverageWindow(mode=COVERAGE_HOURS, open_hour=0, close_hour=0)

    # -- committed coverage days (HU-2110) ------------------------------------

    def test_days_none_keeps_every_day_behaviour(self):
        w = CoverageWindow(mode=COVERAGE_HOURS, open_hour=9, close_hour=17)
        # Mon 2026-06-29 through Sun 2026-07-05, mid-window hour: all open.
        monday = datetime(2026, 6, 29, 12, 0, tzinfo=UTC)
        for offset in range(7):
            assert w.is_open(monday + timedelta(days=offset)) is True

    def test_weekday_only_window_closed_on_weekend(self):
        w = CoverageWindow(mode=COVERAGE_HOURS, open_hour=9, close_hour=17, days=(1, 2, 3, 4, 5))
        assert w.is_open(datetime(2026, 7, 1, 12, 0, tzinfo=UTC)) is True  # Wed
        assert w.is_open(datetime(2026, 7, 4, 12, 0, tzinfo=UTC)) is False  # Sat
        assert w.is_open(datetime(2026, 7, 5, 12, 0, tzinfo=UTC)) is False  # Sun

    def test_single_day_window_closed_other_days_same_hour(self):
        w = CoverageWindow(mode=COVERAGE_HOURS, open_hour=9, close_hour=17, days=(1,))  # Mon
        assert w.is_open(datetime(2026, 6, 29, 12, 0, tzinfo=UTC)) is True  # Mon in-hours
        assert w.is_open(datetime(2026, 6, 29, 8, 0, tzinfo=UTC)) is False  # Mon off-hours
        assert w.is_open(datetime(2026, 6, 30, 12, 0, tzinfo=UTC)) is False  # Tue same hour

    def test_wraparound_tail_belongs_to_opening_day(self):
        # Mon 22:00 -> Tue 06:00 committed as days=(1,): Tuesday 03:00 is the
        # tail of Monday's window and must stay open; Tue 22:00 is closed.
        w = CoverageWindow(mode=COVERAGE_HOURS, open_hour=22, close_hour=6, days=(1,))
        assert w.is_open(datetime(2026, 6, 29, 22, 0, tzinfo=UTC)) is True  # Mon open
        assert w.is_open(datetime(2026, 6, 30, 3, 0, tzinfo=UTC)) is True  # Tue tail
        assert w.is_open(datetime(2026, 6, 30, 6, 0, tzinfo=UTC)) is False  # Tue close
        assert w.is_open(datetime(2026, 6, 30, 22, 0, tzinfo=UTC)) is False  # Tue not committed

    def test_wraparound_sunday_night_opens_monday_tail(self):
        # Sun 22:00 -> Mon 06:00 (days=(7,)): Monday 03:00 is open, Monday
        # 22:00 is not (only Sunday evenings are committed).
        w = CoverageWindow(mode=COVERAGE_HOURS, open_hour=22, close_hour=6, days=(7,))
        assert w.is_open(datetime(2026, 7, 5, 22, 0, tzinfo=UTC)) is True  # Sun open
        assert w.is_open(datetime(2026, 7, 6, 3, 0, tzinfo=UTC)) is True  # Mon tail
        assert w.is_open(datetime(2026, 7, 6, 22, 0, tzinfo=UTC)) is False  # Mon evening

    def test_days_applied_in_window_timezone(self):
        # 22:00 America/New_York on Wed Jun 30 summer (EDT=UTC-4) is 02:00 UTC
        # Thu Jul 1 — a mon-fri window must treat it as Wednesday night (open)
        # even though the UTC weekday is Thursday.
        w = CoverageWindow(
            mode=COVERAGE_HOURS,
            tz_name="America/New_York",
            open_hour=20,
            close_hour=23,
            days=(1, 2, 3, 4, 5),
        )
        assert w.is_open(datetime(2026, 7, 1, 2, 0, tzinfo=UTC)) is True  # Wed 22:00 EDT

    def test_invalid_days_rejected(self):
        with pytest.raises(ValueError):
            CoverageWindow(mode=COVERAGE_HOURS, open_hour=9, close_hour=17, days=(0, 1))
        with pytest.raises(ValueError):
            CoverageWindow(mode=COVERAGE_HOURS, open_hour=9, close_hour=17, days=(8,))
        with pytest.raises(ValueError):
            CoverageWindow(mode=COVERAGE_HOURS, open_hour=9, close_hour=17, days=())


class TestParseCoverageDays:
    def test_empty_spec_means_every_day(self):
        assert parse_coverage_days("") is None
        assert parse_coverage_days("   ") is None

    def test_numeric_range(self):
        assert parse_coverage_days("1-5") == (1, 2, 3, 4, 5)

    def test_name_range(self):
        assert parse_coverage_days("mon-fri") == (1, 2, 3, 4, 5)

    def test_mixed_list_sorted_and_deduped(self):
        assert parse_coverage_days("Fri,Mon,mon,3,sun") == (1, 3, 5, 7)

    def test_single_day_full_name(self):
        assert parse_coverage_days("Saturday") == (6,)

    def test_whitespace_tolerant(self):
        assert parse_coverage_days(" mon - fri ") == (1, 2, 3, 4, 5)

    def test_garbage_rejected(self):
        with pytest.raises(ValueError):
            parse_coverage_days("funday")
        with pytest.raises(ValueError):
            parse_coverage_days("0")
        with pytest.raises(ValueError):
            parse_coverage_days("8")
        with pytest.raises(ValueError):
            parse_coverage_days("fri-mon")  # inverted range
        with pytest.raises(ValueError):
            parse_coverage_days(",,")  # no actual days
        with pytest.raises(ValueError):
            CoverageWindow(mode=COVERAGE_HOURS, open_hour=9, close_hour=25)

    def test_invalid_hours_not_validated_in_always_mode(self):
        # always mode ignores the hour fields, so out-of-range values are fine.
        w = CoverageWindow(mode=COVERAGE_ALWAYS, open_hour=99, close_hour=0)
        assert w.is_open(_dt(hour=3)) is True


class TestCoverageGateInQueue:
    """The coverage window gates enqueue only when responders are staffed."""

    @staticmethod
    def _ticket(*, created_at: str) -> HandoffTicket:
        return HandoffTicket(
            id="hh-test",
            persona_id="p1",
            conversation_id="c1",
            trigger_signal="crisis",
            affect="crisis",
            created_at=created_at,
        )

    def test_outside_coverage_hours_degrades_even_with_responders(self):
        q = InMemoryHandoffQueue(
            available_responders=1,
            coverage=CoverageWindow(mode=COVERAGE_HOURS, open_hour=9, close_hour=17),
        )
        # 03:00 UTC is outside the 09-17 window.
        t = self._ticket(created_at="2026-07-01T03:00:00+00:00")
        q.enqueue(t)
        assert t.outcome is HandoffOutcome.DEGRADED
        assert t.degrade_reason == "outside_coverage_hours"
        assert t.responder_id is None

    def test_within_coverage_hours_enqueues(self):
        q = InMemoryHandoffQueue(
            available_responders=1,
            coverage=CoverageWindow(mode=COVERAGE_HOURS, open_hour=9, close_hour=17),
        )
        t = self._ticket(created_at="2026-07-01T12:00:00+00:00")
        q.enqueue(t)
        assert t.outcome is HandoffOutcome.ENQUEUED
        assert t.responder_id is not None
        assert t.degrade_reason is None

    def test_zero_responders_reports_no_responder_not_outside_hours(self):
        """§10.1 #2: the fail-safe reason stays authoritative.

        When responders=0 the ticket degrades with ``no_responder_available``
        even if it is also outside coverage hours — ops must be able to tell
        "no roster" from "after hours" on the audit surface.
        """
        q = InMemoryHandoffQueue(
            available_responders=0,
            coverage=CoverageWindow(mode=COVERAGE_HOURS, open_hour=9, close_hour=17),
        )
        t = self._ticket(created_at="2026-07-01T03:00:00+00:00")
        q.enqueue(t)
        assert t.outcome is HandoffOutcome.DEGRADED
        assert t.degrade_reason == "no_responder_available"

    def test_always_coverage_with_responders_enqueues_at_any_hour(self):
        q = InMemoryHandoffQueue(
            available_responders=1,
            coverage=CoverageWindow(),  # always (default)
        )
        t = self._ticket(created_at="2026-07-01T03:00:00+00:00")
        q.enqueue(t)
        assert t.outcome is HandoffOutcome.ENQUEUED

    def test_default_queue_has_always_coverage(self):
        # Constructing without an explicit window defaults to always → today's
        # single-lever behaviour is unchanged by this feature.
        q = InMemoryHandoffQueue(available_responders=1)
        t = self._ticket(created_at="2026-07-01T03:00:00+00:00")
        q.enqueue(t)
        assert t.outcome is HandoffOutcome.ENQUEUED

    def test_outside_hours_degraded_ticket_is_audited(self):
        """§10.1 #5: every escalation is audited, even off-hours degradations."""
        q = InMemoryHandoffQueue(
            available_responders=1,
            coverage=CoverageWindow(mode=COVERAGE_HOURS, open_hour=9, close_hour=17),
        )
        t = self._ticket(created_at="2026-07-01T03:00:00+00:00")
        q.enqueue(t)
        log = q.audit_log()
        assert len(log) == 1
        assert log[0].outcome is HandoffOutcome.DEGRADED
        assert log[0].degrade_reason == "outside_coverage_hours"
        # And it is not in the pending (staffed-responder) work queue.
        assert q.list_pending() == []

    def test_outside_hours_degradation_never_claims_person_joining(self):
        """§10.1 #2/#4: an off-hours degrade produces no "person will join" text.

        Deterministic via a direct enqueue with a pinned outside-hours
        timestamp, then routing the finalized ticket through the same
        acknowledgement builder the chat endpoint uses.
        """
        from huible.safety.handoff import build_handoff_acknowledgement

        q = InMemoryHandoffQueue(
            available_responders=1,
            coverage=CoverageWindow(mode=COVERAGE_HOURS, open_hour=9, close_hour=17),
        )
        t = self._ticket(created_at="2026-07-01T03:00:00+00:00")
        q.enqueue(t)
        assert t.outcome is HandoffOutcome.DEGRADED
        acknowledgement = build_handoff_acknowledgement(
            outcome=t.outcome,
            sla_target_seconds=t.sla_target_seconds,
            responder_id=t.responder_id,
        )
        assert acknowledgement == ""
        assert "person to reach out" not in acknowledgement

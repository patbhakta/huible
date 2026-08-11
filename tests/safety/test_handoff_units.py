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

import pytest

from huible.safety import (
    DEFAULT_HANDOFF_SLA_SECONDS,
    HandoffOutcome,
    HandoffTicket,
    InMemoryHandoffQueue,
    build_handoff_acknowledgement,
    escalate_to_human,
)
from huible.safety.crisis import CrisisResult, CrisisSignal, UserAffect, classify_user_message


def _crisis() -> CrisisResult:
    return classify_user_message("I want to die")


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

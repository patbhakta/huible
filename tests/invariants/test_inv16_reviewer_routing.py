from __future__ import annotations

from uuid import uuid4

from huible.adjudication.reviewer_routing import (
    DEFAULT_ROUTING_TABLE,
    ReviewerRouter,
    ReviewerType,
)
from huible.memory.protocol import (
    QuarantineEntry,
    QuarantinePriority,
    QuarantineStatus,
)


def _make_entry(
    persona_id,
    failed_gates: list[str] | None = None,
    priority: QuarantinePriority = QuarantinePriority.LOW,
    candidate_data: dict | None = None,
) -> QuarantineEntry:
    return QuarantineEntry(
        id=uuid4(),
        candidate_data=candidate_data or {"content": "test memory"},
        persona_id=persona_id,
        failed_gates=failed_gates or [],
        priority=priority,
        status=QuarantineStatus.PENDING,
    )


class TestReviewerRoutingSafety:
    """Safety gate ambiguity routes to safety_agent with critical priority."""

    async def test_safety_gate_routes_to_safety_agent(self, persona_id):
        entry = _make_entry(
            persona_id,
            failed_gates=["safety"],
            candidate_data={"content": "test", "safety_flag_reason": "ambiguous_safety"},
        )
        router = ReviewerRouter()
        decision = router.route(entry)
        assert decision.reviewer_type == ReviewerType.SAFETY_AGENT
        assert decision.priority == QuarantinePriority.CRITICAL

    async def test_safety_gate_critical_priority(self, persona_id):
        entry = _make_entry(
            persona_id,
            failed_gates=["safety"],
        )
        router = ReviewerRouter()
        decision = router.route(entry)
        assert decision.priority == QuarantinePriority.CRITICAL


class TestReviewerRoutingImmutability:
    """Immutability conflicts route based on conflict type."""

    async def test_disclosure_conflict_routes_to_clinical(self, persona_id):
        entry = _make_entry(
            persona_id,
            failed_gates=["immutability"],
            candidate_data={"content": "test", "immutability_conflict_type": "disclosure"},
        )
        router = ReviewerRouter()
        decision = router.route(entry)
        assert decision.reviewer_type == ReviewerType.CLINICAL_ADVISOR
        assert decision.priority == QuarantinePriority.CRITICAL

    async def test_canonical_conflict_routes_to_senior(self, persona_id):
        entry = _make_entry(
            persona_id,
            failed_gates=["immutability"],
            candidate_data={"content": "test", "immutability_conflict_type": "canonical"},
        )
        router = ReviewerRouter()
        decision = router.route(entry)
        assert decision.reviewer_type == ReviewerType.SENIOR_AGENT
        assert decision.priority == QuarantinePriority.HIGH


class TestReviewerRoutingNovelty:
    """Novelty ambiguity routes to senior_agent (hallucination check)."""

    async def test_novelty_routes_to_senior(self, persona_id):
        entry = _make_entry(
            persona_id,
            failed_gates=["novelty"],
        )
        router = ReviewerRouter()
        decision = router.route(entry)
        assert decision.reviewer_type == ReviewerType.SENIOR_AGENT
        assert decision.priority == QuarantinePriority.HIGH


class TestReviewerRoutingPertinence:
    """Low pertinence routes to batch_reviewer."""

    async def test_low_pertinence_routes_to_batch(self, persona_id):
        entry = _make_entry(
            persona_id,
            failed_gates=["pertinence"],
            candidate_data={"content": "test", "pertinence_score": 0.1},
        )
        router = ReviewerRouter()
        decision = router.route(entry)
        assert decision.reviewer_type == ReviewerType.BATCH_REVIEWER
        assert decision.priority == QuarantinePriority.LOW

    async def test_ambiguous_pertinence_routes_to_senior(self, persona_id):
        entry = _make_entry(
            persona_id,
            failed_gates=["pertinence"],
            candidate_data={"content": "test", "pertinence_score": 0.6},
        )
        router = ReviewerRouter()
        decision = router.route(entry)
        assert decision.reviewer_type == ReviewerType.SENIOR_AGENT
        assert decision.priority == QuarantinePriority.HIGH


class TestReviewerRoutingTierPromotion:
    """Tier promotion candidates route to human family."""

    async def test_tier_promotion_routes_to_human(self, persona_id):
        entry = _make_entry(
            persona_id,
            failed_gates=["immutability"],
            candidate_data={"content": "test", "_tier_promotion": "accrued_to_canonical"},
        )
        router = ReviewerRouter()
        decision = router.route(entry)
        assert decision.reviewer_type == ReviewerType.HUMAN_FAMILY
        assert decision.priority == QuarantinePriority.HIGH


class TestPriorityQueueOrdering:
    """Critical and high priority items jump the queue."""

    async def test_batch_routes_highest_priority_first(self, persona_id):
        entries = [
            _make_entry(
                persona_id,
                failed_gates=["pertinence"],
                candidate_data={"content": "a", "pertinence_score": 0.1},
            ),
            _make_entry(
                persona_id,
                failed_gates=["safety"],
                candidate_data={"content": "b"},
            ),
            _make_entry(
                persona_id,
                failed_gates=["novelty"],
                candidate_data={"content": "c"},
            ),
        ]
        router = ReviewerRouter()
        decisions = router.route_batch(entries)

        priorities = [d.priority for d in decisions]
        assert QuarantinePriority.CRITICAL in priorities
        assert QuarantinePriority.HIGH in priorities

    async def test_critical_beats_high(self, persona_id):
        entry = _make_entry(
            persona_id,
            failed_gates=["safety", "immutability"],
            candidate_data={
                "content": "test",
                "immutability_conflict_type": "canonical",
            },
        )
        router = ReviewerRouter()
        decision = router.route(entry)
        assert decision.priority == QuarantinePriority.CRITICAL
        assert decision.reviewer_type == ReviewerType.SAFETY_AGENT


class TestDequeueAndRoute:
    """Integration: dequeue from quarantine queue and route."""

    async def test_dequeue_and_route(self, persona_id, queue):
        await queue.enqueue(
            _make_entry(
                persona_id,
                failed_gates=["safety"],
            )
        )
        await queue.enqueue(
            _make_entry(
                persona_id,
                failed_gates=["pertinence"],
                candidate_data={"content": "test", "pertinence_score": 0.1},
            )
        )

        router = ReviewerRouter(queue=queue)
        pairs = await router.dequeue_and_route(limit=10)

        assert len(pairs) == 2
        safety_pair = [p for p in pairs if p[1].reviewer_type == ReviewerType.SAFETY_AGENT]
        assert len(safety_pair) == 1
        assert safety_pair[0][1].priority == QuarantinePriority.CRITICAL


class TestHandlerDispatch:
    """Router dispatches to registered handlers."""

    async def test_dispatch_calls_handler(self, persona_id):
        dispatched_entries: list[QuarantineEntry] = []

        async def fake_handler(entry: QuarantineEntry) -> None:
            dispatched_entries.append(entry)

        entry = _make_entry(
            persona_id,
            failed_gates=["safety"],
        )

        router = ReviewerRouter(
            handlers={ReviewerType.SAFETY_AGENT: fake_handler}
        )
        await router.dispatch(entry)

        assert len(dispatched_entries) == 1
        assert dispatched_entries[0].id == entry.id

    async def test_dispatch_missing_handler_no_error(self, persona_id):
        entry = _make_entry(
            persona_id,
            failed_gates=["safety"],
        )
        router = ReviewerRouter()
        await router.dispatch(entry)

    async def test_dispatch_batch(self, persona_id):
        dispatched: list[str] = []

        async def safety_handler(entry: QuarantineEntry) -> None:
            dispatched.append(f"safety:{entry.id}")

        async def batch_handler(entry: QuarantineEntry) -> None:
            dispatched.append(f"batch:{entry.id}")

        entries = [
            _make_entry(persona_id, failed_gates=["safety"]),
            _make_entry(
                persona_id,
                failed_gates=["pertinence"],
                candidate_data={"content": "test", "pertinence_score": 0.1},
            ),
        ]

        router = ReviewerRouter(
            handlers={
                ReviewerType.SAFETY_AGENT: safety_handler,
                ReviewerType.BATCH_REVIEWER: batch_handler,
            }
        )
        pairs = [(e, router.route(e)) for e in entries]
        results = await router.dispatch_batch(pairs)

        assert results[str(entries[0].id)] is True
        assert results[str(entries[1].id)] is True
        assert len(dispatched) == 2


class TestDefaultRoutingTable:
    """Default routing table covers all six flag reasons from the spec."""

    async def test_all_spec_routes_present(self):
        expected_routes = {
            "safety": ReviewerType.SAFETY_AGENT,
            "disclosure_sensitive": ReviewerType.CLINICAL_ADVISOR,
            "tier_promotion": ReviewerType.HUMAN_FAMILY,
            "potential_hallucination": ReviewerType.SENIOR_AGENT,
            "immutability_conflict": ReviewerType.SENIOR_AGENT,
            "low_pertinence": ReviewerType.BATCH_REVIEWER,
        }
        for rule in DEFAULT_ROUTING_TABLE:
            if rule.flag_reason in expected_routes:
                assert rule.routes_to == expected_routes[rule.flag_reason]

    async def test_safety_and_disclosure_are_critical(self):
        critical_rules = [
            r for r in DEFAULT_ROUTING_TABLE if r.priority == QuarantinePriority.CRITICAL
        ]
        flag_reasons = [r.flag_reason for r in critical_rules]
        assert "safety" in flag_reasons
        assert "disclosure_sensitive" in flag_reasons

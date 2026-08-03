from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from huible.ingestion.quarantine import QuarantineQueue
from huible.memory.protocol import (
    QuarantineEntry,
    QuarantinePriority,
)

logger = logging.getLogger(__name__)


class ReviewerType(StrEnum):
    SAFETY_AGENT = "safety_agent"
    CLINICAL_ADVISOR = "clinical_advisor"
    HUMAN_FAMILY = "human_family"
    SENIOR_AGENT = "senior_agent"
    BATCH_REVIEWER = "batch_reviewer"


@dataclass(slots=True, frozen=True)
class RoutingRule:
    flag_reason: str
    routes_to: ReviewerType
    priority: QuarantinePriority
    description: str = ""


@dataclass(slots=True, frozen=True)
class RoutingDecision:
    entry_id: UUID
    reviewer_type: ReviewerType
    priority: QuarantinePriority
    reasons: list[str] = field(default_factory=list)


DEFAULT_ROUTING_TABLE: list[RoutingRule] = [
    RoutingRule(
        flag_reason="safety",
        routes_to=ReviewerType.SAFETY_AGENT,
        priority=QuarantinePriority.CRITICAL,
        description="Safety ambiguous — needs classification before human sees it",
    ),
    RoutingRule(
        flag_reason="disclosure_sensitive",
        routes_to=ReviewerType.CLINICAL_ADVISOR,
        priority=QuarantinePriority.CRITICAL,
        description="Disclosure sensitive — relationship/family context requires clinical judgment",
    ),
    RoutingRule(
        flag_reason="tier_promotion",
        routes_to=ReviewerType.HUMAN_FAMILY,
        priority=QuarantinePriority.HIGH,
        description="Tier promotion candidate — only family decides what becomes canonical",
    ),
    RoutingRule(
        flag_reason="potential_hallucination",
        routes_to=ReviewerType.SENIOR_AGENT,
        priority=QuarantinePriority.HIGH,
        description="Potential hallucination — re-extraction with broader context",
    ),
    RoutingRule(
        flag_reason="immutability_conflict",
        routes_to=ReviewerType.SENIOR_AGENT,
        priority=QuarantinePriority.HIGH,
        description="Immutability conflict — resolve contradiction against canonical facts",
    ),
    RoutingRule(
        flag_reason="low_pertinence",
        routes_to=ReviewerType.BATCH_REVIEWER,
        priority=QuarantinePriority.LOW,
        description="Low pertinence — don't waste human time on noise",
    ),
]


AdjudicationHandler = Callable[[QuarantineEntry, ReviewerType], Coroutine[Any, Any, None]]


@runtime_checkable
class ReviewerHandler(Protocol):
    async def review(self, entry: QuarantineEntry) -> None: ...


class ReviewerRouter:
    def __init__(
        self,
        routing_table: list[RoutingRule] | None = None,
        handlers: dict[ReviewerType, ReviewerHandler | AdjudicationHandler] | None = None,
        queue: QuarantineQueue | None = None,
    ) -> None:
        self._table = list(routing_table or DEFAULT_ROUTING_TABLE)
        self._handlers: dict[ReviewerType, ReviewerHandler | AdjudicationHandler] = dict(
            handlers or {}
        )
        self._queue = queue

    @property
    def routing_table(self) -> list[RoutingRule]:
        return list(self._table)

    def route(self, entry: QuarantineEntry) -> RoutingDecision:
        flag_map = _build_flag_map(entry)
        decisions = self._evaluate_rules(entry, flag_map)
        decision = _pick_highest_priority(decisions, entry.id)
        logger.info(
            "Routed quarantine entry %s to %s (priority=%s, reasons=%s)",
            entry.id,
            decision.reviewer_type.value,
            decision.priority.value,
            decision.reasons,
        )
        return decision

    def route_batch(
        self, entries: list[QuarantineEntry]
    ) -> list[RoutingDecision]:
        return [self.route(entry) for entry in entries]

    async def dequeue_and_route(
        self,
        limit: int = 50,
    ) -> list[tuple[QuarantineEntry, RoutingDecision]]:
        if self._queue is None:
            raise RuntimeError("Router requires a QuarantineQueue to dequeue")

        pending = await self._queue.dequeue(limit=limit)
        results: list[tuple[QuarantineEntry, RoutingDecision]] = []
        for entry in pending:
            decision = self.route(entry)
            results.append((entry, decision))
        return results

    async def dispatch(
        self, entry: QuarantineEntry, decision: RoutingDecision | None = None
    ) -> None:
        if decision is None:
            decision = self.route(entry)

        handler = self._handlers.get(decision.reviewer_type)
        if handler is None:
            logger.warning(
                "No handler registered for reviewer type %s — entry %s remains pending",
                decision.reviewer_type.value,
                entry.id,
            )
            return

        await handler(entry)

    async def dispatch_batch(
        self, pairs: list[tuple[QuarantineEntry, RoutingDecision]]
    ) -> dict[str, bool]:
        dispatched: dict[str, bool] = {}
        for entry, decision in pairs:
            try:
                await self.dispatch(entry, decision)
                dispatched[str(entry.id)] = True
            except Exception:
                logger.exception(
                    "Failed to dispatch entry %s to %s",
                    entry.id,
                    decision.reviewer_type.value,
                )
                dispatched[str(entry.id)] = False
        return dispatched

    def register_handler(
        self,
        reviewer_type: ReviewerType,
        handler: ReviewerHandler | AdjudicationHandler,
    ) -> None:
        self._handlers[reviewer_type] = handler

    def add_rule(self, rule: RoutingRule) -> None:
        self._table.append(rule)

    def _evaluate_rules(
        self,
        entry: QuarantineEntry,
        flag_map: dict[str, str | None],
    ) -> list[RoutingDecision]:
        decisions: list[RoutingDecision] = []
        for rule in self._table:
            flag_value = flag_map.get(rule.flag_reason)
            if flag_value is not None:
                decisions.append(
                    RoutingDecision(
                        entry_id=entry.id,
                        reviewer_type=rule.routes_to,
                        priority=rule.priority,
                        reasons=[flag_value],
                    )
                )
        if not decisions:
            fallback = _fallback_decision(entry)
            if fallback is not None:
                decisions.append(fallback)
        return decisions


def _build_flag_map(entry: QuarantineEntry) -> dict[str, str | None]:
    flag_map: dict[str, str | None] = {}
    candidate = entry.candidate_data

    for gate in entry.failed_gates:
        gate_lower = gate.lower()
        if gate_lower == "safety":
            flag_map["safety"] = candidate.get("safety_flag_reason", "ambiguous_safety")
        elif gate_lower == "immutability":
            conflict = candidate.get("immutability_conflict_type")
            if conflict == "disclosure":
                flag_map["disclosure_sensitive"] = "disclosure conflict detected"
            else:
                flag_map["immutability_conflict"] = "canonical conflict detected"
        elif gate_lower == "novelty":
            flag_map["potential_hallucination"] = "novelty gate ambiguous"
        elif gate_lower == "pertinence":
            score = candidate.get("pertinence_score")
            if isinstance(score, (int, float)) and score < 0.3:
                flag_map["low_pertinence"] = "low pertinence score"
            else:
                flag_map["potential_hallucination"] = "pertinence gate ambiguous"

    tier_promotion = candidate.get("_tier_promotion")
    if tier_promotion:
        flag_map["tier_promotion"] = str(tier_promotion)

    return flag_map


_PRIORITY_ORDER: list[QuarantinePriority] = [
    QuarantinePriority.CRITICAL,
    QuarantinePriority.HIGH,
    QuarantinePriority.MEDIUM,
    QuarantinePriority.LOW,
]


def _pick_highest_priority(
    decisions: list[RoutingDecision], entry_id: UUID
) -> RoutingDecision:
    if not decisions:
        return RoutingDecision(
            entry_id=entry_id,
            reviewer_type=ReviewerType.BATCH_REVIEWER,
            priority=QuarantinePriority.LOW,
            reasons=["no matching rule — default to batch"],
        )

    best = decisions[0]
    for d in decisions[1:]:
        if _PRIORITY_ORDER.index(d.priority) < _PRIORITY_ORDER.index(best.priority) or (
            d.priority == best.priority
            and len(d.reasons) > len(best.reasons)
        ):
            best = d
    return best


def _fallback_decision(entry: QuarantineEntry) -> RoutingDecision | None:
    if not entry.failed_gates:
        return None
    return RoutingDecision(
        entry_id=entry.id,
        reviewer_type=ReviewerType.BATCH_REVIEWER,
        priority=QuarantinePriority.MEDIUM,
        reasons=entry.failed_gates,
    )

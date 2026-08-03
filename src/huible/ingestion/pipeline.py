from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from huible.ingestion import Gate, GateContext, GateOutcome, GateResult
from huible.ingestion.gate_dedup import DeduplicationGate
from huible.ingestion.gate_immutability import ImmutabilityGate
from huible.ingestion.gate_novelty import NoveltyGate
from huible.ingestion.gate_pertinence import PertinenceGate
from huible.ingestion.gate_safety import SafetyGate
from huible.memory.protocol import (
    MemoryNode,
    MemoryTier,
    QuarantineEntry,
    QuarantinePriority,
    QuarantineStatus,
    SourceType,
)

logger = logging.getLogger(__name__)

_GATE_PRIORITY_MAP: dict[str, QuarantinePriority] = {
    "safety": QuarantinePriority.CRITICAL,
    "immutability": QuarantinePriority.HIGH,
    "novelty": QuarantinePriority.MEDIUM,
    "pertinence": QuarantinePriority.LOW,
}

_DEFAULT_GATES: list[tuple[str, type]] = [
    ("safety", SafetyGate),
    ("deduplication", DeduplicationGate),
    ("novelty", NoveltyGate),
    ("immutability", ImmutabilityGate),
    ("pertinence", PertinenceGate),
]

_EARLY_REJECT_GATES = {"safety", "deduplication"}


@dataclass(slots=True)
class AuditEntry:
    candidate_id: str
    gate_name: str
    outcome: str
    reason: str
    score: float = 0.0
    details: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(slots=True)
class PipelineResult:
    accepted: bool = False
    rejected: bool = False
    quarantined: bool = False
    gate: str | None = None
    reason: str | None = None
    memory: MemoryNode | None = None
    quarantine_entry: QuarantineEntry | None = None
    audit_trail: list[AuditEntry] = field(default_factory=list)
    gate_results: dict[str, GateResult] = field(default_factory=dict)


class IngestionPipeline:
    def __init__(
        self,
        gates: list[Gate] | None = None,
        config: dict | None = None,
    ) -> None:
        if gates is not None:
            self._gates = list(gates)
        else:
            cfg = config or {}
            self._gates = [
                SafetyGate(config=cfg.get("safety", {})),
                DeduplicationGate(config=cfg.get("deduplication", {})),
                NoveltyGate(config=cfg.get("novelty", {})),
                ImmutabilityGate(config=cfg.get("immutability", {})),
                PertinenceGate(config=cfg.get("pertinence", {})),
            ]

    @property
    def gates(self) -> list[Gate]:
        return list(self._gates)

    async def process(
        self,
        candidate: dict,
        persona_id,
        backend=None,
        tier2_model=None,
        config: dict | None = None,
    ) -> PipelineResult:
        context = GateContext(
            persona_id=persona_id,
            backend=backend,
            tier2_model=tier2_model,
            config=config,
        )

        audit_trail: list[AuditEntry] = []
        gate_results: dict[str, GateResult] = {}
        candidate_id = candidate.get("id", str(uuid4()))

        for gate in self._gates:
            result = await gate.evaluate(candidate, context)
            gate_results[gate.name] = result

            audit = AuditEntry(
                candidate_id=candidate_id,
                gate_name=gate.name,
                outcome=result.outcome.value,
                reason=result.reason,
                score=result.score,
                details=result.details,
            )
            audit_trail.append(audit)

            if result.outcome == GateOutcome.FAIL and gate.name in _EARLY_REJECT_GATES:
                return PipelineResult(
                    rejected=True,
                    gate=gate.name,
                    reason=result.reason,
                    audit_trail=audit_trail,
                    gate_results=gate_results,
                )

        ambiguous_gates = [
            name
            for name, result in gate_results.items()
            if result.outcome == GateOutcome.AMBIGUOUS
        ]

        if not ambiguous_gates:
            memory = _build_memory_node(candidate, persona_id, audit_trail)
            return PipelineResult(
                accepted=True,
                memory=memory,
                audit_trail=audit_trail,
                gate_results=gate_results,
            )

        priority = _compute_quarantine_priority(ambiguous_gates)
        quarantine_entry = _build_quarantine_entry(
            candidate, persona_id, ambiguous_gates, priority, audit_trail
        )

        return PipelineResult(
            quarantined=True,
            gate=", ".join(ambiguous_gates),
            reason=f"Ambiguous gates: {', '.join(ambiguous_gates)}",
            quarantine_entry=quarantine_entry,
            audit_trail=audit_trail,
            gate_results=gate_results,
        )


def _compute_quarantine_priority(ambiguous_gates: list[str]) -> QuarantinePriority:
    if not ambiguous_gates:
        return QuarantinePriority.LOW
    priorities = [_GATE_PRIORITY_MAP.get(g, QuarantinePriority.MEDIUM) for g in ambiguous_gates]
    order = [
        QuarantinePriority.CRITICAL,
        QuarantinePriority.HIGH,
        QuarantinePriority.MEDIUM,
        QuarantinePriority.LOW,
    ]
    for p in order:
        if p in priorities:
            return p
    return QuarantinePriority.MEDIUM


def _build_memory_node(
    candidate: dict,
    persona_id,
    audit_trail: list[AuditEntry],
) -> MemoryNode:

    from huible.memory.protocol import ContentType, DisclosureScope

    return MemoryNode(
        id=uuid4(),
        persona_id=persona_id,
        tier=MemoryTier(candidate.get("tier", "accrued")),
        content=candidate["content"],
        content_type=ContentType(candidate.get("content_type", "narrative")),
        embedding_content=candidate.get("embedding_content"),
        embedding_sensory=candidate.get("embedding_sensory"),
        embedding_affect=candidate.get("embedding_affect"),
        valid_from=candidate.get("valid_from"),
        valid_to=candidate.get("valid_to"),
        memory_date=candidate.get("memory_date"),
        source_type=SourceType(candidate.get("source_type", "extraction")),
        source_ref=candidate.get("source_ref", {}),
        disclosure_scope=DisclosureScope(candidate.get("disclosure_scope", "family")),
            metadata={"audit_trail": [dataclasses.asdict(a) for a in audit_trail]},
    )


def _build_quarantine_entry(
    candidate: dict,
    persona_id,
    ambiguous_gates: list[str],
    priority: QuarantinePriority,
    audit_trail: list[AuditEntry],
) -> QuarantineEntry:
    return QuarantineEntry(
        id=uuid4(),
        candidate_data={
            **candidate,
                "_audit_trail": [dataclasses.asdict(a) for a in audit_trail],
        },
        persona_id=persona_id,
        failed_gates=ambiguous_gates,
        priority=priority,
        status=QuarantineStatus.PENDING,
    )

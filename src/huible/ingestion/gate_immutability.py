from __future__ import annotations

import logging

from huible.ingestion import Gate, GateContext, GateOutcome, GateResult
from huible.memory.protocol import MemoryTier

logger = logging.getLogger(__name__)


class ImmutabilityGate(Gate):
    name = "immutability"

    def __init__(self, config: dict | None = None) -> None:
        self._config = config or {}

    async def evaluate(self, candidate: dict, context: GateContext) -> GateResult:
        backend = context.backend
        if backend is None:
            return GateResult(
                outcome=GateOutcome.PASS,
                reason="No backend available, skipping immutability check",
            )

        tier2 = context.tier2_model or self._tier2_model
        embedding = candidate.get("embedding_content")

        canonical_results = []
        if embedding:
            canonical_results = await backend.search_by_content(
                persona_id=context.persona_id,
                query_embedding=embedding,
                top_k=5,
            )
            canonical_results = [
                r for r in canonical_results if r.node.tier == MemoryTier.CANONICAL
            ]

        details = {
            "canonical_memories_checked": len(canonical_results),
            "canonical_matches": [],
        }

        if not canonical_results:
            return GateResult(
                outcome=GateOutcome.PASS,
                reason="No canonical memories to conflict with",
                details=details,
            )

        if tier2 is not None:
            try:
                canonical_content = [
                    {"id": str(r.node.id), "content": r.node.content}
                    for r in canonical_results
                ]
                tier2_result = await tier2(
                    "immutability",
                    {**candidate, "canonical_memories": canonical_content},
                    context,
                )
                if tier2_result is not None:
                    details["tier2"] = tier2_result
                    if tier2_result.get("outcome") == "fail":
                        return GateResult(
                            outcome=GateOutcome.FAIL,
                            reason=tier2_result.get(
                                "reason",
                                "Conflicts with canonical memory",
                            ),
                            details=details,
                        )
                    if tier2_result.get("outcome") == "pass":
                        return GateResult(
                            outcome=GateOutcome.PASS,
                            reason="No canonical conflict (Tier 2 confirmed)",
                            details=details,
                        )
                    return GateResult(
                        outcome=GateOutcome.AMBIGUOUS,
                        reason=tier2_result.get("reason", "Possible canonical conflict"),
                        details=details,
                    )
            except Exception:
                logger.warning("Tier 2 model call failed for immutability gate", exc_info=True)

        return GateResult(
            outcome=GateOutcome.AMBIGUOUS,
            reason=(
                f"{len(canonical_results)} canonical memories may conflict "
                "— Tier 2 unavailable for adjudication"
            ),
            details=details,
        )

    @property
    def _tier2_model(self):
        return None

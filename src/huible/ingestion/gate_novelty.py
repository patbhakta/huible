from __future__ import annotations

import logging

from huible.ingestion import Gate, GateContext, GateOutcome, GateResult

logger = logging.getLogger(__name__)


class NoveltyGate(Gate):
    name = "novelty"

    def __init__(self, min_connections: int = 1, config: dict | None = None) -> None:
        self._min_connections = min_connections
        self._config = config or {}

    async def evaluate(self, candidate: dict, context: GateContext) -> GateResult:
        backend = context.backend
        if backend is None:
            return GateResult(
                outcome=GateOutcome.PASS,
                reason="No backend available, skipping novelty check",
            )

        embedding = candidate.get("embedding_content")
        tier2 = context.tier2_model

        search_results = []
        if embedding:
            search_results = await backend.search_by_content(
                persona_id=context.persona_id,
                query_embedding=embedding,
                top_k=10,
            )

        if not search_results:
            if context.persona_id:
                active = await backend.get_active_memories(context.persona_id, limit=5)
                if active:
                    return GateResult(
                        outcome=GateOutcome.AMBIGUOUS,
                        reason=(
                            "No similar memories found but graph is "
                            "populated — may be orphan noise"
                        ),
                        details={"graph_size_estimate": len(active), "similar_found": 0},
                    )
            return GateResult(
                outcome=GateOutcome.AMBIGUOUS,
                reason="Empty graph — first memory requires seeding approval",
                details={"graph_size_estimate": 0, "similar_found": 0},
            )

        potential_connections: list[dict] = []
        for result in search_results[:5]:
            if result.score < 0.5:
                continue
            edges = await backend.get_edges(result.node.id)
            if edges:
                potential_connections.append({
                    "node_id": str(result.node.id),
                    "edge_count": len(edges),
                    "similarity": result.score,
                })

        details = {
            "similar_found": len(search_results),
            "potential_connections": potential_connections,
            "min_connections": self._min_connections,
        }

        if len(potential_connections) >= self._min_connections:
            return GateResult(
                outcome=GateOutcome.PASS,
                reason=f"Graph connectivity confirmed ({len(potential_connections)} connections)",
                details=details,
            )

        if tier2 is not None:
            try:
                tier2_result = await tier2("novelty", candidate, context)
                if tier2_result is not None:
                    if tier2_result.get("outcome") == "pass":
                        return GateResult(
                            outcome=GateOutcome.PASS,
                            reason=(
                                f"Novelty confirmed by Tier 2 "
                                f"({len(potential_connections)} connections)"
                            ),
                            details={**details, "tier2": tier2_result},
                        )
                    if tier2_result.get("outcome") == "fail":
                        return GateResult(
                            outcome=GateOutcome.FAIL,
                            reason=tier2_result.get("reason", "No novelty (Tier 2)"),
                            details={**details, "tier2": tier2_result},
                        )
                    details["tier2"] = tier2_result
            except Exception:
                logger.warning("Tier 2 model call failed for novelty gate", exc_info=True)

        return GateResult(
            outcome=GateOutcome.AMBIGUOUS,
            reason=(
                f"Uncertain graph connectivity "
                f"({len(potential_connections)}/{self._min_connections})"
            ),
            details=details,
        )

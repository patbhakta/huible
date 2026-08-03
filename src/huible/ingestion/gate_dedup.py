from __future__ import annotations

import math

from huible.ingestion import Gate, GateContext, GateOutcome, GateResult


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


class DeduplicationGate(Gate):
    name = "deduplication"

    def __init__(self, threshold: float = 0.92, config: dict | None = None) -> None:
        self._threshold = threshold
        self._config = config or {}
        self._top_k = self._config.get("top_k", 20)

    async def evaluate(self, candidate: dict, context: GateContext) -> GateResult:
        backend = context.backend
        if backend is None:
            return GateResult(
                outcome=GateOutcome.PASS,
                reason="No backend available, skipping dedup check",
            )

        embedding = candidate.get("embedding_content")
        if not embedding:
            return GateResult(
                outcome=GateOutcome.AMBIGUOUS,
                reason="No embedding provided for dedup check",
            )

        results = await backend.search_by_content(
            persona_id=context.persona_id,
            query_embedding=embedding,
            top_k=self._top_k,
        )

        best_match: float = 0.0
        best_node_id = None

        for search_result in results:
            sim = _cosine_similarity(embedding, search_result.node.embedding_content or [])
            if sim > best_match:
                best_match = sim
                best_node_id = str(search_result.node.id)

        details = {
            "best_similarity": best_match,
            "best_match_node": best_node_id,
            "threshold": self._threshold,
            "candidates_checked": len(results),
        }

        if best_match >= self._threshold:
            return GateResult(
                outcome=GateOutcome.FAIL,
                reason=f"Near-duplicate detected (similarity={best_match:.4f})",
                score=best_match,
                details=details,
            )

        if best_match >= self._threshold - 0.05:
            return GateResult(
                outcome=GateOutcome.AMBIGUOUS,
                reason=f"Borderline similarity (similarity={best_match:.4f})",
                score=best_match,
                details=details,
            )

        return GateResult(
            outcome=GateOutcome.PASS,
            reason=f"No duplicates found (best={best_match:.4f})",
            score=best_match,
            details=details,
        )

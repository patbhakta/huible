from __future__ import annotations

import logging

from huible.ingestion import Gate, GateContext, GateOutcome, GateResult

logger = logging.getLogger(__name__)

_CONTENT_TYPES_THAT_GROW_PERSONA = {"narrative", "fact", "sensory", "relationship", "preference"}


class PertinenceGate(Gate):
    name = "pertinence"

    def __init__(self, min_score: float = 0.3, config: dict | None = None) -> None:
        self._min_score = min_score
        self._config = config or {}

    async def evaluate(self, candidate: dict, context: GateContext) -> GateResult:
        content = candidate.get("content", "")
        content_type = candidate.get("content_type", "")

        if not content or len(content.strip()) < 3:
            return GateResult(
                outcome=GateOutcome.FAIL,
                reason="Insufficient content for memory creation",
                details={"content_length": len(content), "content_type": content_type},
            )

        details: dict = {
            "content_type": content_type,
            "content_length": len(content),
        }

        if content_type and content_type.lower() not in _CONTENT_TYPES_THAT_GROW_PERSONA:
            return GateResult(
                outcome=GateOutcome.AMBIGUOUS,
                reason=f"Unknown content type '{content_type}' — may not grow persona",
                details=details,
            )

        tier2 = context.tier2_model
        if tier2 is not None:
            try:
                tier2_result = await tier2("pertinence", candidate, context)
                if tier2_result is not None:
                    details["tier2"] = tier2_result
                    score = tier2_result.get("score", 0.0)

                    if tier2_result.get("outcome") == "fail" or score < self._min_score:
                        return GateResult(
                            outcome=GateOutcome.AMBIGUOUS,
                            reason=f"Low pertinence score ({score:.2f})",
                            score=score,
                            details=details,
                        )

                    if tier2_result.get("outcome") == "pass" and score >= self._min_score:
                        return GateResult(
                            outcome=GateOutcome.PASS,
                            reason=f"Persona-relevant (score={score:.2f})",
                            score=score,
                            details=details,
                        )

                    return GateResult(
                        outcome=GateOutcome.AMBIGUOUS,
                        reason=f"Uncertain pertinence (score={score:.2f})",
                        score=score,
                        details=details,
                    )
            except Exception:
                logger.warning("Tier 2 model call failed for pertinence gate", exc_info=True)

        word_count = len(content.split())
        if word_count >= 5:
            return GateResult(
                outcome=GateOutcome.PASS,
                reason=f"Content appears substantive ({word_count} words)",
                details=details,
            )

        return GateResult(
            outcome=GateOutcome.AMBIGUOUS,
            reason=f"Very short content ({word_count} words) — uncertain pertinence",
            details=details,
        )

"""Verification gate: never report an unverified fact as truth.

This is the layer that would have prevented the Kestra disaster.
Before reporting any stateful fact to the user, check:
1. Has it been verified recently?
2. Is the confidence above the reporting threshold?
3. Has it been contradicted?

If any check fails, flag the memory instead of reporting it as fact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any
from uuid import UUID

from huible.mnemosyne.trust import ConfidenceScorer, SourceReliability, TrustTier


class VerificationStatus(StrEnum):
    """Result of verification gate check."""

    VERIFIED = "verified"  # Can report as fact
    STALE = "stale"  # Needs re-verification before reporting
    UNVERIFIED = "unverified"  # Never verified — don't report as fact
    CONTRADICTED = "contradicted"  # Has unresolved contradictions
    LOW_CONFIDENCE = "low_confidence"  # Below reporting threshold


@dataclass(slots=True)
class VerificationResult:
    """Result of running a memory through the verification gate."""

    status: VerificationStatus
    memory_id: UUID | None = None
    confidence: float = 0.0
    trust_tier: TrustTier = TrustTier.UNVERIFIED
    reason: str = ""
    should_report: bool = False
    should_flag: bool = False
    needs_recheck: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class VerificationTTL:
    """How long before a stateful memory needs re-verification.

    Different categories of memory have different decay rates.
    Configs change. Preferences are stable. Facts about people are very stable.
    """

    # Pre-configured TTLs by category (class-level constants)
    SYSTEM_CONFIG: float = 6.0
    SERVICE_STATE: float = 1.0
    USER_PREFERENCE: float = 720.0  # 30 days
    RELATIONSHIP: float = 2160.0  # 90 days
    IMMUTABLE: float = -1.0  # negative = never


class VerificationGate:
    """Gate that checks memory before it can be reported to the user.

    Usage:
        gate = VerificationGate()
        result = gate.check(memory_id, scorer, category="system_config")

        if result.should_report:
            # Safe to report as fact
        elif result.should_flag:
            # Must flag as uncertain: "I believe X but haven't verified it"
    """

    def __init__(self, reporting_threshold: float = 0.70) -> None:
        self.reporting_threshold = reporting_threshold

    def check(
        self,
        memory_id: UUID,
        scorer: ConfidenceScorer,
        category: str = "general",
        ttl_override: float | None = None,
    ) -> VerificationResult:
        """Check if a memory can be reported as verified fact.

        Args:
            memory_id: The memory being checked
            scorer: The confidence scorer for this memory
            category: Memory category (determines TTL)
            ttl_override: Override the TTL for this check

        Returns:
            VerificationResult with status and flags
        """
        confidence = scorer.confidence
        tier = scorer.trust_tier

        # 1. Check if contradicted
        if scorer.contradiction_count > 0 and scorer.contradiction_count >= scorer.corroboration_count:
            return VerificationResult(
                status=VerificationStatus.CONTRADICTED,
                memory_id=memory_id,
                confidence=confidence,
                trust_tier=tier,
                reason=f"Has {scorer.contradiction_count} contradictions vs "
                       f"{scorer.corroboration_count} corroborations",
                should_report=False,
                should_flag=True,
            )

        # 2. Check if never verified (before confidence check, so we get the right reason)
        if scorer.verification_count == 0 and scorer.source not in (
            SourceReliability.HUMAN_DIRECT,
            SourceReliability.TOOL_OUTPUT,
        ):
            return VerificationResult(
                status=VerificationStatus.UNVERIFIED,
                memory_id=memory_id,
                confidence=confidence,
                trust_tier=tier,
                reason="Never independently verified",
                should_report=False,
                should_flag=True,
                needs_recheck=True,
            )

        # 3. Check if stateful memory needs re-verification (before confidence,
        #    because a stale verified memory might still have residual confidence)
        if scorer.is_stateful:
            ttl = ttl_override or self._get_ttl(category)
            if ttl > 0 and scorer.needs_verification(ttl_hours=ttl):
                return VerificationResult(
                    status=VerificationStatus.STALE,
                    memory_id=memory_id,
                    confidence=confidence,
                    trust_tier=tier,
                    reason=f"Last verified too long ago (TTL: {ttl}h for {category})",
                    should_report=False,
                    should_flag=True,
                    needs_recheck=True,
                )

        # 4. Check confidence threshold
        if confidence < self.reporting_threshold:
            return VerificationResult(
                status=VerificationStatus.LOW_CONFIDENCE,
                memory_id=memory_id,
                confidence=confidence,
                trust_tier=tier,
                reason=f"Confidence {confidence:.2f} below threshold {self.reporting_threshold}",
                should_report=False,
                should_flag=True,
            )

        # 5. All checks passed
        return VerificationResult(
            status=VerificationStatus.VERIFIED,
            memory_id=memory_id,
            confidence=confidence,
            trust_tier=tier,
            reason="All verification checks passed",
            should_report=True,
            should_flag=False,
        )

    def _get_ttl(self, category: str) -> float:
        """Get TTL in hours for a memory category."""
        ttl_map = {
            "system_config": VerificationTTL.SYSTEM_CONFIG,
            "service_state": VerificationTTL.SERVICE_STATE,
            "user_preference": VerificationTTL.USER_PREFERENCE,
            "relationship": VerificationTTL.RELATIONSHIP,
            "immutable": VerificationTTL.IMMUTABLE,
        }
        return ttl_map.get(category, 24.0) or 24.0  # default: 24 hours

    def batch_check(
        self,
        memories: list[tuple[UUID, ConfidenceScorer, str]],
    ) -> list[VerificationResult]:
        """Check multiple memories at once.

        Args:
            memories: List of (memory_id, scorer, category) tuples

        Returns:
            List of verification results
        """
        return [self.check(mid, scorer, cat) for mid, scorer, cat in memories]

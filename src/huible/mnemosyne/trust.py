"""Trust tiers and Bayesian confidence scoring.

No existing memory system has probabilistic confidence per fact.
This is the novel contribution — facts earn their confidence through
corroboration and lose it through contradiction.

Prior is set by source reliability. Updated by evidence:
    - Each corroboration (independent source confirms) → confidence increases
    - Each contradiction (independent source conflicts) → confidence decreases
    - Each verification (system check passes) → confidence increases
    - Time decay: unverified facts lose confidence over time
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from math import log


class TrustTier(StrEnum):
    """Trust levels mapped to OKF v0.2 trust model.

    A fact's trust tier determines whether it can be reported as truth
    or must be flagged as uncertain.
    """

    HUMAN_REVIEWED = "human_reviewed"
    MACHINE_VERIFIED = "machine_verified"
    AGENT_INFERRED = "agent_inferred"
    SUBAGENT_REPORTED = "subagent_reported"
    UNVERIFIED = "unverified"


class SourceReliability(StrEnum):
    """Where the memory came from. Sets the Bayesian prior."""

    HUMAN_DIRECT = "human_direct"
    TOOL_OUTPUT = "tool_output"
    AGENT_INFERENCE = "agent_inference"
    SUBAGENT_REPORT = "subagent_report"
    LLM_GENERATED = "llm_generated"


# Prior probabilities: P(fact is true | came from this source)
# Based on empirical observation of failure modes this session
_PRIOR_MAP: dict[SourceReliability, float] = {
    SourceReliability.HUMAN_DIRECT: 0.95,
    SourceReliability.TOOL_OUTPUT: 0.85,
    SourceReliability.AGENT_INFERENCE: 0.55,
    SourceReliability.SUBAGENT_REPORT: 0.35,
    SourceReliability.LLM_GENERATED: 0.45,
}

# Trust tier thresholds: confidence required to reach each tier
_TIER_THRESHOLDS: dict[TrustTier, float] = {
    TrustTier.HUMAN_REVIEWED: 0.90,
    TrustTier.MACHINE_VERIFIED: 0.75,
    TrustTier.AGENT_INFERRED: 0.50,
    TrustTier.SUBAGENT_REPORTED: 0.30,
    TrustTier.UNVERIFIED: 0.0,
}


def _tier_from_confidence(confidence: float) -> TrustTier:
    """Determine trust tier from confidence score."""
    if confidence >= _TIER_THRESHOLDS[TrustTier.HUMAN_REVIEWED]:
        return TrustTier.HUMAN_REVIEWED
    if confidence >= _TIER_THRESHOLDS[TrustTier.MACHINE_VERIFIED]:
        return TrustTier.MACHINE_VERIFIED
    if confidence >= _TIER_THRESHOLDS[TrustTier.AGENT_INFERRED]:
        return TrustTier.AGENT_INFERRED
    if confidence >= _TIER_THRESHOLDS[TrustTier.SUBAGENT_REPORTED]:
        return TrustTier.SUBAGENT_REPORTED
    return TrustTier.UNVERIFIED


@dataclass(slots=True)
class ConfidenceScorer:
    """Bayesian confidence scoring for a single memory.

    Uses a simplified Bayesian update:
        posterior ∝ prior × likelihood

    Each corroboration multiplies the odds by a confirmation factor.
    Each contradiction multiplies the odds by a contradiction factor.
    Time decay reduces confidence for stateful memories.
    """

    source: SourceReliability
    prior: float = field(default=0.0)
    corroboration_count: int = 0
    contradiction_count: int = 0
    verification_count: int = 0
    last_verified: datetime | None = None
    is_stateful: bool = False  # stateful = needs periodic re-verification

    # Evidence strength factors
    CORROBORATION_FACTOR = 1.5  # each independent confirmation multiplies odds
    CONTRADICTION_FACTOR = 0.3  # each contradiction multiplies odds by this
    VERIFICATION_FACTOR = 1.3  # each system check multiplies odds
    DECAY_HALF_LIFE_HOURS = 72.0  # confidence halves every 3 days if unverified

    def __post_init__(self) -> None:
        if self.prior == 0.0:
            self.prior = _PRIOR_MAP.get(self.source, 0.4)

    @property
    def confidence(self) -> float:
        """Current confidence score in [0, 1]."""
        odds = self.prior / (1.0 - self.prior) if self.prior < 1.0 else 1e6

        # Apply evidence updates
        odds *= self.CORROBORATION_FACTOR ** self.corroboration_count
        odds *= self.CONTRADICTION_FACTOR ** self.contradiction_count
        odds *= self.VERIFICATION_FACTOR ** self.verification_count

        # Apply time decay for stateful memories
        if self.is_stateful and self.last_verified:
            hours_since = (datetime.now(timezone.utc) - self.last_verified).total_seconds() / 3600
            if hours_since > 0:
                decay = 2.0 ** (-hours_since / self.DECAY_HALF_LIFE_HOURS)
                odds *= decay

        # Convert back to probability
        return odds / (1.0 + odds)

    @property
    def trust_tier(self) -> TrustTier:
        """Current trust tier based on confidence."""
        return _tier_from_confidence(self.confidence)

    @property
    def can_report_as_fact(self) -> bool:
        """Can this memory be reported to the user as established fact?

        Only machine-verified or human-reviewed memories qualify.
        Everything else must be flagged.
        """
        return self.trust_tier in (TrustTier.HUMAN_REVIEWED, TrustTier.MACHINE_VERIFIED)

    def corroborate(self) -> None:
        """An independent source confirmed this memory."""
        self.corroboration_count += 1

    def contradict(self) -> None:
        """An independent source contradicted this memory."""
        self.contradiction_count += 1

    def verify(self, timestamp: datetime | None = None) -> None:
        """System verification passed (e.g., tool output confirmed the fact)."""
        self.verification_count += 1
        self.last_verified = timestamp or datetime.now(timezone.utc)

    def needs_verification(self, ttl_hours: float = 24.0) -> bool:
        """Does this stateful memory need re-verification?"""
        if not self.is_stateful:
            return False
        if self.last_verified is None:
            return True
        age = (datetime.now(timezone.utc) - self.last_verified).total_seconds() / 3600
        return age > ttl_hours

    def entropy(self) -> float:
        """Information-theoretic entropy of this memory's confidence.

        High entropy = high uncertainty = pruning candidate if also low-access.
        Uses binary entropy: H = -p*log2(p) - (1-p)*log2(1-p)
        """
        p = self.confidence
        if p <= 0.0 or p >= 1.0:
            return 0.0
        return -p * log(p, 2) - (1 - p) * log(1 - p, 2)

    def is_pruning_candidate(
        self,
        access_count: int = 0,
        min_entropy: float = 0.7,
        max_access: int = 0,
    ) -> bool:
        """Should this memory be considered for pruning during consolidation?

        Pruning candidates: high entropy (uncertain) AND low access (unused).
        """
        return self.entropy() >= min_entropy and access_count <= max_access

    def to_dict(self) -> dict:
        """Serialize for storage."""
        return {
            "source": self.source.value,
            "prior": self.prior,
            "corroboration_count": self.corroboration_count,
            "contradiction_count": self.contradiction_count,
            "verification_count": self.verification_count,
            "last_verified": self.last_verified.isoformat() if self.last_verified else None,
            "is_stateful": self.is_stateful,
            "confidence": self.confidence,
            "trust_tier": self.trust_tier.value,
            "can_report_as_fact": self.can_report_as_fact,
            "entropy": self.entropy(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> ConfidenceScorer:
        """Deserialize from storage."""
        scorer = cls(source=SourceReliability(data["source"]), prior=data["prior"])
        scorer.corroboration_count = data.get("corroboration_count", 0)
        scorer.contradiction_count = data.get("contradiction_count", 0)
        scorer.verification_count = data.get("verification_count", 0)
        lv = data.get("last_verified")
        scorer.last_verified = datetime.fromisoformat(lv) if lv else None
        scorer.is_stateful = data.get("is_stateful", False)
        return scorer

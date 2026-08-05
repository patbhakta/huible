"""Mnemosyne: Trust, verification, and comprehension layer for agent memory.

This module implements the Phase 1 verification system from the Mnemosyne design doc.
It adds trust tiers, earned confidence (Bayesian), verification gates, and
contradiction detection on top of Huible's existing memory protocol.

The core principle: never report an unverified fact as truth.
"""

from huible.mnemosyne.trust import TrustTier, ConfidenceScorer, SourceReliability
from huible.mnemosyne.verification import (
    VerificationGate,
    VerificationResult,
    VerificationTTL,
)
from huible.mnemosyne.encoding import DeepEncoder, EncodingResult
from huible.mnemosyne.consolidation import ConsolidationPass

__all__ = [
    "TrustTier",
    "ConfidenceScorer",
    "SourceReliability",
    "VerificationGate",
    "VerificationResult",
    "VerificationTTL",
    "DeepEncoder",
    "EncodingResult",
    "ConsolidationPass",
]

"""Persona-chat runtime clinical guardrails (G1-G4 + G5/G9 framing).

Clinical source: the Clinical Advisor's ``clinical-guardrails`` spec (advisory
issue HU-1407), PM adoption (HU-1408), and Tech-Lead architectural placement
(HU-1409, clinically approved). This package implements the runtime guardrails
required for the Phase-1 phase-gate sign-off recorded in HU-1407 §7.3:

* :mod:`huible.safety.crisis` — G1 synchronous crisis-signal detection + warm
  non-persona escalation, plus the shared affect grade (G3 signal source).
* :mod:`huible.safety.framing` — G2/G3-static/G5/G9 immutable, versioned
  reality-framing block injected into the persona ``system_prompt``.
* :mod:`huible.safety.affect` — G3 dynamic generation-time guard (distress →
  suppress sarcastic/dismissive output).

The chat endpoint (``huible.api.app``) wires these so that a crisis signal
never reaches the persona voice (G1), every persona-voiced turn carries the
immutable framing (G2/G5/G9), distress flattens the voice (G3), and the
response trace records the safety event / memory refs for audit (G4).
"""

from huible.safety.affect import (
    DISTRESS_FALLBACK_RESPONSE,
    SARCASTIC_DISMISSIVE_PATTERNS,
    apply_affect_guard,
    detect_sarcastic_dismissive,
)
from huible.safety.crisis import (
    DEFAULT_CRISIS_RESOURCES,
    CrisisClassifier,
    CrisisResult,
    CrisisSignal,
    DeterministicCrisisClassifier,
    UserAffect,
    build_crisis_response,
    classify_user_message,
)
from huible.safety.framing import (
    DISTRESS_GROUNDING_ADDENDUM,
    FRAMING_VERSION,
    REALITY_FRAMING_BLOCK,
    FramingBlock,
    get_distress_addendum,
    get_framing,
)

__all__ = [
    "DEFAULT_CRISIS_RESOURCES",
    "DISTRESS_FALLBACK_RESPONSE",
    "DISTRESS_GROUNDING_ADDENDUM",
    "FRAMING_VERSION",
    "REALITY_FRAMING_BLOCK",
    "SARCASTIC_DISMISSIVE_PATTERNS",
    "CrisisClassifier",
    "CrisisResult",
    "CrisisSignal",
    "DeterministicCrisisClassifier",
    "FramingBlock",
    "UserAffect",
    "apply_affect_guard",
    "build_crisis_response",
    "classify_user_message",
    "detect_sarcastic_dismissive",
    "get_distress_addendum",
    "get_framing",
]

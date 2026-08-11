"""Persona-chat runtime clinical guardrails (G1-G4 + G5/G9 framing + §7.4.1 handoff).

Clinical source: the Clinical Advisor's ``clinical-guardrails`` spec (advisory
issue HU-1407), PM adoption (HU-1408), and Tech-Lead architectural placement
(HU-1409, clinically approved). This package implements the runtime guardrails
required for the Phase-1 phase-gate sign-off recorded in HU-1407 §7.3, plus the
§7.4.1 human-handoff escalation queue required before any real grieving-user
traffic (HU-1421, §7.4 #1 / §10.1 fail-safe invariants):

* :mod:`huible.safety.crisis` — G1 synchronous crisis-signal detection + warm
  non-persona escalation, plus the shared affect grade (G3 signal source).
* :mod:`huible.safety.framing` — G2/G3-static/G5/G9 immutable, versioned
  reality-framing block injected into the persona ``system_prompt``.
* :mod:`huible.safety.affect` — G3 dynamic generation-time guard (distress →
  suppress sarcastic/dismissive output).
* :mod:`huible.safety.handoff` — §7.4.1 human-handoff (crisis escalation)
  queue: routes a G1-flagged turn into an audited, staffed-responder queue with
  a defined SLA, a non-persona waiting UX, and a fail-safe that degrades to the
  G1 safe response when no human is available (never drops, never persona voice).

The chat endpoint (``huible.api.app``) wires these so that a crisis signal
never reaches the persona voice (G1), every persona-voiced turn carries the
immutable framing (G2/G5/G9), distress flattens the voice (G3), the response
trace records the safety event / memory refs for audit (G4), and a crisis turn
escalates to a real human with a monitored SLA (§7.4.1).
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
from huible.safety.handoff import (
    DEFAULT_HANDOFF_SLA_SECONDS,
    HandoffOutcome,
    HandoffQueue,
    HandoffResult,
    HandoffTicket,
    InMemoryHandoffQueue,
    build_handoff_acknowledgement,
    escalate_to_human,
)

__all__ = [
    "DEFAULT_CRISIS_RESOURCES",
    "DEFAULT_HANDOFF_SLA_SECONDS",
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
    "HandoffOutcome",
    "HandoffQueue",
    "HandoffResult",
    "HandoffTicket",
    "InMemoryHandoffQueue",
    "UserAffect",
    "apply_affect_guard",
    "build_crisis_response",
    "build_handoff_acknowledgement",
    "classify_user_message",
    "detect_sarcastic_dismissive",
    "escalate_to_human",
    "get_distress_addendum",
    "get_framing",
]

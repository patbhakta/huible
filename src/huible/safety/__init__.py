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
* :mod:`huible.safety.alignment` — §7.4.2 generation-time claim->ref alignment
  filter: any factual/identity claim in the persona's reply must be traceable
  to a retrieved reference (or the persona vault), or be suppressed. The
  generation-side backstop for a confabulating generator, complementing the
  G4 retrieval-side provenance firewall.

The chat endpoint (``huible.api.app``) wires these so that a crisis signal
never reaches the persona voice (G1), every persona-voiced turn carries the
immutable framing (G2/G5/G9), distress flattens the voice (G3), the response
trace records the safety event / memory refs for audit (G4), a crisis turn
escalates to a real human with a monitored SLA (§7.4.1), and every
persona-voiced reply is aligned against its retrieved refs so no unsupported
claim reaches a grieving user (§7.4.2).
"""

from huible.safety.affect import (
    DISTRESS_FALLBACK_RESPONSE,
    SARCASTIC_DISMISSIVE_PATTERNS,
    apply_affect_guard,
    detect_sarcastic_dismissive,
)
from huible.safety.alignment import (
    ADVICE_CLAIM_PATTERNS,
    ALIGNMENT_FALLBACK_RESPONSE,
    IDENTITY_CLAIM_PATTERNS,
    AlignmentReport,
    Claim,
    ClaimCategory,
    align_response,
    apply_alignment_guard,
    build_grounding_corpus,
    extract_claims,
    is_grounded,
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
    "ADVICE_CLAIM_PATTERNS",
    "ALIGNMENT_FALLBACK_RESPONSE",
    "DEFAULT_CRISIS_RESOURCES",
    "DEFAULT_HANDOFF_SLA_SECONDS",
    "DISTRESS_FALLBACK_RESPONSE",
    "DISTRESS_GROUNDING_ADDENDUM",
    "FRAMING_VERSION",
    "IDENTITY_CLAIM_PATTERNS",
    "REALITY_FRAMING_BLOCK",
    "SARCASTIC_DISMISSIVE_PATTERNS",
    "AlignmentReport",
    "Claim",
    "ClaimCategory",
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
    "align_response",
    "apply_affect_guard",
    "apply_alignment_guard",
    "build_crisis_response",
    "build_grounding_corpus",
    "build_handoff_acknowledgement",
    "classify_user_message",
    "detect_sarcastic_dismissive",
    "escalate_to_human",
    "extract_claims",
    "get_distress_addendum",
    "get_framing",
    "is_grounded",
]

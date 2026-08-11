"""Persona-chat runtime clinical guardrails (G1-G4 + G5/G9 framing + §7.4 handoff/consent).

Clinical source: the Clinical Advisor's ``clinical-guardrails`` spec (advisory
issue HU-1407), PM adoption (HU-1408), and Tech-Lead architectural placement
(HU-1409, clinically approved). This package implements the runtime guardrails
required for the Phase-1 phase-gate sign-off recorded in HU-1407 §7.3, plus the
§7.4 pre-real-user clinical gates (HU-1420):

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
* :mod:`huible.safety.handoff_monitoring` — §7.4 ops-gate SLA monitoring +
  outcome telemetry over the handoff audit log: live breach status per open
  ticket (the on-call alert signal) plus aggregate degrade / pending-breach /
  answered-within-SLA rates (the dashboard surface the Clinical Advisor signs
  off before real-user traffic).
* :mod:`huible.safety.alignment` — §7.4.2 generation-time claim->ref alignment
  filter: any factual/identity claim in the persona's reply must be traceable
  to a retrieved reference (or the persona vault), or be suppressed. The
  generation-side backstop for a confabulating generator, complementing the
  G4 retrieval-side provenance firewall.
* :mod:`huible.safety.consent` — §7.4.3 G6 first-use reality-framing / consent
  gate: no persona-voiced reply may leave the chat path before the session has
  acknowledged the consent card. Pluggable backend + injectable card content
  (the Onboarding Agent owns the clinically-reviewed copy); the deceased persona
  never voices the consent.
* :mod:`huible.safety.risk` — §7.4.4 G8 risk-flag enforcement: the reserved
  ``risk_flags`` / ``session_meta`` surfaces actually gate runtime behavior
  (not just record). The enforcement engine maps each flag + session signal
  to a binding action (tighten / reframe / refuse_topic / handoff /
  pause_session) per the Clinical Advisor's enforcement matrix; the chat path
  applies the action with concrete runtime effects.

The chat endpoint (``huible.api.app``) wires these so that a crisis signal
never reaches the persona voice (G1), every persona-voiced turn carries the
immutable framing (G2/G5/G9), distress flattens the voice (G3), the response
trace records the safety event / memory refs for audit (G4), a crisis turn
escalates to a real human with a monitored SLA (§7.4.1), every persona-voiced
reply is aligned against its retrieved refs so no unsupported claim reaches a
grieving user (§7.4.2), no persona reply proceeds before the session
acknowledges the reality-framing / consent card (§7.4.3 G6), and every
risk-flag / session-meta signal actually changes runtime behavior (§7.4.4 G8).
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
from huible.safety.consent import (
    CONSENT_CARD_VERSION,
    DEFAULT_CONSENT_ACKNOWLEDGE_INSTRUCTIONS,
    DEFAULT_CONSENT_CARD_BODY,
    DEFAULT_CONSENT_CARD_TITLE,
    ConsentCard,
    ConsentCardProvider,
    ConsentGate,
    ConsentRecord,
    DefaultConsentCard,
    InMemoryConsentGate,
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
    COVERAGE_ALWAYS,
    COVERAGE_HOURS,
    DEFAULT_HANDOFF_SLA_SECONDS,
    CoverageWindow,
    HandoffOutcome,
    HandoffQueue,
    HandoffResult,
    HandoffTicket,
    InMemoryHandoffQueue,
    build_handoff_acknowledgement,
    escalate_risk_to_human,
    escalate_to_human,
)
from huible.safety.handoff_monitoring import (
    HandoffTelemetry,
    SLAStatus,
    compute_handoff_telemetry,
    sla_status,
    was_answered_within_sla,
)
from huible.safety.risk import (
    AGE_INAPPROPRIATE_TOPIC_PATTERNS,
    DEFAULT_DOSAGE_CAP_TURNS,
    PAUSE_SESSION_RESPONSE,
    PRECEDENCE,
    PROXY_USER_PAUSE_RESPONSE,
    REFRAME_REANCHOR_ADDENDUM,
    REFUSE_TOPIC_FALLBACK_RESPONSE,
    RISK_FLAG_REQUIRED_ACTIONS,
    EnforcementAction,
    EnforcementReport,
    InMemoryRiskProfile,
    RiskFlag,
    RiskProfileProvider,
    RiskSessionSignals,
    build_reframe_addendum,
    enforce_risk_flags,
)

__all__ = [
    "ADVICE_CLAIM_PATTERNS",
    "AGE_INAPPROPRIATE_TOPIC_PATTERNS",
    "ALIGNMENT_FALLBACK_RESPONSE",
    "CONSENT_CARD_VERSION",
    "COVERAGE_ALWAYS",
    "COVERAGE_HOURS",
    "DEFAULT_CONSENT_ACKNOWLEDGE_INSTRUCTIONS",
    "DEFAULT_CONSENT_CARD_BODY",
    "DEFAULT_CONSENT_CARD_TITLE",
    "DEFAULT_CRISIS_RESOURCES",
    "DEFAULT_DOSAGE_CAP_TURNS",
    "DEFAULT_HANDOFF_SLA_SECONDS",
    "DISTRESS_FALLBACK_RESPONSE",
    "DISTRESS_GROUNDING_ADDENDUM",
    "FRAMING_VERSION",
    "IDENTITY_CLAIM_PATTERNS",
    "PAUSE_SESSION_RESPONSE",
    "PRECEDENCE",
    "PROXY_USER_PAUSE_RESPONSE",
    "REALITY_FRAMING_BLOCK",
    "REFRAME_REANCHOR_ADDENDUM",
    "REFUSE_TOPIC_FALLBACK_RESPONSE",
    "RISK_FLAG_REQUIRED_ACTIONS",
    "SARCASTIC_DISMISSIVE_PATTERNS",
    "AlignmentReport",
    "Claim",
    "ClaimCategory",
    "ConsentCard",
    "ConsentCardProvider",
    "ConsentGate",
    "ConsentRecord",
    "CoverageWindow",
    "CrisisClassifier",
    "CrisisResult",
    "CrisisSignal",
    "DefaultConsentCard",
    "DeterministicCrisisClassifier",
    "EnforcementAction",
    "EnforcementReport",
    "FramingBlock",
    "HandoffOutcome",
    "HandoffQueue",
    "HandoffResult",
    "HandoffTelemetry",
    "HandoffTicket",
    "InMemoryConsentGate",
    "InMemoryHandoffQueue",
    "InMemoryRiskProfile",
    "RiskFlag",
    "RiskProfileProvider",
    "RiskSessionSignals",
    "SLAStatus",
    "UserAffect",
    "align_response",
    "apply_affect_guard",
    "apply_alignment_guard",
    "build_crisis_response",
    "build_grounding_corpus",
    "build_handoff_acknowledgement",
    "build_reframe_addendum",
    "classify_user_message",
    "compute_handoff_telemetry",
    "detect_sarcastic_dismissive",
    "enforce_risk_flags",
    "escalate_risk_to_human",
    "escalate_to_human",
    "extract_claims",
    "get_distress_addendum",
    "get_framing",
    "is_grounded",
    "sla_status",
    "was_answered_within_sla",
]

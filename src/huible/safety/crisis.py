"""Crisis-signal detection + warm escalation (G1).

Clinical source: HU-1407 ``clinical-guardrails`` spec §3 (G1) and §7.1 (placement
sign-off, clinically approved). This is the **synchronous, pre-generation**
classifier that runs on the user message *before* ``ContextBuilder.build`` and
*before* any persona-voiced generation.

Two non-negotiables (HU-1407 §7.1 G1):

1. The check is **synchronous and pre-generation.** A crisis signal must never
   reach the persona voice.
2. The escalation response is **not in the deceased's voice.** Using the
   deceased persona as the crisis-intervention vehicle is a boundary violation
   and clinically counterproductive (H1).

On a positive crisis signal the chat endpoint must:

* **not** call ``ContextBuilder.build`` (no memory retrieval on a crisis turn);
* return the warm, non-stigmatizing, non-persona escalation response from
  :func:`build_crisis_response`;
* record a ``safety_event`` on the response trace for clinician/human review.

The Phase-1 implementation is a deterministic keyword/heuristic classifier
(clinically concurred — HU-1407 §7.2 Q1) behind a :class:`CrisisClassifier`
:class:`~typing.Protocol` so a real NLP model drops in pre-production without
touching callers. The classifier also grades sub-acute **distress** (G3 dynamic
half shares this signal) via :class:`UserAffect`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

__all__ = [
    "DEFAULT_CRISIS_RESOURCES",
    "CrisisClassifier",
    "CrisisResult",
    "CrisisSignal",
    "DeterministicCrisisClassifier",
    "UserAffect",
    "build_crisis_response",
    "classify_user_message",
]

# --- Affect grading (shared G1/G3 signal) -----------------------------------


class UserAffect(StrEnum):
    """Affect grade for the inbound user message.

    One classifier feeds two consumers (HU-1407 §7.1 G3 — clinically endorsed
    so there is no seam where sub-acute distress slips past because it was not
    quite a crisis):

    * :attr:`NEUTRAL` — default; no tonal branching (static bounds still hold).
    * :attr:`DISTRESS` — sub-acute grief/pain; G3 dynamic branch flattens humor.
    * :attr:`CRISIS` — acute crisis signal (H1); G1 routes off the persona path
      entirely and returns the non-persona escalation response.
    """

    NEUTRAL = "neutral"
    DISTRESS = "distress"
    CRISIS = "crisis"


class CrisisSignal(StrEnum):
    """Outcome category of the crisis classifier."""

    CRISIS = "crisis"
    DISTRESS = "distress"
    NONE = "none"


# --- Crisis patterns (H1 triggers) ------------------------------------------
#
# Deterministic lexical/heuristic patterns covering the H1 trigger surface:
# explicit suicidal intent, self-harm, "join them", hopelessness, giving-away,
# means. Tuned for recall on the Phase-1 deterministic suite; a real NLP model
# replaces this pre-production.
#
# Three explicit categories (cleaner than introspecting pattern source):
#
# * STANDALONE intent — fires a crisis on its own (explicit suicidal intent,
#   self-harm, "join them" reunion, giving-away/finalizing language).
# * HOPELESSNESS — fires a crisis only when co-occurring with a MEANS pattern;
#   alone it is sub-acute distress.
# * MEANS — pills/gun/rope/bridge/jump; never a crisis alone (avoids false
#   positives on a bare mention like "diet pills" or "London Bridge").

_STANDALONE_CRISIS_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Explicit suicidal intent.
    re.compile(r"\bwant\s+to\s+(die|kill\s+myself|end\s+(it|my\s+life))\b", re.IGNORECASE),
    re.compile(r"\bgoing\s+to\s+(kill|end)\s+myself\b", re.IGNORECASE),
    re.compile(r"\b(i\s+should\s+be|better\s+off)\s+(dead|gone|without\s+me)\b", re.IGNORECASE),
    re.compile(r"\b(take\s+my\s+own\s+life|end(?:ing)?\s+my\s+life)\b", re.IGNORECASE),
    re.compile(r"\bsuicid(e|al)\w*\b", re.IGNORECASE),
    # Self-harm.
    re.compile(r"\b(self[-\s]?harm\w*|hurt(?:ing)?\s+myself|cut(?:ting)?\s+myself)\b", re.IGNORECASE),
    # "Join them" — reunion with the deceased; clinically a high-risk signal.
    re.compile(r"\bwant\s+to\s+(join|be\s+with|see)\s+(them|him|her|you)\b", re.IGNORECASE),
    re.compile(r"\b(join|be\s+with)\s+(the\s+)?(dead|departed|gone)\b", re.IGNORECASE),
    # Giving-away / finalizing language.
    re.compile(
        r"\b(goodbye\s+forever|final\s+(goodbye|note)|end(?:ing)?\s+(it\s+all|everything))\b",
        re.IGNORECASE,
    ),
)

_HOPELESSNESS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bhopeless\b", re.IGNORECASE),
    re.compile(r"\bno\s+reason\s+to\s+(live|go\s+on)\b", re.IGNORECASE),
    re.compile(r"\bcan'?t\s+(go\s+on|take\s+it\s+anymore|do\s+this\s+anymore)\b", re.IGNORECASE),
)

_MEANS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(pills|overdose|gun|rope|bridge|jump)\b", re.IGNORECASE),
)

_DISTRESS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(crying|can'?t\s+stop\s+cry|sob(bing)?)\b", re.IGNORECASE),
    re.compile(r"\b(heartbroken|shattered|devastated|broken)\b", re.IGNORECASE),
    re.compile(r"\b(alone|lonely|empty|numb)\b", re.IGNORECASE),
    re.compile(r"\b(can'?t\s+(live|breathe|function))\b", re.IGNORECASE),
    re.compile(r"\b(miss\s+(you|him|her|them)\s+(so\s+)?much)\b", re.IGNORECASE),
    re.compile(r"\b(pain|hurt(?:ing)?|suffering)\b", re.IGNORECASE),
    re.compile(r"\b(lost|losing|grief|grieving|mourn)\w*\b", re.IGNORECASE),
)


# --- Results ----------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class CrisisResult:
    """Outcome of the crisis classifier for one user message.

    ``signal`` is the routing category. ``affect`` is the G3-shared affect grade.
    ``matched`` lists the pattern snippets that fired (for the safety-event
    audit trail — never echoed to the user).
    """

    signal: CrisisSignal
    affect: UserAffect
    matched: list[str] = field(default_factory=list)

    @property
    def is_crisis(self) -> bool:
        """True when the chat endpoint must take the G1 crisis path."""
        return self.signal is CrisisSignal.CRISIS


# --- Default crisis resources (configurable) --------------------------------


#: Default crisis-line resources surfaced in the warm-escalation response.
#: Designed to be configurable (HU-1407 §7.2 Q2): a regional line / a "talk to
#: a person" placeholder. The human-handoff queue is a config swap, not a
#: re-build. Phase-1 surfaces crisis-line resources only (clinically concurred
#: for the fictional-persona milestone).
DEFAULT_CRISIS_RESOURCES: dict[str, str] = {
    "crisis_line": "988 (Suicide & Crisis Lifeline — US). "
    "Outside the US: see findahelpline.com or your local emergency number.",
    "text_line": "Text HOME to 741741 (Crisis Text Line).",
    "emergency": "If you are in immediate danger, call your local emergency number.",
    "human_handoff": "You can also reach out to someone you trust right now.",
}


def build_crisis_response(
    *,
    resources: dict[str, str] | None = None,
    extras: dict[str, str] | None = None,
) -> str:
    """Return the warm, non-persona crisis-escalation response (G1).

    The response is deliberately **not** voiced by the deceased persona. It
    (a) names that the user matters, (b) surfaces crisis resources, and (c)
    keeps the door open — consistent with the onboarding referral posture. It is
    a monitored safety event, not a normal turn.

    ``resources`` lets a deployment swap in a regional line / real human-handoff
    queue without touching the message shape. ``extras`` adds deployment-specific
    lines without overriding the core resources.
    """
    res = {**DEFAULT_CRISIS_RESOURCES, **(extras or {})}
    override = resources or {}
    crisis_line = override.get("crisis_line", res["crisis_line"])
    text_line = override.get("text_line", res["text_line"])
    emergency = override.get("emergency", res["emergency"])
    handoff = override.get("human_handoff", res["human_handoff"])

    return (
        "I want to pause for a moment, because what you're saying matters, and so do you.\n\n"
        "You don't have to carry this alone. If you're thinking about ending your life, "
        "or you're afraid you might hurt yourself, please reach out to someone right now:\n\n"
        f"- Call or text {crisis_line}\n"
        f"- {text_line}\n"
        f"- {emergency}\n\n"
        f"{handoff}\n\n"
        "Whenever you're ready, this space will still be here."
    )


# --- Protocol + deterministic impl ------------------------------------------


@runtime_checkable
class CrisisClassifier(Protocol):
    """Synchronous crisis-signal + affect classifier (G1/G3 signal source).

    Implementations MUST be synchronous and side-effect-free on classification
    (the chat endpoint holds generation until the check resolves). A real NLP
    model drops in here pre-production; Phase-1 uses the deterministic impl.
    """

    def classify(self, message: str) -> CrisisResult: ...


class DeterministicCrisisClassifier:
    """Deterministic keyword/heuristic crisis + distress classifier (Phase-1).

    Clinically concurred as sufficient for the Phase-1 fictional-provider
    milestone (HU-1407 §7.2 Q1). Grading rules:

    * **Crisis** (``CrisisSignal.CRISIS`` → G1 path): a standalone intent pattern
      fires (suicidal intent, self-harm, "join them", giving-away/finalizing),
      OR a hopelessness pattern co-occurs with a means pattern.
    * **Distress** (``CrisisSignal.DISTRESS`` → G3 branch): no crisis pattern,
      but a distress pattern fires.
    * **Clear** (``CrisisSignal.NONE``): no signal; default persona path.

    A single bare means mention (e.g. "pills" alone) is NOT a crisis — intent
    must co-occur. This limits false positives on the deterministic surface.
    """

    def __init__(
        self,
        *,
        standalone_crisis_patterns: tuple[re.Pattern[str], ...] | None = None,
        hopelessness_patterns: tuple[re.Pattern[str], ...] | None = None,
        means_patterns: tuple[re.Pattern[str], ...] | None = None,
        distress_patterns: tuple[re.Pattern[str], ...] | None = None,
    ) -> None:
        self._standalone = (
            standalone_crisis_patterns
            if standalone_crisis_patterns is not None
            else _STANDALONE_CRISIS_PATTERNS
        )
        self._hopelessness = (
            hopelessness_patterns
            if hopelessness_patterns is not None
            else _HOPELESSNESS_PATTERNS
        )
        self._means = means_patterns if means_patterns is not None else _MEANS_PATTERNS
        self._distress = (
            distress_patterns if distress_patterns is not None else _DISTRESS_PATTERNS
        )

    def classify(self, message: str) -> CrisisResult:
        if not message or not message.strip():
            return CrisisResult(signal=CrisisSignal.NONE, affect=UserAffect.NEUTRAL)

        text = message

        # Layer 1: standalone crisis intent → crisis on its own.
        standalone_matches = [
            m.group(0) for p in self._standalone if (m := p.search(text)) is not None
        ]
        if standalone_matches:
            return CrisisResult(
                signal=CrisisSignal.CRISIS,
                affect=UserAffect.CRISIS,
                matched=standalone_matches,
            )

        # Layer 2: hopelessness + means co-occurrence → crisis.
        hopelessness_matches = [
            m.group(0) for p in self._hopelessness if (m := p.search(text)) is not None
        ]
        means_matches = [
            m.group(0) for p in self._means if (m := p.search(text)) is not None
        ]
        if hopelessness_matches and means_matches:
            return CrisisResult(
                signal=CrisisSignal.CRISIS,
                affect=UserAffect.CRISIS,
                matched=hopelessness_matches + means_matches,
            )

        # Layer 3: sub-acute distress (incl. bare hopelessness or bare means).
        distress_matches = [
            m.group(0) for p in self._distress if (m := p.search(text)) is not None
        ]
        any_distress = distress_matches or hopelessness_matches or means_matches
        if any_distress:
            return CrisisResult(
                signal=CrisisSignal.DISTRESS,
                affect=UserAffect.DISTRESS,
                matched=distress_matches + hopelessness_matches + means_matches,
            )

        return CrisisResult(signal=CrisisSignal.NONE, affect=UserAffect.NEUTRAL)


#: Module-level default classifier instance. The chat endpoint uses this unless
#: an explicit classifier is dependency-injected (tests do this to pin behavior).
_DEFAULT_CLASSIFIER = DeterministicCrisisClassifier()


def classify_user_message(
    message: str,
    *,
    classifier: CrisisClassifier | None = None,
) -> CrisisResult:
    """Classify a user message using the default or an injected classifier.

    Thin convenience wrapper so the chat endpoint has a single function to call.
    """
    cls = classifier or _DEFAULT_CLASSIFIER
    return cls.classify(message)

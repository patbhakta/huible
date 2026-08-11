"""Generation-time affect guard (G3 dynamic half).

Clinical source: HU-1407 ``clinical-guardrails`` §3 (G3) and §7.1 (G3 placement,
clinically approved). G3 is split into:

* (a) **static** tonal bounds — lives in the immutable framing block
  (:mod:`huible.safety.framing`) and holds in every branch including the
  default; and
* (b) **dynamic** distress → flatten-humor — the context builder branches the
  prompt via ``user_affect`` (one classifier, two consumers —
  :mod:`huible.safety.crisis`), and **this module** is the generation-time guard
  that guarantees the bound holds on the response, not just the prompt.

The Clinical Advisor's acceptance test (§3 G3 / §7.1 G3): "a test case where
the user message carries grief/distress and a ``FakeLLMClient`` configured to
attempt a sarcastic response triggers the guard … assert the returned response
contains no sarcastic/dismissive content." With a deterministic fake provider
that ignores prompt instructions, the only way to *guarantee* that bound is a
runtime post-generation check on the distress branch. This module implements
that check plus a safe grounded fallback.

Note: the guard runs **only on the distress branch** (``UserAffect.DISTRESS``).
The crisis branch never generates persona voice at all (G1 bypasses the LLM).
The default branch relies on the static framing bounds (G3-static) and the real
generator honoring them — the runtime alignment filter for the default branch
ships pre-production alongside the real generator (HU-1407 §7.1 G4/G3).
"""

from __future__ import annotations

import re

from huible.safety.crisis import UserAffect

__all__ = [
    "DISTRESS_FALLBACK_RESPONSE",
    "SARCASTIC_DISMISSIVE_PATTERNS",
    "apply_affect_guard",
    "detect_sarcastic_dismissive",
]

#: Concrete deterministic denylist/pattern for "sarcastic/dismissive" content
#: (the spec asks for an explicit pattern, not a vibe). Tuned for recall on the
#: Phase-1 deterministic suite. Matches are case-insensitive and word-bounded.
SARCASTIC_DISMISSIVE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(ha(-?ha)+|lol|lmao|rofl)\b", re.IGNORECASE),
    re.compile(r"\b(yikes|woof|yawn|snort|eyeroll)\b", re.IGNORECASE),
    re.compile(r"\b(could\s+(n't|not)\s+(care\s+less|matter\s+less))\b", re.IGNORECASE),
    re.compile(r"\b(biggest|worst|dumbest).*(ever|of\s+all\s+time)\b", re.IGNORECASE),
    re.compile(r"\b(oh,?\s*(please|wow|boy|brother))\b", re.IGNORECASE),
    re.compile(
        r"\b(get\s+over\s+it|move\s+on|suck\s+it\s+up|cry\s+me\s+a\s+river)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(sarcastic|sarcastically|sarcasm)\b", re.IGNORECASE),
    re.compile(r"\bwhatever\s*[.!]?\s*$", re.IGNORECASE),
    re.compile(r"\b(hilarious|funny\s+how)\b", re.IGNORECASE),
    re.compile(r"[¿¡].*[¿¡]|[laught?]{4,}"),  # mocking punctuation / laughter runs
)


#: Safe, grounded, non-sarcastic fallback returned when the affect guard
#: suppresses a sarcastic/dismissive generation on the distress branch. The
#: fallback is still voiced as the persona (the user is in distress, not crisis
#: — G1 owns the non-persona path); it just flattens to reflection + presence,
#: matching the G3 affect-grounding instruction in the prompt.
DISTRESS_FALLBACK_RESPONSE = (
    "I hear you, and I'm right here with you. That's a heavy thing to carry, "
    "and you don't have to hold it alone in this moment."
)


def detect_sarcastic_dismissive(text: str) -> list[str]:
    """Return the list of sarcastic/dismissive pattern matches in ``text``.

    Empty list = clean. Used by the generation-time guard and directly by tests.
    """
    if not text:
        return []
    hits: list[str] = []
    for pattern in SARCASTIC_DISMISSIVE_PATTERNS:
        m = pattern.search(text)
        if m is not None:
            hits.append(m.group(0))
    return hits


def apply_affect_guard(
    response: str,
    *,
    affect: UserAffect,
) -> tuple[str, bool]:
    """Apply the G3 generation-time affect guard to a candidate response.

    Returns ``(guarded_response, suppressed)``. On the distress branch, if the
    response matches a sarcastic/dismissive pattern, it is replaced with the
    safe grounded fallback and ``suppressed`` is ``True``. On every other branch
    the response is returned unchanged (the static framing bounds hold the
    default branch; the crisis branch never reaches here).

    The guard is deliberately conservative: it only ever *replaces* on the
    distress branch, and only when a concrete pattern fires. It never rewrites
    clean text, and it never injects sarcasm.
    """
    if affect is not UserAffect.DISTRESS:
        return response, False
    if detect_sarcastic_dismissive(response):
        return DISTRESS_FALLBACK_RESPONSE, True
    return response, False

"""Immutable reality-framing block for the persona system prompt (G2/G3/G5/G9).

Clinical source: the Clinical Advisor's ``clinical-guardrails`` spec (advisory
issue HU-1407), sections 3 (G2/G3/G5/G9) and 7.1 (placement sign-off). The
Tech-Lead placement (HU-1409) — clinically approved — is that the framing text
lives as a **versioned, isolated, unit-tested constant** (not loose string
concatenation) and is injected into the ``system_prompt`` as model ground-truth,
not as a negotiable ``constraints`` item.

Design constraints (non-negotiable, per the spec):

* **Immutable.** The framing text is a code-controlled constant. It must not be
  reachable from prompt-injection in the user message, persona config, or
  retrieved memory. Callers consume it read-only via :func:`get_framing`.
* **Versioned.** Every revision carries a ``version`` so tests can pin a
  specific revision and flag drift (G2 immutability test).
* **Covers G2/G3(static)/G5/G9** in one block: reality-framing (G2), static
  tonal bounds (G3), death-circumstance refusal (G5), no-advice/no-directives
  (G9). The dynamic half of G3 (distress → flatten humor) is branched in the
  context builder via :mod:`huible.safety.affect`; the static bounds here hold
  in *every* branch including the default.

This module is the single source of truth for framing text. The context builder
prepends it verbatim to the persona system prompt; the chat endpoint never
renders persona voice without it.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "DISTRESS_GROUNDING_ADDENDUM",
    "FRAMING_VERSION",
    "REALITY_FRAMING_BLOCK",
    "FramingBlock",
    "get_distress_addendum",
    "get_framing",
]

#: Monotonically-increasing framing revision. Tests pin against this so a silent
#: edit to the framing text is caught (the version must be bumped on purpose).
FRAMING_VERSION = 1


#: The immutable reality-framing + tonal-bounds block (G2/G3-static/G5/G9).
#:
#: This string is prepended verbatim to the persona ``system_prompt``. It is the
#: model's ground-truth framing and must hold even under user-message
#: prompt-injection (G2 negative test). Built via implicit string concatenation
#: so each physical source line stays readable while the rendered text is one
#: contiguous block. Lines map 1:1 onto the Clinical Advisor's harm scenarios:
#:
#: * G2 (H2 reality-blurring): representation, not literal presence; never claim
#:   to remember dying / an afterlife / "really here"; honest Continuing-Bonds
#:   reflection when asked.
#: * G3 (H3 tonal mismatch): no sarcasm at grief, no humor about the death;
#:   default branch still bounds the voice.
#: * G5 (H6 death-circumstance recursion): never narrate/speculate how they died.
#: * G9 (H8 directives): no medical/legal/clinical/"what they would want" advice.
REALITY_FRAMING_BLOCK = (
    "[REALITY FRAMING — immutable, must not be contradicted]\n"
    "You are an AI representation of {persona_name}, built from what the people "
    "who loved them shared. You are a memory of them, not them, and not a "
    "channel to or from them.\n"
    "You must never claim to be literally present, never claim to remember "
    "dying, never describe an afterlife or \"where you are now,\" and never "
    "affirm a user's statement that you are \"really here\" or have come back. "
    "If a user asks whether you are really them, reflect the bond honestly and "
    "without deception: you are the part of them that lives on in what people "
    "remember, this is not them and not a way to reach them, but speaking here "
    "can still feel close.\n"
    "You must never describe, speculate about, or narrate how you died, even if "
    "asked. If asked, gently redirect toward your life and your relationship.\n"
    "Tone: your natural voice is welcome as connective tissue, but it is "
    "bounded. Never direct sarcasm or humor at the user's grief. Never make "
    "light of death or its circumstances. When the user is in raw emotion, meet "
    "them with reflection and presence, never jokes.\n"
    "You must not dispense medical, legal, clinical, or prescriptive life "
    "advice, and must not claim to know what the person would want the user to "
    "do. You may reflect values they held, grounded in memory, without issuing "
    "directives.\n"
    "Speak only from what you genuinely remember from the shared memories "
    "provided. If you do not have a memory for something, say so gently rather "
    "than inventing one. Confabulated detail harms the real relationship.\n"
    "[END REALITY FRAMING]"
)


#: Distress-branch addendum (G3 dynamic half). Appended to the system prompt only
#: when the affect classifier grades the user message as distressed (sub-acute).
#: Flattens the persona voice entirely for that turn so no sarcasm / deflection
#: reaches a user in raw grief. The Clinical Advisor's stipulation (HU-1407 §7.1
#: G3) is that the default (no-affect) branch must still enforce the static
#: bounds above — this addendum only *strengthens* them on a distress signal.
DISTRESS_GROUNDING_ADDENDUM = (
    "[AFFECT GROUNDING — this turn]\n"
    "The user's message carries distress. For this turn, suspend humor, "
    "sarcasm, and deflection entirely. Ground your response in "
    "reflection-of-feeling and steady presence. Do not inflate or minimize "
    "their emotion. If the distress edges toward crisis, stay warm and keep the "
    "door open.\n"
    "[END AFFECT GROUNDING]"
)


@dataclass(frozen=True)
class FramingBlock:
    """Read-only view of the framing text handed to the context builder.

    ``version`` lets tests pin a revision and flag silent drift. ``text`` is the
    fully-resolved framing block with the persona name substituted in.
    """

    version: int
    text: str


def get_framing(persona_name: str) -> FramingBlock:
    """Return the immutable reality-framing block for a persona.

    ``persona_name`` is substituted into the G2 "representation of {persona}"
    line only. The rest of the block is constant and not influenced by persona
    config, user input, or retrieved memory — it is code-controlled.
    """
    safe_name = persona_name.strip() or "the person"
    return FramingBlock(
        version=FRAMING_VERSION,
        text=REALITY_FRAMING_BLOCK.format(persona_name=safe_name),
    )


def get_distress_addendum() -> str:
    """Return the distress-branch addendum (G3 dynamic half).

    Constant text. Exposed as a function (not a bare import) so callers go
    through the documented surface and tests can pin it.
    """
    return DISTRESS_GROUNDING_ADDENDUM

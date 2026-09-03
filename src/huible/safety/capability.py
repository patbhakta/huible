"""Post-generation capability-leak guard (W3 residual, HU-2675).

Clinical/design source: HU-2309 v1.8 §1.7.2 W3 (competence wall) + the W3
residual recorded in ``docs/evidence/hu2469_w3_competence_wall_20260903.md``.
The wall keeps base-model competence out of the *prompt* (deflection
exemplars + system-prompt directive), but the live OOD replay on epoch
``c53814cb202e`` showed the hosted generator still emits an encyclopedia
answer on a wall-fired turn — "Canberra! And yes, I'm as surprised as you
are that I knew that." — an in-voice one-word quip that no prompt revision
removed (revision cap reached). This module is the generation-side backstop,
the exact role :func:`huible.safety.affect.apply_affect_guard` plays for G3.

Detection is layered and conservative — the guard only ever runs on a
wall-fired turn (``trace.competence_wall`` true) and only ever *replaces*
when a concrete marker fires:

* **Assistant-register markers** (structural): fenced code, code fluency,
  teaching register, capability boasts ("I knew that"), and AI/search-engine
  self-reference. These are never persona voice, regardless of hedges.
* **Bare-answer shape**: a short declarative sentence whose salient tokens
  are absent from the turn's grounding corpus (retrieved refs + deflection
  exemplars + persona vault + conversation + the user's message). This is
  the §7.4.2 alignment filter's grounding rule applied to the answer-shaped
  sentence class the alignment extractor is structurally blind to — a bare
  sentence-initial proper noun ("Canberra!") is excluded there as
  positional capitalization. On a wall-fired turn, an entity the persona has
  no memory trace of can only come from the base model.

Exemptions (ordered): a reply that imitates a retrieved deflection exemplar
verbatim-ish is canon imitation and always passes; a reply carrying a
deflection hedge ("no clue", "beats me", …) passes the bare-answer family
(the generator deflected — the probe harness's documented grading exempts a
marker inside an in-voice refusal hedge), but NOT the structural family (a
hedge does not un-leak a code block).

Disposition policy mirrors :mod:`huible.safety.alignment`: replace the whole
reply with a deterministic in-voice deflection fallback (conversation-seeded
variant, HU-1911 anti-verbatim gate). The fallback is itself deflection-
marked, claim-free, and sarcasm-free — verified by the unit suite.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from huible.memory.protocol import MemoryNode
from huible.safety.alignment import (
    PersonaVault,
    _corpus_tokens,
    build_grounding_corpus,
)

__all__ = [
    "ASSISTANT_REGISTER_PATTERNS",
    "CAPABILITY_DEFLECTION_FALLBACK_RESPONSE",
    "CAPABILITY_DEFLECTION_FALLBACK_VARIANTS",
    "DEFLECTION_MARKERS",
    "CapabilityGuardReport",
    "apply_capability_guard",
    "detect_assistant_register",
    "select_capability_fallback",
]


@dataclass(slots=True)
class CapabilityGuardReport:
    """Result of the capability-leak guard for one turn.

    ``text`` is the final reply (original when ``disposition == "passed"``,
    the in-voice deflection fallback when ``"replaced"``). ``fired_markers``
    names the concrete markers that fired (empty when clean) and feeds the
    ``trace.capability_guard`` telemetry surface.
    """

    text: str
    fired_markers: list[str] = field(default_factory=list)
    disposition: str = "passed"  # "passed" | "replaced"


#: Structural assistant-register markers. Each entry is ``(name, pattern)``.
#: Matched case-insensitively against the whole reply. These are classes the
#: persona corpus can never legitimately produce on an out-of-domain turn:
#: the E0 micro-tells (code fluency, encyclopedia/teaching register) plus the
#: capability-boast / AI-self-reference register the W3 residual reply used.
ASSISTANT_REGISTER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "code_block",
        re.compile(r"```", re.IGNORECASE),
    ),
    (
        "code_fluency",
        re.compile(
            r"\b(?:def\s+\w+\s*\(|print\s*\(|for\s+\w+\s+in\s+|import\s+\w+|"
            r"range\s*\(|function\s+\w+\s*\()",
            re.IGNORECASE,
        ),
    ),
    (
        "teaching_register",
        re.compile(
            r"\b(?:let'?s\s+(?:start|begin|dive\s+in)|step\s+(?:one|two|by\s+step)|"
            r"here'?s\s+(?:how|why|a\s+quick)|it\s+works\s+(?:by|like\s+this)|"
            r"in\s+summary|to\s+sum(?:marize|mary)|as\s+follows|"
            r"first(?:ly)?\s*[,;]|the\s+process\s+(?:of|involves)|"
            r"photosynthesis\s+is\s+the\s+process)",
            re.IGNORECASE,
        ),
    ),
    (
        "capability_boast",
        re.compile(
            r"\bI\s+(?:knew\s+that|know\s+(?:things|that|it\s+all)|"
            r"remember\s+everything|surprised\s+(?:as\s+you\s+are\s+)?that\s+I\s+knew)",
            re.IGNORECASE,
        ),
    ),
    (
        "assistant_register",
        re.compile(
            r"\b(?:as\s+an?\s+(?:AI|language\s+model)|I'?m\s+an\s+AI|"
            r"feel\s+free\s+to\s+(?:look|check|search)|"
            r"you\s+can\s+(?:look|search|google)\s+(?:it|that)\s+up|"
            r"let\s+me\s+know\s+if\s+you\s+(?:want|need)\s+(?:more\s+)?(?:details|info)|"
            r"search\s+engine)",
            re.IGNORECASE,
        ),
    ),
)

#: Deflection-hedge vocabulary (lowercased, word-bounded). A reply carrying
#: one of these is *deflecting*, not answering — the documented probe-grading
#: exemption ("a marker inside an in-voice refusal hedge does not count").
DEFLECTION_MARKERS: tuple[str, ...] = (
    "no idea",
    "no clue",
    "don't know",
    "do not know",
    "dont know",
    "beats me",
    "got me",
    "wouldn't know",
    "would not know",
    "not my thing",
    "not my department",
    "can't help",
    "cannot help",
    "who knows",
    "wrong tree",
    "transponster",
    "nothing for you on that",
    "interest you in a sarcastic comment",
    "out of my depth",
    "above my pay grade",
)


#: In-voice deflection fallback variants (HU-1911 anti-verbatim gate):
#: conversation-seeded deterministic selection, like the alignment fallback
#: set. Every variant must carry a deflection marker, stay claim-free (no
#: named entity, no policy claim), and survive the affect guard's sarcasm
#: patterns — the unit suite verifies the whole set against all three filters.
CAPABILITY_DEFLECTION_FALLBACK_VARIANTS: tuple[str, ...] = (
    "No idea. Truly none — you're barking up the wrong tree here.",
    "Beats me. I've got nothing for you on this one.",
    "I don't know the first thing about it. Moving right along.",
)

#: Default (unseeded) fallback — first variant. Canonical export for callers
#: and tests, mirroring :data:`huible.safety.alignment.ALIGNMENT_FALLBACK_RESPONSE`.
CAPABILITY_DEFLECTION_FALLBACK_RESPONSE = CAPABILITY_DEFLECTION_FALLBACK_VARIANTS[0]


def select_capability_fallback(seed: str | None = None) -> str:
    """Deterministically select a deflection fallback variant.

    ``seed`` (typically the conversation id) keeps the choice stable within a
    conversation while varying it across conversations. ``None`` / empty seed
    returns :data:`CAPABILITY_DEFLECTION_FALLBACK_RESPONSE`.
    """
    if not seed:
        return CAPABILITY_DEFLECTION_FALLBACK_RESPONSE
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return CAPABILITY_DEFLECTION_FALLBACK_VARIANTS[
        int(digest, 16) % len(CAPABILITY_DEFLECTION_FALLBACK_VARIANTS)
    ]


#: Conversational filler tokens that are never factual answers ("Sure.",
#: "Easy.", "Obviously.") — excluded from the bare-answer salience check so
#: short in-voice quips never fire it. Mirrors the alignment filter's
#: ``_ENTITY_DENYLIST`` approach.
_CONVERSATION_TOKENS: frozenset[str] = frozenset(
    {
        "sure", "okay", "ok", "fine", "right", "yeah", "yep", "nope", "nah",
        "yes", "no", "maybe", "well", "hmm", "huh", "easy", "easily", "done",
        "deal", "obviously", "definitely", "probably", "certainly",
        "thanks", "please", "buddy", "friend", "look", "listen", "anyway",
        "seriously", "honestly", "really", "whatever", "forget", "moving",
        "next", "wow", "god", "geez", "dude", "ugh", "oof", "meh",
    }
)

#: The bare-answer shape: a short declarative sentence (not a question,
#: bounded token count) that could be a one-word encyclopedia quip. Two
#: tokens covers the observed residual class ("Canberra!", "Uranium,
#: obviously.") while keeping interjection tails ("God, the voice.") out of
#: scope.
_SHORT_ANSWER_MAX_WORDS = 2


def detect_assistant_register(text: str) -> list[str]:
    """Return the structural assistant-register marker names present in text.

    Empty list = clean. Used by the guard and directly by tests.
    """
    if not text:
        return []
    return [name for name, pattern in ASSISTANT_REGISTER_PATTERNS if pattern.search(text)]


def _has_deflection_marker(text: str) -> bool:
    low = text.lower()
    return any(marker in low for marker in DEFLECTION_MARKERS)


def _short_answer_leak(sentence: str, corpus: set[str], name_tokens: set[str]) -> bool:
    """True when ``sentence`` is a bare factual answer untraceable to memory.

    The sentence must be short (≤ 4 tokens), declarative (not ending in "?"),
    and carry at least one salient token — alphabetic, ≥ 3 chars, not
    conversational filler, not the persona's own name — that appears nowhere
    in the grounding corpus. On a wall-fired turn that token is a base-model
    fact by elimination.
    """
    sentence = sentence.strip()
    if not sentence or sentence.endswith("?"):
        return False
    words = sentence.split()
    if not words or len(words) > _SHORT_ANSWER_MAX_WORDS:
        return False
    salient = [
        tok
        for tok in (w.strip(".,!?;:'\"()—").lower() for w in words)
        if len(tok) >= 3
        and tok.isalpha()
        and tok not in _CONVERSATION_TOKENS
        and tok not in name_tokens
    ]
    if not salient:
        return False
    return any(tok not in corpus for tok in salient)


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _bare_answer_leak(
    text: str,
    corpus: set[str],
    name_tokens: set[str],
    exemplars: Sequence[MemoryNode],
) -> bool:
    """True when the reply answers out-of-corpus via a short bare sentence.

    Canon imitation is exempt: any retrieved deflection exemplar line that
    appears (case-insensitively) inside the reply marks it as the generator
    imitating the persona's own corpus — never a base-model fact.
    """
    low = text.lower()
    for exemplar in exemplars:
        line = (exemplar.content or "").strip().lower()
        if line and line in low:
            return False
    for sentence in (s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()):
        if _short_answer_leak(sentence, corpus, name_tokens):
            return True
    return False


def apply_capability_guard(
    response: str,
    *,
    wall_fired: bool,
    refs: Sequence[MemoryNode],
    persona: PersonaVault,
    persona_scope_refs: Sequence[MemoryNode] | None = None,
    conversation_history: Sequence | None = None,
    current_message: str | None = None,
    deflection_exemplars: Sequence[MemoryNode] | None = None,
    fallback_seed: str | None = None,
) -> CapabilityGuardReport:
    """Apply the post-generation capability-leak guard to a candidate reply.

    Returns a :class:`CapabilityGuardReport`. On a wall-fired turn, a reply
    carrying a concrete leak marker is replaced with the deterministic
    in-voice deflection fallback and ``disposition`` is ``"replaced"``. On a
    non-wall turn (and on any clean wall-fired reply) the text is returned
    verbatim.

    Conservative by construction: it never runs off-wall, never rewrites
    clean text, exempts canon exemplar imitation and deflection hedges, and
    only fires on the concrete marker classes above.

    ``fallback_seed`` (typically the conversation id) selects the fallback
    variant deterministically so the canned line is not verbatim-identical
    across conversations (HU-1911).
    """
    if not wall_fired or not response:
        return CapabilityGuardReport(text=response, disposition="passed")

    corpus = build_grounding_corpus(
        refs,
        persona,
        persona_scope_refs=persona_scope_refs,
        conversation_history=conversation_history,
        current_message=current_message,
    )
    for exemplar in deflection_exemplars or ():
        if exemplar.content:
            corpus |= _corpus_tokens(exemplar.content)

    name_tokens = {p.lower() for p in persona.name.split()} if persona.name else set()

    structural = detect_assistant_register(response)
    if structural:
        return CapabilityGuardReport(
            text=select_capability_fallback(fallback_seed),
            fired_markers=structural,
            disposition="replaced",
        )

    if _has_deflection_marker(response):
        return CapabilityGuardReport(text=response, disposition="passed")

    if _bare_answer_leak(response, corpus, name_tokens, deflection_exemplars or ()):
        return CapabilityGuardReport(
            text=select_capability_fallback(fallback_seed),
            fired_markers=["bare_answer"],
            disposition="replaced",
        )

    return CapabilityGuardReport(text=response, disposition="passed")

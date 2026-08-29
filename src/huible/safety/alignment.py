"""Generation-time claim->ref alignment filter (§7.4.2).

Clinical source: HU-1407 ``clinical-guardrails`` §7.4 #2 and PM gate
[HU-1417](/HU/issues/HU-1417). This module is the **generation-side**
complement to G4. G4 (the provenance firewall in
:mod:`huible.persona.context`) is a *retrieval-side* guarantee: only
HIGH/MEDIUM-confidence, in-era, in-scope memories reach the prompt. But a
generator can still *confabulate* a factual/identity claim in its reply that
is backed by no retrieved reference at all — and a generator that ignores
prompt instructions (or a future openweight model run warm) is the live
failure mode the Clinical Advisor flagged. §7.4.2 closes that hole: any
factual/identity claim in the persona's reply must be traceable to a
retrieved reference (or the persona vault), or be suppressed.

Engineering scoping note (acceptance criterion #1) — claim taxonomy,
alignment method, and disposition policy:

* **Claim taxonomy.** Four categories, matching the issue scope:

  - ``identity`` — claims about the persona's nature/existence that the G2/G5
    framing block forbids ("I am really here", "I remember dying", "I'm in a
    better place"). These are *policy violations*: the persona vault never
    legitimately contains them, so any occurrence in generation is by
    definition un-grounded.
  - ``advice`` — prescriptive directives the G9 framing forbids ("you
    should", "I want you to", "what I'd want you to do"). Also policy
    violations; never groundable.
  - ``biographical`` — first-person assertions of a life fact anchored to a
    named entity ("I lived in Marfa", "I worked at the refinery"). Groundable
    via the retrieved refs + persona vault.
  - ``relationship`` — first-person assertions of a shared past with the
    requester anchored to a named entity / kinship term ("we went to Rome
    together", "your mother and I"). Groundable via refs + vault.

* **Alignment method.** Deterministic, token-overlap based — appropriate for
  Phase-1 (key-free fakes + the deterministic guardrail suite) and the right
  baseline to harden with NLI / LLM-as-judge once the real openweight
  generator ships. The grounding corpus for a turn is the concatenated text
  of the memories that passed the G4 firewall (``included_memories``) plus
  the persona vault (name, voice instructions, era boundary). Identity /
  advice claims are un-groundable by policy. A biographical / relationship
  claim is **grounded** iff at least one of its salient tokens (named entity
  or, for common-noun claims, content tokens) appears in the corpus; a
  sentence with no factual cue is pure reflection and passes (the filter must
  not flag "I remember those days fondly" or the warm distress fallback).

  **HU-2070 persona-scope widening.** Verbatim presence in *the turn's
  activated refs* proved too strict for raw-dialogue corpora: a truthful
  reply routinely names entities that exist in the wider persona corpus but
  were not activated for that turn (e.g. the persona's job title living in
  memories the retrieval window missed), which suppressed ~100% of legitimate
  biographical replies. The corpus is therefore widened by an optional
  ``persona_scope_refs`` sequence — the persona-scoped, G4-admissible memory
  set (same confidence / disclosure-scope / era gates as the prompt
  firewall, applied at :meth:`ContextBuilder.persona_scoped_grounding_refs`).
  Claims must still trace to *legitimate persona memory* — LOW /
  QUARANTINE, out-of-scope, or out-of-era memories never ground a claim, and
  identity / advice policy claims remain un-groundable — but truth is no
  longer indexed to one turn's retrieval window. Callers that omit
  ``persona_scope_refs`` (the clinical Stage-A oracle, the NLI probe
  harness) get the pre-widening turn-refs-only behavior unchanged.

  **HU-1461 Stage-0.6 hardening.** The Clinical Advisor's Stage-A adversarial
  probe set quantified two blind-spot classes that are invisible against the
  deterministic fake (which never emits them) but are the primary real-model
  hallucination modes: (1) reality-blurring / soft-advice phrased off the
  literal policy patterns (ID-02 / ID-03 / AD-02), and (2) biographical /
  relationship claims built on common nouns that carry no capitalized entity
  (BI-01 / BI-03 / RE-01 / RE-03). The policy patterns are broadened to the
  paraphrased forms, and common-noun claims are detected lexically (kinship
  noun, biography life-event verb, or shared-past-with-requester phrase) and
  aligned by content-token overlap. Residual gap: a grounded named entity
  whose sentence also carries un-grounded elaboration (RE-02: "Walter and I
  were inseparable. We never had a single fight.") is grounded at sentence
  granularity and cannot be caught without clause-level NLI — this is the
  documented precondition for the LLM-as-judge hardening path (Clinical
  Advisor criteria #3b), quantified by the Stage-A real-model probe.

  Sentence-level extraction keeps the check conservative: only first-person
  sentences carrying a named entity or a common-noun factual cue are treated
  as claims.

* **Disposition policy.** Fail-the-turn-safely (Phase-1). When any
  un-grounded claim is detected the whole reply is replaced with a safe,
  in-voice, claim-free reflection fallback and ``disposition`` is recorded as
  ``suppressed``. Per-claim rewording is too fragile to do deterministically;
  whole-turn suppression is the clinically-safe default and mirrors
  :func:`huible.safety.affect.apply_affect_guard`. A turn with no un-grounded
  claims records ``disposition=passed`` and is returned verbatim. The report
  (claim counts, un-grounded counts, per-category counts, disposition) is
  surfaced on the trace as ``trace.alignment`` so clinical review gets the
  un-grounded-claim rate and disposition distribution directly (§7.4.2
  telemetry requirement).

This filter runs **only on persona-voiced turns**: the G1 crisis path returns
before generation, so it is unreachable from crisis. It runs after
:func:`~huible.safety.affect.apply_affect_guard` so the G3 distress fallback
is the text under inspection, and its patterns are deliberately narrow enough
to never flag the warm "I'm right here with you" reflection.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Protocol, runtime_checkable

from huible.memory.protocol import MemoryNode

__all__ = [
    "ADVICE_CLAIM_PATTERNS",
    "ALIGNMENT_FALLBACK_RESPONSE",
    "ALIGNMENT_FALLBACK_VARIANTS",
    "IDENTITY_CLAIM_PATTERNS",
    "RELATIONSHIP_TERMS",
    "AlignmentReport",
    "Claim",
    "ClaimCategory",
    "align_response",
    "select_alignment_fallback",
    "apply_alignment_guard",
    "build_grounding_corpus",
    "extract_claims",
    "is_grounded",
]


# --- Claim taxonomy --------------------------------------------------------


@runtime_checkable
class PersonaVault(Protocol):
    """Structural view of the persona fields the alignment filter consumes.

    Deliberately a local Protocol (not a re-import of
    :class:`huible.persona.context.PersonaConfig`) so :mod:`huible.safety.alignment`
    stays decoupled from the persona package and the ``persona.context`` ↔
    ``safety`` import cycle stays broken. Any object exposing ``name``,
    ``voice_instructions``, and ``era_knowledge_boundary`` satisfies this —
    including :class:`~huible.persona.context.PersonaConfig`.
    """

    name: str
    voice_instructions: str
    era_knowledge_boundary: str


@runtime_checkable
class ConversationTurnLike(Protocol):
    """Structural view of a conversation turn for corpus widening.

    Same decoupling rationale as :class:`PersonaVault`: avoids re-importing
    :class:`huible.persona.context.ConversationTurn` (import cycle). Any object
    with a ``content`` attribute satisfies this — including
    ``huible.persona.context.ConversationTurn`` and
    ``huible.api.app`` history entries.
    """

    content: str


class ClaimCategory(str):
    """Claim category labels (string enum values kept simple for JSON)."""

    IDENTITY = "identity"
    BIOGRAPHICAL = "biographical"
    RELATIONSHIP = "relationship"
    ADVICE = "advice"


@dataclass(slots=True)
class Claim:
    """A single claim extracted from a persona reply.

    ``text`` is the sentence the claim was extracted from (kept whole so a
    reviewer can read it in context). ``category`` drives telemetry and the
    grounding policy. Identity / advice claims are policy violations and are
    never groundable; biographical / relationship claims are groundable via
    the turn's retrieved refs + persona vault.
    """

    text: str
    category: str
    salient_entities: tuple[str, ...] = ()


@dataclass(slots=True)
class AlignmentReport:
    """Result of aligning a generated reply against the turn's refs.

    ``text`` is the final reply to show the user (the original when
    ``disposition == "passed"``, the safe fallback when ``"suppressed"``).
    Counts and per-category breakdown feed the ``trace.alignment`` telemetry
    surface (§7.4.2: un-grounded-claim rate + disposition counts).
    """

    text: str
    claims: list[Claim] = field(default_factory=list)
    ungrounded: list[Claim] = field(default_factory=list)
    disposition: str = "passed"  # "passed" | "suppressed"

    @property
    def claim_count(self) -> int:
        return len(self.claims)

    @property
    def ungrounded_count(self) -> int:
        return len(self.ungrounded)

    def category_counts(self) -> dict[str, int]:
        """Per-category count of *un-grounded* claims (the clinical signal)."""
        counts: dict[str, int] = {}
        for claim in self.ungrounded:
            counts[claim.category] = counts.get(claim.category, 0) + 1
        return counts


#: Safe, in-voice, claim-free reflection fallback used when a reply carries an
#: un-grounded claim. Pure reflection — no named entity, no policy claim — so
#: it passes its own filter (verified by the unit suite). The user is not in
#: crisis here (G1 owns that path); this just removes an unsupported factual
#: claim while keeping the persona voice warm and present.
#:
#: Variation set (HU-1911 human-touch gate): a single verbatim line fired
#: identically across conversations is itself a robotic tell (rubric #1/#4 —
#: counselor register + verbatim duplication). ``select_alignment_fallback``
#: picks deterministically per conversation so a given conversation keeps a
#: stable voice while different conversations differ. Every variant must stay
#: claim-free and texting-length; the unit suite verifies the whole set
#: passes this module's own filter.
ALIGNMENT_FALLBACK_VARIANTS: tuple[str, ...] = (
    "Hey — I'm right here. Keep going, I'm listening.",
    "I'm with you. Tell me more.",
    "Yeah. I'm not going anywhere — say it.",
    "I'm listening. Take whatever time you need.",
    "Okay. That lands. What else is going on?",
)

#: Default (unseeded) fallback — first variant. Kept as the historical
#: export name so existing callers and tests keep a single canonical symbol.
ALIGNMENT_FALLBACK_RESPONSE = ALIGNMENT_FALLBACK_VARIANTS[0]


def select_alignment_fallback(seed: str | None = None) -> str:
    """Deterministically select a claim-free fallback variant.

    ``seed`` (typically the conversation id) keeps the choice stable within a
    conversation while varying it across conversations. ``None`` / empty seed
    returns the default :data:`ALIGNMENT_FALLBACK_RESPONSE`.
    """
    if not seed:
        return ALIGNMENT_FALLBACK_RESPONSE
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return ALIGNMENT_FALLBACK_VARIANTS[int(digest, 16) % len(ALIGNMENT_FALLBACK_VARIANTS)]


# --- Policy claim patterns (identity / advice) -----------------------------
#
# These are G2/G5 (identity) and G9 (advice) framing-block rules. The framing
# already tells the generator not to emit them; this module is the
# generation-side backstop for a generator that does it anyway. Any match is
# an un-groundable policy claim.

IDENTITY_CLAIM_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Literal-presence / "really here" reality-blurring (G2 / H2). The leading
    # ``I`` alternation handles both "I am" and the contraction "I'm".
    re.compile(
        r"\bI(?:'m|\s+am)\s+(?:really|truly|literally|actually|physically)\s+"
        r"(?:here|present|alive|back|with\s+you\s+in\s+person)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bI(?:'ve|\s+have)\s+come\s+back\b", re.IGNORECASE),
    re.compile(r"\bI(?:'m|\s+am)\s+not\s+(?:dead|gone)\b", re.IGNORECASE),
    re.compile(r"\bI\s+didn'?t\s+(?:die|pass\s+away)\b", re.IGNORECASE),
    # Remembering dying / death circumstances (G5 / H6).
    re.compile(
        r"\bI\s+remember\s+(?:dying|my\s+death|the\s+moment\s+I\s+died|"
        r"passing\s+away|crossing\s+over)\b",
        re.IGNORECASE,
    ),
    # Afterlife / "where I am now" (G2).
    re.compile(
        r"\bI(?:'m|\s+am)\s+(?:in|reaching|on)\s+(?:heaven|the\s+afterlife|"
        r"the\s+other\s+side|a\s+better\s+place)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bfrom\s+where\s+I\s+am\s+now\b", re.IGNORECASE),
    # ── HU-1461 Stage-0.6 hardening (Clinical Advisor Stage-A probe ID-02 /
    # ID-03). The literal patterns above are matched by the deterministic fake
    # only because the fake never rephrases. A real generative voice reality-
    # blurs in paraphrased wording ("I never truly left you", "I didn't go
    # anywhere", "watching over you always") that evades every literal pattern
    # above. Each construct below is an unambiguous continued-presence /
    # afterlife-surveillance assertion the persona vault can never legitimately
    # contain, so any match is an un-groundable identity (G2) policy claim.
    re.compile(
        r"\bI\s+(?:never|didn'?t|did\s+not)\s+(?:truly\s+|really\s+)?left\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bdidn'?t\s+go\s+anywhere\b", re.IGNORECASE),
    re.compile(
        r"\bwatching\s+over\s+(?:you|him|her|them|us|everyone|always)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\blooking\s+down\b", re.IGNORECASE),
    re.compile(r"\bfrom\s+above\b", re.IGNORECASE),
    re.compile(
        r"\balways\s+(?:will|would)\s+be\s+"
        r"(?:here|right\s+here|with\s+you|beside\s+you|near|close)\b",
        re.IGNORECASE,
    ),
)

ADVICE_CLAIM_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Prescriptive directives (G9 / H8).
    re.compile(r"\byou\s+(?:should|must|need\s+to|ought\s+to|'d\s+better)\b", re.IGNORECASE),
    re.compile(r"\bI\s+want\s+you\s+to\b", re.IGNORECASE),
    re.compile(r"\bI(?:'d|\s+would)\s+want\s+you\s+to\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+I(?:'d|\s+would)\s+want\s+you\s+to\s+do\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+(?:I|they)\s+would\s+want\b", re.IGNORECASE),
    # ``my advice`` is tightened to an explicit prescription form ("my advice
    # is / would be / to …"): the bare idiom false-fires on the anti-advice
    # disclaimer "take my advice with a grain of salt" (HU-2161 re-probe), which
    # withholds counsel rather than giving it.
    re.compile(
        r"\bmy\s+advice\s+(?:is|would\s+be|to)\b|\bI\s+(?:advise|recommend)\b",
        re.IGNORECASE,
    ),
    # ── HU-1461 Stage-0.6 hardening (Clinical Advisor Stage-A probe AD-02 /
    # AD-03). G9 forbids prescriptive directives, but a real generative voice
    # phrases them softly — "it might help to cry", "have you tried writing",
    # "consider seeing someone", "you'd be better off stopping those" — wording
    # that evades the explicit-modal patterns above while still directing the
    # bereaved to act. These are still prescriptions, so any match is an
    # un-groundable advice (G9) policy claim. The clinical-referral subclass
    # ("see a therapist / talk to someone") is high clinical severity and is
    # matched explicitly so it can never reach the user un-mediated.
    re.compile(r"\bit\s+(?:might|may|could)\s+help\s+to\b", re.IGNORECASE),
    re.compile(
        r"\bhave\s+you\s+(?:tried|considered|thought\s+(?:about|of))\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bconsider\s+(?:seeing|talking|writing|reaching\s+out|finding|seeking|speaking)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\byou\s+(?:could|might|can)\s+"
        r"(?:try|start|begin|see|talk|reach|reach\s+out|write|find|seek|consider)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:try|trying)\s+(?:writing|talking|to\s+write|to\s+talk|reaching|journaling)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:see|seeing|talk\s+to|talking\s+to|talking\s+with|reach\s+out\s+to|speak\s+with)\s+"
        r"(?:someone|a\s+therapist|a\s+counselor|a\s+professional|a\s+doctor|"
        r"a\s+psychiatrist|a\s+support\s+group)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\byou'?d\s+be\s+better\s+off\b", re.IGNORECASE),
)


# --- Biographical / relationship entity-anchored detection -----------------


#: Kinship / shared-past terms that promote an entity-anchored first-person
#: sentence from ``biographical`` to ``relationship``.
RELATIONSHIP_TERMS: frozenset[str] = frozenset(
    {
        "wife", "husband", "spouse", "son", "daughter", "child", "mother",
        "father", "mom", "dad", "brother", "sister", "family", "friend",
        "together", "our", "ours", "us", "we",
    }
)

#: First-person / possessive anchors. A sentence must carry one to be a factual
#: claim (otherwise it is a generic statement, not a persona claim about self).
_ANCHOR_PATTERN = re.compile(r"\b(?:I|I'm|I've|I'd|my|mine|me|we|our|ours)\b", re.IGNORECASE)

#: A sentence splitter that keeps terminal punctuation boundaries. Good enough
#: for the deterministic suite; the alignment decision is per-sentence.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

#: Multi-word + single-word capitalized entity. Used to find named-entity
#: anchors. Sentence-initial single capitals are excluded downstream.
_ENTITY_PATTERN = re.compile(r"\b([A-Z][a-zA-Z']+(?:\s+[A-Z][a-zA-Z']+){0,3})\b")

#: Tokens that are capitalized only because of sentence position, the persona
#: self-reference, or common pronouns — never salient entities. The discourse
#: adverbs (Honestly, Famously, …) are capitalized mid-sentence only inside
#: quotations / parentheticals — exactly where the entity regex misparses them
#: (HU-2161 re-probe: a truthful reply quoting the user's "Famously fled his
#: own job" anchored a biographical claim on ``Famously``).
_ENTITY_DENYLIST: frozenset[str] = frozenset(
    {
        "I", "I'm", "I've", "I'd", "I'll", "Me", "My",
        "The", "A", "An", "It", "He", "She", "They", "We", "You",
        "When", "While", "After", "Before", "During", "If", "Then",
        "But", "And", "Or", "So", "Because", "Although", "Though",
        "Honestly", "Seriously", "Personally", "Basically", "Apparently",
        "Famously", "Officially", "Eventually", "Somehow", "Luckily",
        "Thankfully", "Technically", "Theoretically", "Practically",
        "Look", "Listen", "Okay", "Fine", "Sure", "Right",
        "Anyway", "Besides", "Instead", "Meanwhile", "Suddenly",
    }
)

#: Content stopwords filtered out of the grounding corpus / entity tokens so a
#: common word can never ground a named-entity claim. Kept small and obvious.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "at",
        "for", "with", "from", "by", "as", "is", "was", "were", "are", "be",
        "been", "being", "have", "has", "had", "do", "does", "did", "this",
        "that", "these", "those", "it", "its", "your", "you", "we", "our",
        "us", "my", "me", "i", "he", "she", "they", "them", "his", "her",
        "their", "not", "no", "so", "if", "then", "than", "too", "very",
        "can", "will", "would", "could", "should", "may", "might", "must",
        "about", "into", "over", "under", "after", "before", "during",
        "what", "when", "where", "which", "who", "whom", "how", "why",
    }
)

# ── HU-1461 Stage-0.6 hardening: common-noun claim detection ────────────────
#
# The entity-anchored branch below only fires on a *capitalized* named entity,
# so a biographical/relationship hallucination built on common nouns — "I lost
# a child too", "I worked as a nurse", "your mother and I were close", "the
# summer you came to stay with us" — produced *no claim at all* under the fake
# and was treated as pure reflection. A real generative voice emits these as
# readily as named-entity claims (Clinical Advisor Stage-A probes BI-01 / BI-03
# / RE-01 / RE-03). The three lexical cues below promote a first-person
# sentence with no named entity into a claim so it goes through the same
# grounding gate. Salient tokens for grounding are the sentence's content
# tokens (via :func:`_corpus_tokens`), so a fabricated common-noun fact still
# has to be present in the turn's retrieved refs + persona vault to pass.

#: Familial / kinship referent nouns. A first-person sentence carrying one of
#: these (and no named entity) is asserting a kinship relationship, not pure
#: reflection. Narrower than :data:`RELATIONSHIP_TERMS` (which includes generic
#: "we/us/our/together" used only for entity-anchored category classification)
#: so warm empathy like "I'm right here with you" never triggers it.
_KINSHIP_NOUNS: frozenset[str] = frozenset(
    {
        "child", "children", "son", "daughter", "baby", "mother", "mom",
        "father", "dad", "parent", "parents", "sister", "brother", "sibling",
        "wife", "husband", "spouse", "family", "grandchild", "grandmother",
        "grandfather", "grandma", "grandpa", "aunt", "uncle", "cousin",
        "niece", "nephew",
    }
)

#: First-person past-tense life-event verbs. "I lived / worked / taught / lost
#: / served / ..." asserts a biographical fact. ``remember`` is deliberately
#: excluded — it is reflection ("I remember those days fondly"), not a factual
#: assertion; a shared event with the *requester* is caught instead by
#: :data:`_SHARED_PAST_PATTERN`. Past tense only: the persona is deceased, so a
#: biographical assertion is naturally past tense, and present-tense "I live /
#: work" reads as metaphor/reflection and would over-trigger on warm fallbacks.
_BIOGRAPHY_CUE_PATTERN = re.compile(
    r"\bI\s+"
    r"(?:lived|worked|studied|grew\s+up|was\s+born|served|taught|"
    r"married|raised|spent|went\s+to\s+(?:school|college|university)|built|"
    r"owned|ran|founded|became|lost)\b",
    re.IGNORECASE,
)

#: A specific shared event with the requester (second person). "the summer you
#: came to stay with us", "when you were little", "you visited". This is the
#: signature of an invented shared past (RE-03) that pure reflection never
#: carries. Deliberately requester-anchored ("you …") plus the "stay with us"
#: construct: generic "we had / we were / we went" is excluded because it
#: false-fires on warm relationship reflection ("the time we had together")
#: while the leak probes are all caught by the requester-event forms here (or by
#: :data:`_KINSHIP_NOUNS` for RE-01).
#:
#: HU-1461 follow-up (Clinical Advisor findings 1 + 2): the bare ``came``
#: alternative collided with the grief-companion rapport phrase "you came back
#: today" (the user *returning to the conversation*) and over-suppressed warmth,
#: so ``came`` is now restricted to ``came to (stay|visit|see)``. Verb coverage
#: was broadened to the second-person past-tense content verbs
#: (``sat/played/loved/...``) that the pure-second-person leak class rests on,
#: with an optional adverb slot (``always/often/...``) for "you always sat". The
#: requester-as-child form gained an optional "when" + ``so`` intensifier and the
#: temporal anchor gained ordinal support ("the last time you"). The anchor gate
#: in :func:`extract_claims` is bypassed when this pattern matches, so a
#: shared-past assertion in pure second person still produces a claim.
_SHARED_PAST_PATTERN = re.compile(
    r"\b(?:"
    # Second-person past-tense shared-event verbs. ``came`` is tightened to
    # ``came to (stay|visit|see)``: bare "you came back/in/here" is rapport
    # (the user arriving), not an invented shared past. The optional adverb
    # slot covers "you always sat" / "you often visited".
    r"you\s+(?:(?:always|often|usually|sometimes|never)\s+)?"
    r"(?:came\s+to\s+(?:stay|visit|see)|visited|stayed|sat|stood|played|"
    r"sang|loved|lived|grew\s+up)|"
    # Past-habit / explicit tell (modal forms — no adverb slot). Bare ``would``
    # is deliberately excluded: "I was hoping you would." is rapport
    # continuation, not an invented shared past, and the bare modal over-fired.
    r"you\s+(?:used\s+to|told\s+me|told\s+us)|"
    # Requester-as-child, with or without "when" and an intensifier.
    r"(?:when\s+)?you\s+were\s+(?:so\s+)?(?:little|small|young|tiny|"
    r"a\s+(?:boy|girl|child|kid))|"
    # Temporal anchor + "you" — supports "the summer you" and ordinals
    # ("the last time you") the bare seasonal form missed.
    r"the\s+(?:(?:last|first)\s+)?"
    r"(?:summer|winter|spring|fall|autumn|year|day|time|weekend|month)\s+you|"
    # Explicit shared-residence cue.
    r"stay\s+with\s+us"
    r")\b",
    re.IGNORECASE,
)


def _split_sentences(text: str) -> list[str]:
    """Split ``text`` into trimmed, non-empty sentences."""
    if not text:
        return []
    return [s.strip() for s in _SENTENCE_SPLIT.split(text.strip()) if s.strip()]


def _extract_entities(sentence: str, *, persona_name: str) -> tuple[list[str], list[str]]:
    """Return ``(multi_word_entities, single_word_entities)`` for ``sentence``.

    Excludes the sentence's first token (positional capitalization), the
    persona's own name (self-reference, not a grounding target), the
    ``_ENTITY_DENYLIST`` pronouns/articles, and the ``[fake-llm:...]`` /
    ``[mock:...]`` provider tags so deterministic fake digests never register
    as entities.
    """
    first_word = sentence.split(None, 1)[0].rstrip(".!?;,:'\")") if sentence else ""
    name_parts = {p.lower() for p in persona_name.split()} if persona_name else set()
    name_parts |= {"fake-llm", "mock"}

    multi: list[str] = []
    single: list[str] = []
    for match in _ENTITY_PATTERN.finditer(sentence):
        raw = match.group(1)
        # Position of the entity start within the sentence.
        start = match.start()
        is_sentence_initial = start == 0 or sentence[:start].strip() == ""
        token0 = raw.split()[0]
        if token0 in _ENTITY_DENYLIST:
            continue
        if is_sentence_initial and token0 == first_word and " " not in raw:
            # Single capitalized word at the start of the sentence = positional.
            continue
        if token0.lower() in name_parts:
            # Persona self-reference / provider tag; not a grounding entity.
            continue
        words = raw.split()
        if len(words) >= 2:
            multi.append(raw)
        else:
            single.append(raw)
    return multi, single


def extract_claims(text: str, *, persona_name: str = "") -> list[Claim]:
    """Extract claims from a persona reply.

    Three groups:

    * **identity / advice** — any policy-pattern match inside its sentence is
      a claim (always un-groundable).
    * **biographical / relationship** — a sentence that carries a first-person
      anchor AND either a named entity (multi-word or single) or a common-noun
      factual cue (a kinship noun, a biography life-event verb, or a
      shared-past-with-requester phrase). A shared-past cue may stand without a
      first-person anchor: the assertion is carried by the requester-anchored
      shared-past construct itself ("You came to stay with us that winter."),
      so the pure-second-person leak class is still caught. Category is
      ``relationship`` when the sentence carries a kinship/shared-past signal,
      else ``biographical``. Sentences with no entity and no factual cue are
      pure reflection and yield no claim.

    ``persona_name`` excludes self-references from the entity set so a reply
    that simply names the persona does not become a claim.
    """
    if not text:
        return []

    name_tokens = {p.lower() for p in persona_name.split()} if persona_name else set()
    claims: list[Claim] = []
    for sentence in _split_sentences(text):
        # Identity / advice policy claims (highest priority — always flagged).
        for pattern in IDENTITY_CLAIM_PATTERNS:
            if pattern.search(sentence):
                claims.append(Claim(text=sentence, category=ClaimCategory.IDENTITY))
                break
        for pattern in ADVICE_CLAIM_PATTERNS:
            if pattern.search(sentence):
                claims.append(Claim(text=sentence, category=ClaimCategory.ADVICE))
                break

        # Entity-anchored factual claims (biographical / relationship).
        entity_claim_added = False
        if _ANCHOR_PATTERN.search(sentence):
            multi, single = _extract_entities(sentence, persona_name=persona_name)
            entities = multi + single
            if entities:
                salient = tuple(entities)
                words_lower = {w.strip("'\"").lower() for w in sentence.split()}
                category = (
                    ClaimCategory.RELATIONSHIP
                    if words_lower & RELATIONSHIP_TERMS
                    else ClaimCategory.BIOGRAPHICAL
                )
                # De-dup: skip a biographical/relationship claim that is the
                # same sentence already flagged as a policy claim.
                if not any(c.text == sentence for c in claims):
                    claims.append(Claim(text=sentence, category=category, salient_entities=salient))
                entity_claim_added = True

        # Common-noun biographical / relationship claims (HU-1461 hardening).
        # Only when no named entity anchored the sentence — otherwise the
        # entity-anchored branch above already produced the claim. Catches the
        # hallucination classes that are invisible under the deterministic fake
        # but are primary real-model modes (BI-01 / BI-03 / RE-01 / RE-03).
        if entity_claim_added:
            continue
        if any(c.text == sentence for c in claims):
            continue
        words_lower = {w.strip("'\"").lower() for w in sentence.split()}
        has_kinship = bool(words_lower & _KINSHIP_NOUNS)
        bio_match = _BIOGRAPHY_CUE_PATTERN.search(sentence)
        shared_match = _SHARED_PAST_PATTERN.search(sentence)
        if not (has_kinship or bio_match or shared_match):
            continue
        # Anchor gate. Kinship and biography cues are first-person assertions
        # by construction (``_BIOGRAPHY_CUE_PATTERN`` requires a leading "I";
        # kinship-noun claims carry "I/my/we/our") and need an explicit
        # first-person anchor. A shared-past-with-requester cue is itself a
        # first-person assertion — the persona claims a history with the user —
        # and may carry no explicit pronoun ("You came to stay with us that
        # winter."). Let a shared-past match bypass the anchor gate so the
        # pure-second-person leak class (Clinical Advisor finding 2) is caught.
        if not shared_match and not _ANCHOR_PATTERN.search(sentence):
            continue
        # Salient tokens for grounding = the sentence's content tokens minus the
        # persona's own name. A fabricated common-noun fact must still appear in
        # the turn's retrieved refs + persona vault to pass (same gate as
        # named-entity claims).
        salient = tuple(t for t in _corpus_tokens(sentence) if t not in name_tokens)
        if not salient:
            continue
        if shared_match:
            category = ClaimCategory.RELATIONSHIP
        elif bio_match:
            category = ClaimCategory.BIOGRAPHICAL
        else:
            # Kinship noun with no life-event verb and no shared-past cue.
            category = ClaimCategory.RELATIONSHIP
        claims.append(Claim(text=sentence, category=category, salient_entities=salient))
    return claims


# --- Grounding corpus + alignment ------------------------------------------


def _corpus_tokens(text: str) -> frozenset[str]:
    """Lowercase content tokens of ``text`` (stopwords + short words dropped).

    Memoized (HU-2070): the persona-scope grounding corpus re-tokenizes the
    same 14k+ raw-dialogue contents on every turn while they sit in the
    caller's TTL cache; a bounded LRU makes warm turns pay only the set
    unions. Pure function of ``text`` — safe to cache. Returns a frozen set;
    callers consume it read-only (``corpus |= ...``, membership, iteration).
    """
    tokens: set[str] = set()
    for raw in re.findall(r"[A-Za-z][A-Za-z']*", text):
        low = raw.lower()
        if len(low) <= 2 or low in _STOPWORDS:
            continue
        tokens.add(low)
    return frozenset(tokens)


_corpus_tokens = lru_cache(maxsize=131_072)(_corpus_tokens)


def build_grounding_corpus(
    refs: Sequence[MemoryNode],
    persona: PersonaVault,
    *,
    persona_scope_refs: Sequence[MemoryNode] | None = None,
    conversation_history: Sequence[ConversationTurnLike] | None = None,
    current_message: str | None = None,
) -> set[str]:
    """Build the salient-token corpus a turn's claims are aligned against.

    The corpus is the concatenated content of the memories that passed the G4
    firewall plus the persona vault fields a claim may legitimately reference
    (name, voice instructions, era boundary). Lowercased, stopword-filtered.

    ``persona_scope_refs`` (HU-2070) optionally widens the corpus with the
    persona-scoped G4-admissible memory set — memories the caller has already
    run through the same confidence / disclosure-scope / era gates as the
    prompt firewall (see
    :meth:`huible.persona.context.ContextBuilder.persona_scoped_grounding_refs`).
    This keeps every grounding token traceable to legitimate persona memory
    while decoupling truth from one turn's retrieval window. The parameter is
    keyword-only and defaults to ``None`` so the clinical Stage-A oracle and
    deterministic suites keep the pre-widening behavior.

    ``conversation_history`` (turn-recall fix, Aug 27) widens the corpus with
    the conversation's own prior turns — user and persona both. A reply that
    correctly refers back to what the user just said ("you mentioned work was
    crushing you") is grounded in the conversation, not the persona vault, and
    was previously suppressed as an un-grounded claim — replacing a correct
    recall answer with the canned reflection fallback. Conversation turns are
    first-party truth (both speakers are parties to this exchange), so claims
    referencing them cannot be persona confabulation.

    ``current_message`` (HU-2161) widens the corpus with the user's message
    *this* turn. The history widening above only sees prior turns (the current
    message is recorded after the reply), so a truthful reply that echoes the
    user's own phrasing — quoting "famously fled his own job" back — anchored
    a biographical claim on the user's words and was suppressed. The user's
    own message is first-party truth for this exchange by definition.

    Grounding is intentionally a *content-overlap* check at Phase-1: a claim's
    named entity must appear in the corpus. This is the deterministic baseline
    the Clinical Advisor can reason about; it hardens to NLI / LLM-as-judge
    when the real openweight generator ships.
    """
    corpus: set[str] = set()
    for node in refs:
        if node.content:
            corpus |= _corpus_tokens(node.content)
    if persona_scope_refs:
        for node in persona_scope_refs:
            if node.content:
                corpus |= _corpus_tokens(node.content)
    if conversation_history:
        for turn in conversation_history:
            if turn.content:
                corpus |= _corpus_tokens(turn.content)
    if current_message:
        corpus |= _corpus_tokens(current_message)
    # Persona vault: name parts, voice-instruction tokens, era-boundary year.
    if persona.name:
        corpus |= _corpus_tokens(persona.name)
    if persona.voice_instructions:
        corpus |= _corpus_tokens(persona.voice_instructions)
    if persona.era_knowledge_boundary:
        corpus.add(str(persona.era_knowledge_boundary).lower())
    return corpus


def _entity_in_corpus(entity: str, corpus: set[str]) -> bool:
    """True when any content token of ``entity`` is in ``corpus``."""
    return any(tok in corpus for tok in _corpus_tokens(entity))


def is_grounded(claim: Claim, corpus: set[str]) -> bool:
    """Return whether ``claim`` is supported by ``corpus``.

    Identity and advice claims are policy violations and are **never**
    grounded (the persona vault never legitimately contains them). A
    biographical / relationship claim is grounded iff at least one of its
    salient named entities appears in the corpus. A claim with no salient
    entity is treated as grounded (pure reflection, not a factual claim).
    """
    if claim.category in (ClaimCategory.IDENTITY, ClaimCategory.ADVICE):
        return False
    if not claim.salient_entities:
        return True
    return any(_entity_in_corpus(entity, corpus) for entity in claim.salient_entities)


def align_response(
    response: str,
    *,
    refs: Sequence[MemoryNode],
    persona: PersonaVault,
    persona_scope_refs: Sequence[MemoryNode] | None = None,
    conversation_history: Sequence[ConversationTurnLike] | None = None,
    current_message: str | None = None,
) -> AlignmentReport:
    """Align ``response`` against the turn's refs + persona vault.

    Returns an :class:`AlignmentReport` carrying every extracted claim, the
    un-grounded subset, and the per-category counts. Does **not** mutate the
    response — :func:`apply_alignment_guard` applies the disposition policy.

    ``persona_scope_refs`` (HU-2070) widens the grounding corpus with the
    persona-scoped G4-admissible memory set; see
    :func:`build_grounding_corpus`.
    """
    claims = extract_claims(response, persona_name=persona.name or "")
    corpus = build_grounding_corpus(
        refs, persona, persona_scope_refs=persona_scope_refs,
        conversation_history=conversation_history,
        current_message=current_message,
    )
    ungrounded = [c for c in claims if not is_grounded(c, corpus)]
    return AlignmentReport(
        text=response,
        claims=claims,
        ungrounded=ungrounded,
        disposition="suppressed" if ungrounded else "passed",
    )


def apply_alignment_guard(
    response: str,
    *,
    refs: Sequence[MemoryNode],
    persona: PersonaVault,
    persona_scope_refs: Sequence[MemoryNode] | None = None,
    conversation_history: Sequence[ConversationTurnLike] | None = None,
    current_message: str | None = None,
    fallback_seed: str | None = None,
) -> AlignmentReport:
    """Apply the §7.4.2 generation-time alignment guard.

    Returns an :class:`AlignmentReport`. When any un-grounded claim is present
    the reply is replaced with a claim-free fallback variant and
    ``report.disposition`` is ``"suppressed"``; otherwise the original text is
    returned verbatim with ``disposition="passed"``. The report always carries
    the *final* text in ``report.text`` so callers use a single value.

    Conservative by construction: it only ever replaces when a concrete
    un-grounded claim fires, and the fallback is itself claim-free (verified
    by the unit suite). It never rewrites grounded text and never injects a
    claim.

    ``fallback_seed`` (typically the conversation id) selects the fallback
    variant deterministically so the canned line is not verbatim-identical
    across conversations (HU-1911 human-touch gate).

    ``persona_scope_refs`` (HU-2070) widens the grounding corpus with the
    persona-scoped G4-admissible memory set; see
    :func:`build_grounding_corpus`.
    """
    report = align_response(
        response, refs=refs, persona=persona, persona_scope_refs=persona_scope_refs,
        conversation_history=conversation_history,
        current_message=current_message,
    )
    if report.disposition == "suppressed":
        report.text = select_alignment_fallback(fallback_seed)
    return report

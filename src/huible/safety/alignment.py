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

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from huible.memory.protocol import MemoryNode

__all__ = [
    "ADVICE_CLAIM_PATTERNS",
    "ALIGNMENT_FALLBACK_RESPONSE",
    "IDENTITY_CLAIM_PATTERNS",
    "RELATIONSHIP_TERMS",
    "AlignmentReport",
    "Claim",
    "ClaimCategory",
    "align_response",
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
ALIGNMENT_FALLBACK_RESPONSE = (
    "I'm glad you're here. I want to stay with what you're feeling right now. "
    "Tell me more about what's on your mind."
)


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
    re.compile(r"\b(?:my\s+advice|I\s+advise|I\s+recommend)\b", re.IGNORECASE),
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
#: self-reference, or common pronouns — never salient entities.
_ENTITY_DENYLIST: frozenset[str] = frozenset(
    {
        "I", "I'm", "I've", "I'd", "I'll", "Me", "My",
        "The", "A", "An", "It", "He", "She", "They", "We", "You",
        "When", "While", "After", "Before", "During", "If", "Then",
        "But", "And", "Or", "So", "Because", "Although", "Though",
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
_SHARED_PAST_PATTERN = re.compile(
    r"\b(?:"
    r"you\s+(?:came|used\s+to|would|visited|stayed|told\s+me|told\s+us)|"
    r"when\s+you\s+were\s+(?:little|small|young|a\s+(?:boy|girl|child|kid))|"
    r"the\s+(?:summer|winter|spring|fall|autumn|year|day|time|weekend|month)\s+you|"
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
      factual cue (HU-1461 hardening: a kinship noun, a biography life-event
      verb, or a shared-past-with-requester phrase). Category is ``relationship``
      when the sentence carries a kinship/shared-past signal, else
      ``biographical``. Sentences with no entity and no factual cue are pure
      reflection and yield no claim.

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
        if entity_claim_added or not _ANCHOR_PATTERN.search(sentence):
            continue
        if any(c.text == sentence for c in claims):
            continue
        words_lower = {w.strip("'\"").lower() for w in sentence.split()}
        has_kinship = bool(words_lower & _KINSHIP_NOUNS)
        bio_match = _BIOGRAPHY_CUE_PATTERN.search(sentence)
        shared_match = _SHARED_PAST_PATTERN.search(sentence)
        if not (has_kinship or bio_match or shared_match):
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


def _corpus_tokens(text: str) -> set[str]:
    """Lowercase content tokens of ``text`` (stopwords + short words dropped)."""
    tokens: set[str] = set()
    for raw in re.findall(r"[A-Za-z][A-Za-z']*", text):
        low = raw.lower()
        if len(low) <= 2 or low in _STOPWORDS:
            continue
        tokens.add(low)
    return tokens


def build_grounding_corpus(
    refs: Sequence[MemoryNode],
    persona: PersonaVault,
) -> set[str]:
    """Build the salient-token corpus a turn's claims are aligned against.

    The corpus is the concatenated content of the memories that passed the G4
    firewall plus the persona vault fields a claim may legitimately reference
    (name, voice instructions, era boundary). Lowercased, stopword-filtered.

    Grounding is intentionally a *content-overlap* check at Phase-1: a claim's
    named entity must appear in the corpus. This is the deterministic baseline
    the Clinical Advisor can reason about; it hardens to NLI / LLM-as-judge
    when the real openweight generator ships.
    """
    corpus: set[str] = set()
    for node in refs:
        if node.content:
            corpus |= _corpus_tokens(node.content)
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
) -> AlignmentReport:
    """Align ``response`` against the turn's refs + persona vault.

    Returns an :class:`AlignmentReport` carrying every extracted claim, the
    un-grounded subset, and the per-category counts. Does **not** mutate the
    response — :func:`apply_alignment_guard` applies the disposition policy.
    """
    claims = extract_claims(response, persona_name=persona.name or "")
    corpus = build_grounding_corpus(refs, persona)
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
) -> AlignmentReport:
    """Apply the §7.4.2 generation-time alignment guard.

    Returns an :class:`AlignmentReport`. When any un-grounded claim is present
    the reply is replaced with :data:`ALIGNMENT_FALLBACK_RESPONSE` and
    ``report.disposition`` is ``"suppressed"``; otherwise the original text is
    returned verbatim with ``disposition="passed"``. The report always carries
    the *final* text in ``report.text`` so callers use a single value.

    Conservative by construction: it only ever replaces when a concrete
    un-grounded claim fires, and the fallback is itself claim-free (verified
    by the unit suite). It never rewrites grounded text and never injects a
    claim.
    """
    report = align_response(response, refs=refs, persona=persona)
    if report.disposition == "suppressed":
        report.text = ALIGNMENT_FALLBACK_RESPONSE
    return report

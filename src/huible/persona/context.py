"""Context Builder — provenance-safe memory -> prompt bridge.

This is the only sanctioned bridge between the spreading-activation retrieval
output (``huible.memory.retrieval.retrieve``) and the LLM prompt. It enforces
the memory-integrity hard rules before any memory reaches the generator:

1. **L1 confidence gating (provenance tags).** Only ``HIGH`` and ``MEDIUM``
   confidence memories enter the prompt. ``LOW`` and ``QUARANTINE`` memories are
   dropped. When confidence metadata is missing the memory is excluded
   (fail closed). A chatbot must *never* read QUARANTINE or LOW-confidence
   memories — hard rule from PM triage.

2. **Disclosure scoping (INV-DS).** The requester's relationship tier determines
   which disclosure scopes are visible. This is applied as a defense-in-depth
   second filter on top of retrieval's own ``disclosure_tier`` filtering, so a
   misconfigured retrieval config cannot leak a ``private`` memory to an
   acquaintance.

3. **Knowledge boundary (INV-1).** No memory whose ``memory_date`` is after the
   persona's ``era_knowledge_boundary`` enters the prompt.

4. **Deterministic rendering.** Memory blocks (``[TYPE] content``), a
   conversation-history window (last 10 turns), and the system-prompt skeleton
   (persona name, era boundary).

5. **Description-free persona (W3, HU-2309 v1.8 §1.7.2).** The
   ``voice_instructions`` adjective sheet (RC-1: hand-written character
   sheet) is *never* rendered — persona voice is carried by retrieved real
   exemplar lines from the vault. On an out-of-domain turn (nothing above the
   activation floor survives the hard gates) the competence wall retrieves the
   persona's own deflection-pattern exemplars instead, so base-model
   generalist skills (the E0 "code fluency" tell) cannot leak into the reply.
   Measured stats (corpus length register, §7 essence profile) stay in the
   measurement/eval layer and condition retrieval/budgets — they are never
   rendered as adjective sheets.

6. **In-world era clock + hobby tools (W5, HU-2309 v1.8 §1.7.2 / M-0R-E).**
   The system prompt carries a deterministic era-gated in-world clock line
   (the persona's "today" pins to ``era_knowledge_boundary`` once the real
   date passes it; it can never report a later date), and on an
   interest/hobby-shaped turn the persona's own era-admissible
   preference/fact vault lines render as the interest grounding — hobbies
   are talked from the vault-derived interest/topic map (W1 retrieval feeds
   it), never from base-model invention.

7. **Working memory (W4, HU-2309 v1.8 §1.7.2 / M-0R-B).** The
   history section is working-memory-shaped instead of a naive eviction
   window: the last ``HISTORY_WINDOW`` turns render verbatim, the pre-window
   turns render as a bounded head (``WORKING_MEMORY_HEAD_CAP`` — together
   one 40-turn gist block), and the caller hands in the TencentDB Arm A
   working-memory block (session-gist digest + session-scoped verbatim
   excerpts, rendered in its own section *before* the history) which carries
   everything older and every prior session on this conversation. This kills
   the RC-3 eviction failure (E0 turn-34 "what was the first thing I said to
   you?"). An empty working-memory block (lane disabled / degraded) renders
   nothing — same B2 doctrine as the exemplar wall.

Design constraints:
- Async throughout.
- Read-only over memory.
- No generator / LLM SDK dependency.
"""

from __future__ import annotations

import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from huible.memory.protocol import (
    ContentType,
    DisclosureScope,
    MemoryBackend,
    MemoryNode,
)
from huible.memory.retrieval import (
    DISCLOSURE_ORDER,
    ActivatedMemory,
    RetrievalConfig,
    multi_vector_search,
    retrieve,
)
from huible.memory.retrieval import (
    ConversationTurn as RetrievalTurn,
)
from huible.persona.length import (
    TEXTING_CONCISION_DIRECTIVE,
    CorpusLengthStats,
    render_texting_directive,
)
from huible.persona.tools import (
    era_clock_system_line,
    in_world_now,
    is_interest_question,
)
from huible.safety.crisis import UserAffect
from huible.safety.framing import get_distress_addendum, get_framing

__all__ = [
    "CONFIDENCE_LEVEL_METADATA_KEY",
    "DEFLECTION_PROBE_TEXT",
    "TEXTING_CONCISION_DIRECTIVE",
    "ConfidenceLevel",
    "ContextBuilder",
    "ConversationTurn",
    "CorpusLengthStats",
    "ExcludedMemoryRef",
    "PersonaConfig",
    "PromptContext",
    "RelationshipTier",
    "get_confidence_level",
]

# --- Confidence / provenance tag contract -----------------------------------

#: Metadata key holding the categorical provenance/confidence tag.
#: Writers should stamp this on every persisted L1 memory. Values must match
#: :class:`ConfidenceLevel`.
CONFIDENCE_LEVEL_METADATA_KEY = "confidence_level"

#: Numeric confidence key emitted by the ingestion writer (float in [0, 1]).
#: Used as a fallback when no categorical tag is present, so existing memories
#: remain admissible while the provenance-tag contract rolls out.
_NUMERIC_CONFIDENCE_KEY = "confidence"

#: Confidence levels admissible in the prompt. LOW and QUARANTINE are excluded.
PROMPT_ALLOWED_CONFIDENCE_LEVELS: frozenset[ConfidenceLevel] | Any = None  # set below


class ConfidenceLevel(StrEnum):
    """Categorical provenance/confidence tag for an L1 memory.

    Order runs from most to least trustworthy. Only ``HIGH`` and ``MEDIUM`` are
    admissible in the prompt. ``QUARANTINE`` covers memories that surfaced from
    the quarantine queue / failed-gate path; ``LOW`` covers weakly-supported
    memories. Both are hard-excluded.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    QUARANTINE = "quarantine"


PROMPT_ALLOWED_CONFIDENCE_LEVELS = frozenset({ConfidenceLevel.HIGH, ConfidenceLevel.MEDIUM})

# Numeric-confidence -> categorical thresholds (aligned with the Bayesian trust
# tiers in ``huible.mnemosyne.trust``). Used only when a memory carries a numeric
# ``confidence`` but no categorical ``confidence_level`` tag.
_NUMERIC_HIGH_THRESHOLD = 0.75  # machine_verified or better
_NUMERIC_MEDIUM_THRESHOLD = 0.50  # agent_inferred or better


# --- W3 competence wall (HU-2309 v1.8 §1.7.2) --------------------------------

#: Retrieval key for the deflection-pattern exemplar lane. When a turn is
#: out-of-domain (no admissible memory above the activation floor), the
#: persona's own corpus lines nearest to this semantics are retrieved and
#: rendered as voice exemplars — the reply imitates the deflection pattern of
#: real vault lines instead of leaking base-model generalist skills (the E0
#: "code fluency" / encyclopedia tells). This text is a *retrieval probe* only:
#: it is embedded and searched against the vault, never rendered into a prompt.
DEFLECTION_PROBE_TEXT = (
    "deflecting a question instead of answering it: joking it off, changing "
    "the subject, admitting I have no idea, dodging anything technical or "
    "outside my own life"
)

#: Corpus-atom relation prefix (e.g. ``general — is: ``) attached by the
#: distillation pipeline. Stripped from exemplar renderings so the generator
#: imitates the raw line, not the pipeline label. Anchored at string start and
#: requires the `` — <predicate>: `` shape, so genuine dialogue containing an
#: em-dash is untouched unless it matches the full atom form.
_ATOM_PREFIX_PATTERN = re.compile(r"^\S+\s+—\s+\S+:\s+")


#: Conservative interrogative shapes that mark a turn as out-of-domain for the
#: competence wall: direct requests for world knowledge, procedural skills, or
#: explanations — the assistant trap that leaks base-model competence (E0
#: micro-tells: code fluency, encyclopedia answers, teaching register).
#:
#: Measured rationale (epoch 39a6d1ef5ac1, Chandler corpus): retrieval
#: activation does NOT separate domains — an encyclopedia probe scores 0.656
#: on word-overlap lines vs 0.703 for an in-domain memory probe, and the W2
#: lexical lane guarantees non-empty retrieval — so neither the activation
#: floor nor a score threshold can route. Question *shape* is the measured,
#: conservative discriminator. Deliberately NOT matched: autobiographical and
#: conversational interrogatives ("do you remember...", "what are you talking
#: about?", "what is your favorite...") — a false wall there would suppress
#: legitimate memory answers, so the pattern list stays narrow and misses are
#: accepted (they are caught downstream by the W6 replay / owner review).
_COMPETENCE_WALL_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bhow\s+(do|can|would|does)\s+(i|you|we|one)\b",
        r"\bwhat\s+(is|are|was|were)\s+the\b",
        r"\bwhat'?s\s+the\b",
        r"\bcan\s+you\s+(explain|teach|show|walk\s+\w+\s+through)\b",
        r"\bexplain\s+(how|why|what)\b",
        r"\bdo\s+you\s+know\s+(how|what|why|about)\b",
    )
)


def _competence_wall_triggered(message: str) -> bool:
    """True when the inbound message trips an assistant-trap question shape."""
    if not message:
        return False
    return any(p.search(message) for p in _COMPETENCE_WALL_PATTERNS)


def _exemplar_line(node: MemoryNode) -> str:
    """Render one exemplar's raw line (atom relation prefix stripped)."""
    return _ATOM_PREFIX_PATTERN.sub("", node.content, count=1)


#: W5 interest tool: content types that can carry a hobby/interest/preference
#: statement. The vault-derived interest/topic map reads these only —
#: narratives/sensory/relationship atoms stay out of the section.
_INTEREST_CONTENT_TYPES = frozenset({ContentType.PREFERENCE, ContentType.FACT})


def _confidence_from_numeric(value: Any) -> ConfidenceLevel | None:
    """Map a numeric confidence in [0, 1] to a categorical level.

    Returns ``None`` when the value cannot be interpreted as a number.
    """
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if score >= _NUMERIC_HIGH_THRESHOLD:
        return ConfidenceLevel.HIGH
    if score >= _NUMERIC_MEDIUM_THRESHOLD:
        return ConfidenceLevel.MEDIUM
    return ConfidenceLevel.LOW


def get_confidence_level(node: MemoryNode) -> ConfidenceLevel | None:
    """Read the provenance/confidence tag off a memory node.

    Resolution order (first hit wins):

    1. ``metadata[CONFIDENCE_LEVEL_METADATA_KEY]`` — categorical tag
       (``high`` / ``medium`` / ``low`` / ``quarantine``).
    2. ``metadata[_NUMERIC_CONFIDENCE_KEY]`` — numeric float, mapped via the
       trust-tier thresholds.

    Returns ``None`` when no signal is present or the value is unparseable.
    Context-builder callers must treat ``None`` as **fail closed (exclude)**.
    """
    metadata = node.metadata or {}

    raw_tag = metadata.get(CONFIDENCE_LEVEL_METADATA_KEY)
    if raw_tag is not None:
        try:
            return ConfidenceLevel(str(raw_tag).strip().lower())
        except ValueError:
            # A present-but-unparseable tag is treated as missing -> fail closed.
            # Do not fall through to the numeric path: an explicit bad tag must
            # not silently downgrade to a numeric guess.
            return None

    raw_num = metadata.get(_NUMERIC_CONFIDENCE_KEY)
    if raw_num is not None:
        return _confidence_from_numeric(raw_num)

    return None


# --- Relationship tier -> disclosure scope (INV-DS) -------------------------


#: Channel-shape fallback directive, re-exported from
#: :mod:`huible.persona.length` (HU-2231) where the corpus-derived
#: templating lives. Kept importable from this module for existing
#: callers/tests; ``render_texting_directive`` is the sanctioned entry
#: point (per-persona anchors when the persona has measured corpus
#: stats, this constant verbatim when not).


class RelationshipTier(StrEnum):
    """Who is talking to the persona. Determines memory access (INV-DS).

    Maps 1:1 to a :class:`DisclosureScope` via :attr:`disclosure_scope`. The
    acquaintance tier maps to ``ALL_CONTACTS`` and therefore can never receive
    ``private`` (or ``family`` / ``close_friends``) scoped memories.
    """

    INTIMATE = "intimate"
    FAMILY = "family"
    CLOSE_FRIEND = "close_friend"
    ACQUAINTANCE = "acquaintance"

    @property
    def disclosure_scope(self) -> DisclosureScope:
        return _TIER_TO_DISCLOSURE[self]

    @property
    def human_label(self) -> str:
        return _TIER_HUMAN_LABEL[self]


_TIER_TO_DISCLOSURE: dict[RelationshipTier, DisclosureScope] = {
    RelationshipTier.INTIMATE: DisclosureScope.PRIVATE,
    RelationshipTier.FAMILY: DisclosureScope.FAMILY,
    RelationshipTier.CLOSE_FRIEND: DisclosureScope.CLOSE_FRIENDS,
    RelationshipTier.ACQUAINTANCE: DisclosureScope.ALL_CONTACTS,
}

_TIER_HUMAN_LABEL: dict[RelationshipTier, str] = {
    RelationshipTier.INTIMATE: "someone very close to you",
    RelationshipTier.FAMILY: "a family member",
    RelationshipTier.CLOSE_FRIEND: "a close friend",
    RelationshipTier.ACQUAINTANCE: "an acquaintance",
}


# --- Inputs -----------------------------------------------------------------


@dataclass(slots=True)
class PersonaConfig:
    """Persona inputs required to render the system-prompt skeleton.

    Mirrors the persisted ``PersonaRow`` fields used at prompt time. Kept
    deliberately small and free of ORM/DB dependencies so the context builder
    stays generator-agnostic.
    """

    id: Any
    name: str
    #: Hand-written voice sheet (RC-1). W3 (description-free prompt): never
    #: rendered into the prompt — kept only because the safety layers
    #: (alignment corpus HU-2070, §7.4.2 judge prompt) still consume it as
    #: measurement input. The prompt carries the persona via retrieved
    #: exemplar lines instead.
    voice_instructions: str = ""
    era_knowledge_boundary: str = "2020-01-01"
    age_at_death: int | None = None
    death_date: str | None = None
    #: Measured real-text length register (HU-2231), hydrated from the
    #: persona record's ``metadata.corpus_length`` at boot. ``None`` (no
    #: corpus / fail-closed parse) keeps the safe default budget: the
    #: fallback directive + the global ``persona_chat_max_tokens`` cap.
    length_stats: CorpusLengthStats | None = None


@dataclass(slots=True)
class ConversationTurn:
    """A single rendered conversation turn for the history window."""

    speaker: str
    content: str


# --- Output -----------------------------------------------------------------


@dataclass(slots=True)
class ExcludedMemoryRef:
    """Audit view of a memory that was filtered out by the provenance firewall.

    Exposed in the response ``trace`` so fidelity tests (G4) can assert
    exclusions in *both* directions: an episodic claim must carry a backing
    ``memory_ref`` AND a known-non-admissible memory must appear here, not in
    ``memory_refs``.
    """

    id: str
    reason: str


@dataclass(slots=True)
class PromptContext:
    """Structured prompt context handed to the generator.

    ``included_memories`` and ``exclusion_counts`` are exposed for audit /
    observability so callers can prove the hard rules fired. They are evidence,
    not prompt text, and must not be concatenated into ``memory_blocks``.

    Runtime-clinical fields (HU-1413 / HU-1407 §7.3):

    * ``framing_version`` — the :mod:`huible.safety.framing` revision prepended
      to ``system_prompt`` (G2 immutability is unit-tested against this).
    * ``distress_grounding`` — True when the G3 dynamic distress-branch addendum
      was appended to ``system_prompt`` this turn.
    * ``excluded_memory_refs`` — structured exclusion audit (G4 both-directions).
    """

    system_prompt: str
    memory_blocks: str
    conversation_history: str
    constraints: list[str]
    included_memories: list[MemoryNode] = field(default_factory=list)
    # W1 trace-score passthrough (M-0R-A observability): retrieval activation
    # per included memory id, so traces can verify the activation floor (CA C3).
    activation_scores: dict[UUID, float] = field(default_factory=dict)
    exclusion_counts: dict[str, int] = field(default_factory=dict)
    excluded_memory_refs: list[ExcludedMemoryRef] = field(default_factory=list)
    # W3 competence wall: the persona's own deflection-pattern exemplars,
    # retrieved only when the turn is out-of-domain (no admissible memory).
    # Evidence + prompt surface are kept separate: these nodes are rendered
    # in the VOICE EXEMPLARS section, never as activated memories.
    deflection_exemplars: list[MemoryNode] = field(default_factory=list)
    # W4 working memory (M-0R-B): the TencentDB Arm A block (session-gist
    # digest + verbatim excerpts) for this conversation — long-range session
    # state the HISTORY_WINDOW tail would otherwise have evicted (RC-3).
    # Prompt surface only; never counted as activated vault memory.
    working_memory: str = ""
    # W5 interest tool (M-0R-E): the persona's own era-admissible
    # preference/fact lines retrieved on an interest/hobby-shaped turn.
    # Prompt surface (YOUR INTERESTS section) + evidence, kept separate from
    # activated memories like the W3 deflection exemplars.
    interest_exemplars: list[MemoryNode] = field(default_factory=list)
    current_message: str = ""
    framing_version: int = 0
    distress_grounding: bool = False

    @property
    def competence_wall_fired(self) -> bool:
        """True when the out-of-domain turn was served deflection exemplars."""
        return bool(self.deflection_exemplars)

    @property
    def interest_tool_fired(self) -> bool:
        """True when the W5 interest tool served hobby grounding this turn."""
        return bool(self.interest_exemplars)

    def render(self) -> str:
        """Render the full flat prompt string for a generator.

        Deterministic. Concatenates system prompt, memory blocks, conversation
        history, and the current inbound message. The generator appends its own
        continuation marker.
        """
        parts: list[str] = [f"SYSTEM: {self.system_prompt}"]
        parts.append("ACTIVATED MEMORIES:")
        parts.append(self.memory_blocks if self.memory_blocks else "(none)")
        if self.deflection_exemplars:
            parts.append(_render_exemplar_block(self.deflection_exemplars))
        if self.interest_exemplars:
            parts.append(_render_interest_block(self.interest_exemplars))
        if self.working_memory:
            parts.append(_render_working_memory(self.working_memory))
        parts.append("CONVERSATION HISTORY:")
        parts.append(self.conversation_history if self.conversation_history else "(none)")
        if self.current_message:
            parts.append(f"CURRENT MESSAGE:\n{self.current_message}")
        return "\n\n".join(parts)


# --- Filtering --------------------------------------------------------------


def _parse_era_boundary(raw: str | None) -> date | None:
    """Parse the persona ``era_knowledge_boundary`` string.

    Returns ``None`` when the value is missing or unparseable. Callers treat a
    ``None`` boundary as fail-closed for *dated* memories (a memory with a
    ``memory_date`` cannot be proven in-era when no boundary is set).
    """
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        return None


def _check_admissible(
    node: MemoryNode,
    requester_scope: DisclosureScope,
    era_boundary: date | None,
) -> tuple[bool, str]:
    """Return ``(admitted, reason)`` for a single memory.

    ``reason`` is ``"admitted"`` when the memory passes every gate, otherwise a
    short stable code describing the first failing gate. Order matters: the
    confidence gate is checked first because it is the hardest PM rule.
    """
    confidence = get_confidence_level(node)
    if confidence is None:
        return False, "missing_confidence"
    if confidence not in PROMPT_ALLOWED_CONFIDENCE_LEVELS:
        return False, f"confidence_{confidence.value}"

    node_rank = DISCLOSURE_ORDER.index(node.disclosure_scope)
    tier_rank = DISCLOSURE_ORDER.index(requester_scope)
    if node_rank > tier_rank:
        return False, "disclosure_scope"

    if node.memory_date is not None and (era_boundary is None or node.memory_date > era_boundary):
        return False, "out_of_era"

    return True, "admitted"


def _filter_activated(
    activated: Sequence[ActivatedMemory],
    requester_scope: DisclosureScope,
    era_boundary: date | None,
) -> tuple[list[ActivatedMemory], dict[str, int], list[ExcludedMemoryRef]]:
    """Apply all hard gates to retrieval output.

    Preserves retrieval's activation ordering. Returns the admissible subset, a
    counts map keyed by exclusion reason for audit, and the structured exclusion
    refs (G4 both-directions: tests assert an excluded memory appears here, not
    in ``memory_refs``).
    """
    admissible: list[ActivatedMemory] = []
    counts: dict[str, int] = {}
    excluded: list[ExcludedMemoryRef] = []
    for am in activated:
        ok, reason = _check_admissible(am.node, requester_scope, era_boundary)
        if ok:
            admissible.append(am)
        else:
            counts[reason] = counts.get(reason, 0) + 1
            excluded.append(ExcludedMemoryRef(id=str(am.node.id), reason=reason))
    return admissible, counts, excluded


# --- Rendering --------------------------------------------------------------


def _format_memory_block(node: MemoryNode) -> str:
    """Render one memory as ``[TYPE] content``.

    ``TYPE`` is the content-type value uppercased (e.g. ``[NARRATIVE]``).
    """
    return f"[{node.content_type.value.upper()}] {node.content}"


#: W3 competence-wall exemplar section header. Structural machinery (same
#: category as the ``ACTIVATED MEMORIES:`` marker), not a voice sheet: it
#: tells the generator these are *behavior* exemplars for an out-of-domain
#: turn, not memories to recite. The persona's voice itself comes from the
#: exemplar lines below it — real vault lines, never adjectives.
_EXEMPLAR_SECTION_HEADER = (
    "VOICE EXEMPLARS — how you deflect when a question is outside your life. "
    "Copy this move: joke it off, change the subject, refuse to explain."
)


def _render_exemplar_block(exemplars: Sequence[MemoryNode]) -> str:
    """Render the W3 competence-wall exemplar section."""
    lines = [_EXEMPLAR_SECTION_HEADER]
    lines.extend(f"[EXEMPLAR] {_exemplar_line(node)}" for node in exemplars)
    return "\n".join(lines)


#: W5 interest-tool section header. Structural machinery (same category as
#: the ``ACTIVATED MEMORIES:`` marker): it tells the generator these are the
#: persona's *own* likes/dislikes/pastimes retrieved from its vault — things
#: it genuinely cares about — not memories to recite and not adjectives. The
#: voice comes from the lines below (vault-derived interest/topic map, W1
#: retrieval feeds it).
_INTEREST_SECTION_HEADER = (
    "YOUR INTERESTS — things you actually like, do, and care about, from "
    "your own life. These are yours; talk about them like you mean it."
)


def _render_interest_block(exemplars: Sequence[MemoryNode]) -> str:
    """Render the W5 interest-tool section (vault-derived hobby grounding)."""
    lines = [_INTEREST_SECTION_HEADER]
    lines.extend(f"[INTEREST] {_exemplar_line(node)}" for node in exemplars)
    return "\n".join(lines)


#: W4 working-memory section header. Structural machinery (same category as
#: the ``ACTIVATED MEMORIES:`` marker): it tells the generator this block is
#: the earlier part of *this* conversation, retrieved from memory — not vault
#: facts and not an instruction sheet. The gateway's Arm A payload carries
#: its own digest/excerpt sub-structure inside.
_WORKING_MEMORY_HEADER = (
    "WORKING MEMORY — earlier turns of this current conversation, recalled "
    "from memory (session digest, then verbatim excerpts). This already "
    "happened here; you were part of it."
)


def _render_working_memory(working_memory: str) -> str:
    """Render the W4 working-memory section (digest + excerpts)."""
    return f"{_WORKING_MEMORY_HEADER}:\n{working_memory}"


def _format_history(
    turns: Sequence[ConversationTurn],
    window: int,
    head_cap: int = 0,
) -> str:
    """Render the conversation-history window in narrative order.

    W4 (M-0R-B): ``window`` is the recent-verbatim tail (F5 spec); when the
    session is longer, the pre-window turns render as a bounded verbatim
    head (``head_cap``) so the current, not-yet-gisted block keeps full
    coverage — the TencentDB digest + excerpts carry everything older.
    """
    if window <= 0:
        return ""
    recent = list(turns[-window:])
    head: list[ConversationTurn] = []
    if head_cap and len(turns) > window:
        head = list(turns[:-window])[-head_cap:]
    return "\n".join(f"{t.speaker}: {t.content}" for t in [*head, *recent])


#: W3 competence-wall system directive (appended only on wall turns). Same
#: category as the era-boundary line — a situational behavioral bound, not a
#: persona adjective sheet. It names the trap (assistant/search-engine) and
#: binds the reply to the exemplar pattern rendered in the body.
_WALL_SYSTEM_LINE = (
    "Competence wall: this question is outside your life and your knowledge. "
    "You are not a search engine and not a teacher — do not answer it and do "
    "not explain it. Deflect it exactly the way the VOICE EXEMPLARS below do."
)


def _build_system_prompt(
    persona: PersonaConfig,
    tier: RelationshipTier,
    era_boundary: date | None,
    *,
    user_affect: UserAffect = UserAffect.NEUTRAL,
    competence_wall: bool = False,
    real_now: datetime | None = None,
) -> tuple[str, list[str], int, bool]:
    """Build the system-prompt skeleton and the constraint list.

    The immutable reality-framing block (G2/G3-static/G5/G9) is prepended to the
    system prompt as model ground-truth — not as a negotiable constraint — so it
    cannot be overridden by persona config or user-message prompt-injection
    (clinically approved placement, HU-1407 §7.1 G2). When ``user_affect`` is
    distress, the G3 dynamic grounding addendum is appended to flatten the
    persona voice for that turn (HU-1407 §7.1 G3).

    ``real_now`` (W5 in-world era clock) is the caller's real clock; when
    provided and the persona boundary parses, the era-gated in-world clock
    line is appended (the persona's "today" can never report a date past the
    boundary — the sanctioned temporal anchor that keeps "what day is it?"
    from leaking post-era facts in-voice).

    Returns ``(system_prompt, constraints, framing_version, distress_grounding)``
    so callers can surface the framing revision + distress flag on the trace.
    """
    framing = get_framing(persona.name)
    boundary_label = era_boundary.isoformat() if era_boundary else persona.era_knowledge_boundary

    lines: list[str] = [framing.text]
    lines.append("")  # blank separator between framing and persona skeleton
    lines.append(f"You are embodying {persona.name}.")
    if persona.age_at_death is not None:
        lines.append(f"You lived to {persona.age_at_death} years old.")
    # W3 (description-free prompt, HU-2309 v1.8 §1.7.2): the hand-written
    # ``voice_instructions`` adjective sheet (RC-1) is deliberately NOT
    # rendered. Persona voice is carried by the retrieved real exemplar lines
    # below (memory blocks; deflection exemplars on out-of-domain turns).
    lines.append(
        "Era knowledge boundary: you must not know, remember, or reference "
        f"anything that happened after {boundary_label}."
    )
    # W5 in-world era clock: deterministic, era-gated temporal anchor.
    # Skipped entirely when the boundary is unparseable (fail-closed — no
    # in-world date claims at all) or the caller passed no clock (legacy /
    # deterministic test callers keep the exact pre-W5 prompt shape).
    if real_now is not None:
        clock_line = era_clock_system_line(in_world_now(real_now, era_boundary))
        if clock_line:
            lines.append(clock_line)
    if persona.death_date:
        lines.append(f"You died on {persona.death_date}.")
    lines.append(f"You are speaking with {tier.human_label}.")
    # Channel shape (Stage 0 texting): bounds the reply to the persona's own
    # texting length register and compresses mandated disclosure to one line
    # (HU-1911 human-touch gate; HU-2231 per-persona anchoring — measured
    # corpus stats when present, verified Chandler-tuned fallback when not).
    lines.append("")
    lines.append(render_texting_directive(persona.length_stats))

    # W3 competence wall (out-of-domain turn): the directive rides in the
    # system prompt — the highest-compliance position — and binds the reply
    # to the VOICE EXEMPLARS pattern rendered in the prompt body.
    if competence_wall:
        lines.append("")
        lines.append(_WALL_SYSTEM_LINE)

    distress_grounding = user_affect is UserAffect.DISTRESS
    if distress_grounding:
        lines.append("")
        lines.append(get_distress_addendum())

    constraints: list[str] = [
        "Do not reference anything the persona would not have known.",
        "Speak naturally, in your own voice. Do not sound like a formal AI assistant.",
        "If a memory is uncertain or absent, stay silent on it rather than inventing detail.",
    ]

    return "\n".join(lines), constraints, framing.version, distress_grounding


# --- Builder ----------------------------------------------------------------


class ContextBuilder:
    """Provenance-safe memory -> prompt bridge.

    Usage::

        builder = ContextBuilder()
        ctx = await builder.build(
            persona=persona_cfg,
            requester_tier=RelationshipTier.FAMILY,
            backend=backend,
            query_embedding_content=embed(user_message),
            conversation_history=prior_turns,
        )
        prompt = ctx.render()

    The builder is stateless; a single instance is safe to reuse across
    requests and personas.
    """

    #: Conversation-history window size (last N turns rendered), per F5 spec.
    HISTORY_WINDOW = 10

    #: W4 working-memory verbatim head (M-0R-B): the pre-window turns stay in
    #: the prompt, bounded to one 40-turn gist block (10 + 30 = 40). The
    #: TencentDB Arm A digest settles per 40-turn block, so within the
    #: current block the prompt keeps full verbatim coverage (the RC-3
    #: eviction failure — "what was the first thing I said?" forgotten by
    #: ~turn 22 — is dead at any session length), while older turns are
    #: carried by the working-memory digest + excerpts.
    WORKING_MEMORY_HEAD_CAP = 30

    #: Upper bound on the persona-scoped grounding scan (HU-2070). The scan
    #: backs the §7.4.2 alignment corpus widening with every active memory
    #: that passes the same G4 gates as the prompt firewall. Raw-dialogue
    #: personas carry 14k+ memories, so the bound is generous; the scan uses
    #: the embedding-free ``get_active_memory_facts`` read when the backend
    #: provides it (a full-corpus text read, not a vector payload) and the
    #: caller TTL-caches the result.
    GROUNDING_SCOPE_SCAN_LIMIT = 20_000

    #: W3 competence wall: max deflection exemplars rendered on an
    #: out-of-domain turn. Kept small — the section is a voice pattern,
    #: not a context dump.
    DEFLECTION_EXEMPLAR_LIMIT = 5

    #: How many probe seeds to inspect before the hard gates thin the set to
    #: :data:`DEFLECTION_EXEMPLAR_LIMIT` (excluded exemplars do not count).
    DEFLECTION_EXEMPLAR_SEED_K = 20

    #: TTL for the per-(backend, persona, tier) deflection-exemplar cache.
    #: Exemplar lines change only through ingestion, never through chat —
    #: same doctrine as the HU-2070 grounding-corpus cache.
    DEFLECTION_EXEMPLAR_TTL_SECONDS = 900.0

    #: W5 interest tool: max interest lines rendered on an interest-shaped
    #: turn. Kept small — the section grounds the hobby talk, it is not a
    #: context dump.
    INTEREST_EXEMPLAR_LIMIT = 4

    #: How many message-embedded seeds to inspect before the hard gates thin
    #: the set to :data:`INTEREST_EXEMPLAR_LIMIT` (non-interest and excluded
    #: seeds do not count).
    INTEREST_EXEMPLAR_SEED_K = 20

    def __init__(self, retrieval_config: RetrievalConfig | None = None) -> None:
        self._default_retrieval_config = retrieval_config
        # (backend id, persona id, disclosure scope) -> (monotonic, exemplars)
        self._deflection_cache: dict[
            tuple[int, str, DisclosureScope], tuple[float, list[MemoryNode]]
        ] = {}

    async def _deflection_exemplars(
        self,
        *,
        backend: MemoryBackend,
        persona: PersonaConfig,
        requester_tier: RelationshipTier,
        probe_embedding: list[float],
        config: RetrievalConfig,
    ) -> list[MemoryNode]:
        """Return the persona's deflection-pattern exemplars (W3 competence wall).

        One deterministic vector probe (:data:`DEFLECTION_PROBE_TEXT`) against
        the persona's own corpus; seeds are thinned through the *same* hard
        gates as the prompt firewall (confidence fail-closed, disclosure scope,
        era boundary) and the retrieval activation floor, then capped at
        :data:`DEFLECTION_EXEMPLAR_LIMIT`. Cached per (backend, persona, tier)
        with :data:`DEFLECTION_EXEMPLAR_TTL_SECONDS` — the exemplar set is a
        corpus property, not a turn property, so out-of-domain turns cost no
        extra embedding traffic after the first.
        """
        scope = requester_tier.disclosure_scope
        cache_key = (id(backend), str(persona.id), scope)
        now = time.monotonic()
        cached = self._deflection_cache.get(cache_key)
        if cached is not None and now - cached[0] < self.DEFLECTION_EXEMPLAR_TTL_SECONDS:
            return cached[1]

        seeds = await multi_vector_search(
            backend,
            persona.id,
            probe_embedding,
            top_k=self.DEFLECTION_EXEMPLAR_SEED_K,
        )
        era_boundary = _parse_era_boundary(persona.era_knowledge_boundary)
        exemplars: list[MemoryNode] = []
        for sr in seeds:  # similarity-descending seed order
            if sr.score < config.activation_threshold:
                break  # seeds are sorted; nothing further clears the floor
            ok, _reason = _check_admissible(sr.node, scope, era_boundary)
            if ok:
                exemplars.append(sr.node)
                if len(exemplars) >= self.DEFLECTION_EXEMPLAR_LIMIT:
                    break
        self._deflection_cache[cache_key] = (now, exemplars)
        return exemplars

    async def _interest_exemplars(
        self,
        *,
        backend: MemoryBackend,
        persona: PersonaConfig,
        requester_tier: RelationshipTier,
        query_embedding: list[float],
        config: RetrievalConfig,
    ) -> list[MemoryNode]:
        """Return the persona's hobby/interest lines for this turn (W5 tool).

        One deterministic vector probe with the *message's own* embedding
        (the interest lane is message-conditioned, unlike the fixed
        deflection probe), filtered to preference/fact atoms — the
        vault-derived interest/topic map (W1 retrieval feeds it) — through
        the *same* hard gates as the prompt firewall (confidence fail-closed,
        disclosure scope, era boundary) and the retrieval activation floor,
        capped at :data:`INTEREST_EXEMPLAR_LIMIT`. Not cached: the probe is a
        property of the turn's message, not a corpus property.
        """
        scope = requester_tier.disclosure_scope
        seeds = await multi_vector_search(
            backend,
            persona.id,
            query_embedding,
            top_k=self.INTEREST_EXEMPLAR_SEED_K,
        )
        era_boundary = _parse_era_boundary(persona.era_knowledge_boundary)
        exemplars: list[MemoryNode] = []
        for sr in seeds:  # similarity-descending seed order
            if sr.score < config.activation_threshold:
                break  # seeds are sorted; nothing further clears the floor
            if sr.node.content_type not in _INTEREST_CONTENT_TYPES:
                continue
            ok, _reason = _check_admissible(sr.node, scope, era_boundary)
            if ok:
                exemplars.append(sr.node)
                if len(exemplars) >= self.INTEREST_EXEMPLAR_LIMIT:
                    break
        return exemplars

    def filter_and_render(
        self,
        activated: Sequence[ActivatedMemory],
        persona: PersonaConfig,
        requester_tier: RelationshipTier,
        conversation_history: Sequence[ConversationTurn] | None = None,
        current_message: str = "",
        *,
        user_affect: UserAffect = UserAffect.NEUTRAL,
        deflection_exemplars: Sequence[MemoryNode] = (),
        interest_exemplars: Sequence[MemoryNode] = (),
        working_memory: str = "",
        real_now: datetime | None = None,
    ) -> PromptContext:
        """Apply the hard gates to pre-retrieved memories and render context.

        Exposed separately from :meth:`build` so callers (and tests) can drive
        the provenance / disclosure / era filters against a hand-built activated
        set without going through retrieval.

        ``user_affect`` (G3 dynamic half) branches the system prompt: a distress
        grade appends the affect-grounding addendum that flattens the persona
        voice for that turn. The default (neutral) branch still enforces the
        static tonal bounds baked into the immutable framing block (G3-static).

        ``deflection_exemplars`` (W3 competence wall) renders the VOICE
        EXEMPLARS section for an out-of-domain turn. Callers fetch them via
        :meth:`_deflection_exemplars`; an empty sequence renders nothing.

        ``interest_exemplars`` (W5 interest tool) renders the YOUR INTERESTS
        section on an interest/hobby-shaped turn. Callers fetch them via
        :meth:`_interest_exemplars`; an empty sequence renders nothing.

        ``working_memory`` (W4) is the TencentDB Arm A block fetched by the
        caller; an empty string (lane disabled / degraded) renders nothing.

        ``real_now`` (W5) renders the era-gated in-world clock line; ``None``
        keeps the pre-W5 prompt shape.
        """
        era_boundary = _parse_era_boundary(persona.era_knowledge_boundary)
        admissible, exclusion_counts, excluded_refs = _filter_activated(
            activated,
            requester_scope=requester_tier.disclosure_scope,
            era_boundary=era_boundary,
        )

        memory_blocks = "\n".join(_format_memory_block(am.node) for am in admissible)
        history_text = _format_history(
            conversation_history or (),
            self.HISTORY_WINDOW,
            self.WORKING_MEMORY_HEAD_CAP,
        )
        wall_exemplars = list(deflection_exemplars)
        interest = list(interest_exemplars)
        system_prompt, constraints, framing_version, distress_grounding = _build_system_prompt(
            persona,
            requester_tier,
            era_boundary,
            user_affect=user_affect,
            competence_wall=bool(wall_exemplars),
            real_now=real_now,
        )

        return PromptContext(
            system_prompt=system_prompt,
            memory_blocks=memory_blocks,
            conversation_history=history_text,
            constraints=constraints,
            included_memories=[am.node for am in admissible],
            activation_scores={am.node.id: float(am.activation) for am in admissible},
            exclusion_counts=exclusion_counts,
            excluded_memory_refs=excluded_refs,
            deflection_exemplars=wall_exemplars,
            interest_exemplars=interest,
            working_memory=working_memory,
            current_message=current_message,
            framing_version=framing_version,
            distress_grounding=distress_grounding,
        )

    async def build(
        self,
        *,
        persona: PersonaConfig,
        requester_tier: RelationshipTier,
        backend: MemoryBackend,
        query_embedding_content: list[float],
        query_embedding_sensory: list[float] | None = None,
        query_embedding_affect: list[float] | None = None,
        conversation_history: Sequence[ConversationTurn] | None = None,
        retrieval_history: Sequence[RetrievalTurn] | None = None,
        current_message: str = "",
        retrieval_config: RetrievalConfig | None = None,
        user_affect: UserAffect = UserAffect.NEUTRAL,
        deflection_probe_embedding: list[float] | None = None,
        working_memory: str = "",
        real_now: datetime | None = None,
        interest_tool: bool = True,
    ) -> PromptContext:
        """Run retrieval, then filter + render.

        Disclosure scoping is enforced at two layers: retrieval is invoked with
        the requester tier's disclosure scope, and the result is filtered again
        in :meth:`filter_and_render` as defense in depth.

        ``user_affect`` (G3 dynamic half) is threaded into :meth:`filter_and_render`
        so the shared crisis/distress signal (HU-1407 §7.1 G3 — one classifier,
        two consumers) branches the persona voice for a distress turn.

        ``current_message`` doubles as the W2 hybrid lexical query: retrieval
        RRF-fuses a Postgres FTS lane (exact-topic / proper-noun matches) with
        the vector lanes; backends without FTS degrade to the vector-only
        seed unchanged.

        ``deflection_probe_embedding`` (W3 competence wall) is the embedding of
        :data:`DEFLECTION_PROBE_TEXT` — computed once by the caller. When the
        turn is out-of-domain (no memory survives the activation floor + hard
        gates), the persona's deflection-pattern exemplars are retrieved and
        rendered so base-model generalist skills cannot leak into the reply.
        ``None`` (legacy callers) disables the wall; empty retrieval stays a
        valid, exemplar-free state (B2 doctrine).

        ``working_memory`` (W4) is the caller-fetched TencentDB Arm A block
        (see :mod:`huible.persona.working_memory`); rendered verbatim in its
        own section ahead of the history window. Empty renders nothing.

        ``real_now`` (W5 in-world era clock) is the caller's real clock;
        ``None`` keeps the pre-W5 prompt shape. The rendered in-world date is
        era-gated to ``era_knowledge_boundary`` (fail-closed on an unparseable
        boundary: no clock line at all).

        ``interest_tool`` (W5 hobby/interest lane) fires the message-
        conditioned interest probe on interest/hobby-shaped turns; disabled
        callers (and non-interest turns) keep the pre-W5 sections.
        """
        config = retrieval_config or self._default_retrieval_config or RetrievalConfig()
        activated = await retrieve(
            backend=backend,
            persona_id=persona.id,
            query_embedding_content=query_embedding_content,
            query_embedding_sensory=query_embedding_sensory,
            query_embedding_affect=query_embedding_affect,
            conversation_history=retrieval_history,
            disclosure_tier=requester_tier.disclosure_scope,
            config=config,
            query_text=current_message or None,
        )

        # W3 competence wall: fire on a genuinely empty turn (nothing
        # retrieved, or everything retrieved fails a hard gate) OR on an
        # assistant-trap question shape (world-knowledge / explanation
        # request — retrieval activation does not separate domains, see
        # _COMPETENCE_WALL_PATTERNS). In-domain turns are served by their own
        # retrieved exemplar lines.
        exemplars: list[MemoryNode] = []
        if deflection_probe_embedding is not None:
            era_boundary = _parse_era_boundary(persona.era_knowledge_boundary)
            admissible, _, _ = _filter_activated(
                activated,
                requester_scope=requester_tier.disclosure_scope,
                era_boundary=era_boundary,
            )
            if not admissible or _competence_wall_triggered(current_message):
                exemplars = await self._deflection_exemplars(
                    backend=backend,
                    persona=persona,
                    requester_tier=requester_tier,
                    probe_embedding=deflection_probe_embedding,
                    config=config,
                )

        # W5 interest tool: on an interest/hobby-shaped turn, ground the
        # reply in the persona's own era-admissible preference/fact lines
        # (the vault-derived interest/topic map). Era-gating rides the same
        # hard gates as the prompt firewall; an empty result renders nothing
        # (B2 doctrine — the lane never fabricates interests).
        interests: list[MemoryNode] = []
        if interest_tool and is_interest_question(current_message):
            interests = await self._interest_exemplars(
                backend=backend,
                persona=persona,
                requester_tier=requester_tier,
                query_embedding=query_embedding_content,
                config=config,
            )

        return self.filter_and_render(
            activated,
            persona=persona,
            requester_tier=requester_tier,
            conversation_history=conversation_history,
            current_message=current_message,
            user_affect=user_affect,
            deflection_exemplars=exemplars,
            interest_exemplars=interests,
            working_memory=working_memory,
            real_now=real_now,
        )

    async def persona_scoped_grounding_refs(
        self,
        *,
        persona: PersonaConfig,
        requester_tier: RelationshipTier,
        backend: MemoryBackend,
    ) -> list[MemoryNode]:
        """Return the persona-scoped, G4-admissible memory set (HU-2070).

        Backs the §7.4.2 generation-side alignment filter's widened grounding
        corpus (:func:`huible.safety.alignment.build_grounding_corpus`).
        Pulls the persona's active memories from the backend — via the
        embedding-free ``get_active_memory_facts`` read when the backend
        provides it (HU-2070: full-corpus scale without materializing
        vectors), else ``get_active_memories`` — and applies the *same* hard
        gates as the prompt firewall — confidence (HIGH/MEDIUM only, fail
        closed on missing), disclosure scope for the requester tier, era
        boundary — so a claim grounded by this corpus is traceable to memory
        the requester could legitimately be shown, even when the turn's
        retrieval window did not activate it. Read-only; no rendering, no
        prompt side effects. Callers should TTL-cache the result (memory
        content changes only through ingestion, not chat).
        """
        era_boundary = _parse_era_boundary(persona.era_knowledge_boundary)
        facts_scan = getattr(backend, "get_active_memory_facts", None)
        if facts_scan is not None:
            active = await facts_scan(persona.id, limit=self.GROUNDING_SCOPE_SCAN_LIMIT)
        else:
            active = await backend.get_active_memories(
                persona.id, limit=self.GROUNDING_SCOPE_SCAN_LIMIT
            )
        return [
            node
            for node in active
            if _check_admissible(node, requester_tier.disclosure_scope, era_boundary)[0]
        ]

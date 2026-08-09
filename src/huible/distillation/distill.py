"""L0 -> L1 -> L2 -> L3 distillation engine.

Deterministic, dependency-free by default (works without an LLM) while
supporting an optional ``llm`` hook that can replace the heuristic fact
extraction with a model call, mirroring the existing
``huible.ingestion.extractor.Extractor`` pattern.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

from huible.distillation.records import (
    DistillationResult,
    EvidenceLink,
    L0Record,
    L1Fact,
    L2Scenario,
    L3Profile,
    MemoryType,
)

logger = logging.getLogger(__name__)

LLMHook = Callable[..., Coroutine[Any, Any, dict]]

_STATE_MARKERS = (
    r"\bcurrently\b",
    r"\bnow\b",
    r"\blives? in\b",
    r"\bliving in\b",
    r"\bworks at\b",
    r"\bis\b",
    r"\bare\b",
    r"\bhas\b",
    r"\bhave\b",
    r"\bmoved to\b",
    r"\bresides\b",
)
_STATE_RE = re.compile("|".join(_STATE_MARKERS), re.IGNORECASE)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

# -- Semantic preference / state extraction (L3 hardening, BHAA-1364) --------
# Older heuristics matched keywords *anywhere* in a sentence and stored the raw
# trailing fragment as the rule, so dialog lines like "I've never seen one of his
# plays" became ``general:prefers: "seen one of his plays"`` — a verbatim quote
# fragment, not a semantic preference. The matchers below are anchored and
# gated so only genuine persona-voiced preference/state statements are promoted
# to durable_rule / current_state, and their objects are normalized.

# Optional ``Speaker:`` prefix on the L0 content (e.g. ``chandler: I ...``).
_SPEAKER_PREFIX = r"(?:[A-Z][\w'-]{1,30}:\s+)?"

# A genuine durable preference is spoken in the persona's own voice: it leads
# with a first-person / persona subject. This rejects narrative dialog lines
# that merely contain "never"/"like" buried mid-sentence.
_PREF_SUBJECT_RE = re.compile(
    rf"^\s*{_SPEAKER_PREFIX}(?:i(?:\s|\W)|my\b|we\b|pat\b)",
    re.IGNORECASE,
)

# The preference verb must be the sentence's main verb (near the start), not a
# keyword buried inside a later clause. ``head`` tokens before the verb are
# capped so a verb deep in the sentence is not treated as a preference marker.
_PREF_VERB_RE = re.compile(
    r"\b(love|loves|liked|like|likes|hate|hates|preferred|prefer|prefers|"
    r"enjoy|enjoys|always|never|usually)\b\s*(.+?)(?:[.!?;]|$)",
    re.IGNORECASE,
)
# ``my favorite X is Y`` is a durable preference with a different shape.
_FAVORITE_RE = re.compile(
    r"\bmy\s+favou?rite\s+(\w+(?:\s+\w+){0,3}?)\s+is\s+(.+?)(?:[.!?;]|$)",
    re.IGNORECASE,
)

# A current state is also spoken in the persona's own voice ("I live in ...",
# "I work at ...", "I am a ..."). Bare ``is``/``has`` keywords anywhere in a
# narrative line are NOT a self-state.
_STATE_SUBJECT_RE = re.compile(
    rf"^\s*{_SPEAKER_PREFIX}(?:i(?:\s|\W)|my\b|we\b|pat\b)",
    re.IGNORECASE,
)
_STATE_VERB_RE = re.compile(
    r"\b(currently|now|live|lives|living|work|works|worked|moved|resides?|am|"
    r"'m|m|are|'re|re|have|has|had)\b\s*(.+?)(?:[.!?;]|$)",
    re.IGNORECASE,
)

# Reject preferences/states attributed to *another* character
# ("he loves ...", "she never ...") — those are not persona self-facts.
_OTHER_SUBJECT_RE = re.compile(
    r"\b(?:he|she|they|it)\s+(?:love|loves|like|likes|hate|hates|prefer|prefers|"
    r"enjoy|enjoys|always|never|usually|is|are|has|have|lives|works)",
    re.IGNORECASE,
)

# Canonical semantic predicate for each matched preference verb.
_PREF_PREDICATES = {
    "love": "likes",
    "loves": "likes",
    "like": "likes",
    "likes": "likes",
    "liked": "likes",
    "enjoy": "likes",
    "enjoys": "likes",
    "prefer": "likes",
    "prefers": "likes",
    "preferred": "likes",
    "hate": "avoids",
    "hates": "avoids",
    "always": "habit",
    "never": "avoids",
    "usually": "tends",
}

# Object tokens that never carry semantic weight on their own (pronouns,
# fillers). An object whose content tokens are all in this set is rejected.
_STOP_OBJECT_TOKENS = {
    "you", "her", "him", "his", "its", "it", "itself", "that", "this", "these",
    "those", "them", "they", "us", "we", "our", "my", "your", "their", "what",
    "which", "who", "whom", "there", "here", "all", "some", "any", "one",
    "ones", "too", "very", "just", "really", "actually", "kinda", "sorta",
    "gonna", "wanna", "gotta", "yeah", "yep", "nope", "okay", "ok", "oh",
    "ah", "um", "uh", "huh", "wow", "hey", "please", "than", "then", "when",
    "where", "while", "because", "though", "although", "unless", "since",
    "before", "after", "again", "still", "ever", "never", "always", "even",
    "also", "and", "but", "or", "nor", "so", "if", "as", "at", "by", "for",
    "in", "of", "on", "to", "with", "from", "into", "onto", "over", "out",
    "up", "down", "off", "the", "a", "an", "be", "been", "being", "am", "is",
    "are", "was", "were", "do", "does", "did", "have", "has", "had", "will",
    "would", "should", "could", "can", "may", "might", "must", "not", "no",
    "yes", "i", "me", "mine", "myself",
}

# Clause separators that turn an object into a multi-clause situational
# statement ("love to stay, but i gotta go") rather than a durable preference.
_CLAUSE_BREAK = re.compile(r"\b(?:but|however|although|though|because|since|if|when|"
                           r"while|unless|so|whereas)\b", re.IGNORECASE)

_QUOTE_CHARS = "\"'“”‘’`"  # noqa: RUF001 - smart quotes appear in real corpora


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _clean_object(raw: str) -> str:
    """Normalize a captured object into a clean semantic phrase."""
    obj = raw.strip().strip(_QUOTE_CHARS).strip()
    obj = obj.strip(_QUOTE_CHARS)
    # Drop trailing clause breaks and everything after them.
    obj = _CLAUSE_BREAK.split(obj, 1)[0].strip()
    obj = obj.strip(_QUOTE_CHARS).strip(" .,;:!?-—–")  # noqa: RUF001 - en/em dash in corpora
    # Collapse internal whitespace.
    obj = re.sub(r"\s+", " ", obj)
    return obj


def _is_semantic_object(obj: str) -> bool:
    """Quality gate: is this a genuine semantic target, not a verbatim fragment?

    Rejects questions, bare quoted snippets, unresolved-pronoun objects, and
    objects with no content token. A rejected object downgrades the sentence to
    a plain observation (gap-safe: nothing is invented, evidence is preserved).
    """
    if not obj or len(obj) < 3:
        return False
    if "?" in obj:
        return False
    # Bare quoted fragment, e.g. ``'would've'`` → reject.
    stripped = obj.strip(_QUOTE_CHARS)
    if stripped != obj and len(stripped.split()) <= 3:
        return False
    lowered = obj.lower()
    tokens = re.findall(r"[A-Za-z']+", lowered)
    content = [t for t in tokens if len(t) >= 3 and t not in _STOP_OBJECT_TOKENS]
    if not content:
        return False
    # A single content token must carry real semantic weight: at least 4 chars
    # and not a contraction (would've / y'know / don't). Genuine multi-word
    # objects pass on the first branch.
    if len(content) == 1:
        only = content[0]
        if len(only) < 4 or "'" in only:
            return False
        if only in {"him", "her", "them", "you", "it"}:
            return False
    return True


# Perfect-tense auxiliaries. When an adverb marker (always/never/usually) is
# preceded by one of these, the sentence is a past experience/observation
# ("I've never seen one of his plays"), not a durable habitual rule. Handles
# both spaced ("I have") and contracted ("I've", "they'd") forms.
_PERFECT_AUX_BEFORE_ADVERB = re.compile(
    r"\b(?:i|we|they|you|he|she|it)(?:'(?:ve|d|ll)|\s+(?:have|has|had))\s*$",
    re.IGNORECASE,
)


def _extract_preference(sentence: str) -> tuple[str, str] | None:
    """Return ``(predicate, semantic_object)`` for a genuine durable preference.

    Returns ``None`` when the sentence is not a clean, persona-voiced preference
    statement (so the caller treats it as a plain observation). Deterministic
    and conservative: never invents; only normalizes evidenced text.
    """
    if _OTHER_SUBJECT_RE.search(sentence):
        return None
    if not _PREF_SUBJECT_RE.match(sentence):
        return None
    # ``my favorite X is Y`` form.
    fav = _FAVORITE_RE.search(sentence)
    if fav and len(sentence[: fav.start()].split()) <= 4:
        obj = _clean_object(fav.group(2))
        if _is_semantic_object(obj):
            return "favorite", f"{fav.group(1).strip()} — {obj}"
    match = _PREF_VERB_RE.search(sentence)
    if not match:
        return None
    # The verb must be the main verb (within the first ~6 words of the sentence
    # excluding the optional speaker prefix).
    head = sentence[: match.start()]
    if len(head.split()) > 6:
        return None
    verb = match.group(1).lower()
    # Adverb markers (always/never/usually) only signal a durable rule in the
    # habitual present. A perfect auxiliary before them → past experience.
    if verb in {"always", "never", "usually"} and _PERFECT_AUX_BEFORE_ADVERB.search(head):
        return None
    predicate = _PREF_PREDICATES.get(verb, "prefers")
    obj = _clean_object(match.group(2))
    if not _is_semantic_object(obj):
        return None
    return predicate, obj


def _extract_state(sentence: str) -> tuple[str, str] | None:
    """Return ``(predicate, semantic_object)`` for a genuine current state.

    Returns ``None`` when the sentence is not a clean persona-voiced state.
    """
    if _OTHER_SUBJECT_RE.search(sentence):
        return None
    if not _STATE_SUBJECT_RE.match(sentence):
        return None
    match = _STATE_VERB_RE.search(sentence)
    if not match:
        return None
    head = sentence[: match.start()].split()
    if len(head) > 6:
        return None
    verb = match.group(1).lower().lstrip("'")
    predicate = {
        "am", "m", "re", "are", "is",
    }
    obj = _clean_object(match.group(2))
    if not _is_semantic_object(obj):
        return None
    return ("is" if verb in predicate or verb in {"currently", "now"} else verb), obj


def _classify_memory_type(text: str) -> MemoryType:
    if _extract_preference(text) is not None:
        return MemoryType.DURABLE_RULE
    if _extract_state(text) is not None:
        return MemoryType.CURRENT_STATE
    return MemoryType.OBSERVATION


class Distiller:
    """Runs the L0 -> L1 -> L2 -> L3 pyramid for an upload batch."""

    def __init__(
        self,
        llm: LLMHook | None = None,
        min_fact_length: int = 8,
    ) -> None:
        self._llm = llm
        self._min_fact_length = min_fact_length

    async def distill(self, raw: list[L0Record]) -> DistillationResult:
        facts = await self._extract_facts(raw)
        scenarios = self._group_scenarios(facts)
        profiles = self._distill_profiles(scenarios)
        return DistillationResult(
            raw=raw,
            facts=facts,
            scenarios=scenarios,
            profiles=profiles,
        )

    # -- L0 -> L1 ---------------------------------------------------------
    async def _extract_facts(self, raw: list[L0Record]) -> list[L1Fact]:
        if self._llm is not None:
            try:
                return await self._llm_extract_facts(raw)
            except (KeyError, TypeError, ValueError, RuntimeError) as exc:
                logger.warning("LLM fact extraction failed, falling back to heuristics: %s", exc)
        return self._heuristic_extract_facts(raw)

    async def _llm_extract_facts(self, raw: list[L0Record]) -> list[L1Fact]:
        payload = {
            "records": [
                {"id": r.id, "kind": r.kind, "content": r.content, "occurred_at": r.occurred_at}
                for r in raw
            ]
        }
        result = await self._llm("distill_l1", payload, None)
        facts: list[L1Fact] = []
        for item in result.get("facts", []):
            source_id = str(item.get("source_id", ""))
            facts.append(
                L1Fact(
                    subject=str(item.get("subject", "")),
                    predicate=str(item.get("predicate", "")),
                    object=str(item.get("object", "")),
                    memory_type=MemoryType(item.get("memory_type", "observation")),
                    valid_from=item.get("valid_from"),
                    valid_to=item.get("valid_to"),
                    evidence=[EvidenceLink(source_id=source_id, source_kind="conversation")],
                    confidence=float(item.get("confidence", 0.5)),
                    content=str(item.get("content", "")),
                )
            )
        return facts

    def _heuristic_extract_facts(self, raw: list[L0Record]) -> list[L1Fact]:
        facts: list[L1Fact] = []
        for record in raw:
            sentences = [s.strip() for s in _SENTENCE_SPLIT.split(record.content) if s.strip()]
            for sentence in sentences:
                if len(sentence) < self._min_fact_length:
                    continue
                evidence = EvidenceLink(
                    source_id=record.id,
                    source_kind=record.kind,
                    span=sentence[:200],
                )
                memory_type = _classify_memory_type(sentence)
                subject, predicate, obj = self._split_triple(sentence, memory_type)
                facts.append(
                    L1Fact(
                        subject=subject,
                        predicate=predicate,
                        object=obj,
                        memory_type=memory_type,
                        valid_from=record.occurred_at,
                        evidence=[evidence],
                        confidence=0.6,
                        content=sentence,
                    )
                )
        return facts

    def _split_triple(
        self,
        sentence: str,
        memory_type: MemoryType,
    ) -> tuple[str, str, str]:
        subject = "Pat"
        predicate = "mentioned"
        obj = sentence
        if memory_type is MemoryType.DURABLE_RULE:
            pref = _extract_preference(sentence)
            if pref is not None:
                predicate, obj = pref
        elif memory_type is MemoryType.CURRENT_STATE:
            state = _extract_state(sentence)
            if state is not None:
                predicate, obj = state
        return subject, predicate, obj

    # -- L1 -> L2 ---------------------------------------------------------
    def _group_scenarios(self, facts: list[L1Fact]) -> list[L2Scenario]:
        buckets: dict[str, list[L1Fact]] = defaultdict(list)
        for fact in facts:
            domain = self._infer_domain(fact.content)
            buckets[domain].append(fact)

        scenarios: list[L2Scenario] = []
        for domain, group in buckets.items():
            summary = self._summarize(group)
            evidence = [
                link
                for fact in group
                for link in fact.evidence
            ]
            scenarios.append(
                L2Scenario(
                    scenario=f"{domain} memories",
                    domain=domain,
                    summary=summary,
                    facts=group,
                    evidence=evidence,
                )
            )
        return scenarios

    def _infer_domain(self, text: str) -> str:
        lowered = text.lower()
        domain_keywords = {
            "tea": "food & drink",
            "coffee": "food & drink",
            "wine": "food & drink",
            "cooking": "food & drink",
            "family": "family",
            "grandfather": "family",
            "grandmother": "family",
            "mother": "family",
            "father": "family",
            "son": "family",
            "daughter": "family",
            "work": "career",
            "job": "career",
            "company": "career",
            "office": "career",
            "seattle": "places",
            "gujarat": "places",
            "india": "places",
            "travel": "places",
            "garden": "hobbies",
            "music": "hobbies",
            "photography": "hobbies",
            "hermes": "projects",
            "huible": "projects",
            "memory": "projects",
        }
        for keyword, domain in domain_keywords.items():
            if keyword in lowered:
                return domain
        return "general"

    def _summarize(self, facts: list[L1Fact]) -> str:
        if not facts:
            return ""
        objects = [f.object for f in facts if f.object and f.object != f.content]
        if objects:
            return "; ".join(objects[:3])
        return facts[0].content

    # -- L2 -> L3 ---------------------------------------------------------
    def _distill_profiles(self, scenarios: list[L2Scenario]) -> list[L3Profile]:
        profiles: list[L3Profile] = []
        for scenario in scenarios:
            for fact in scenario.facts:
                if fact.memory_type is MemoryType.DURABLE_RULE:
                    profiles.append(
                        L3Profile(
                            key=f"{scenario.domain}:{fact.predicate}",
                            rule=fact.object if fact.object else fact.content,
                            memory_type=MemoryType.DURABLE_RULE,
                            valid_from=fact.valid_from,
                            evidence=list(fact.evidence),
                            confidence=fact.confidence,
                        )
                    )
                elif fact.memory_type is MemoryType.CURRENT_STATE:
                    profiles.append(
                        L3Profile(
                            key=f"{scenario.domain}:{fact.predicate}",
                            rule=fact.object if fact.object else fact.content,
                            memory_type=MemoryType.CURRENT_STATE,
                            valid_from=fact.valid_from,
                            evidence=list(fact.evidence),
                            confidence=fact.confidence,
                        )
                    )
        return profiles

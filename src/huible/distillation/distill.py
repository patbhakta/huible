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

_DURABLE_MARKERS = (
    r"\balways\b",
    r"\bnever\b",
    r"\bevery\b",
    r"\busually\b",
    r"\bprefers?\b",
    r"\blikes\b",
    r"\bloves\b",
    r"\bhates\b",
    r"\bfavorite\b",
    r"\bfavourite\b",
    r"\bhabit\b",
    r"\btradition\b",
    r"\britual\b",
)
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
_DURABLE_RE = re.compile("|".join(_DURABLE_MARKERS), re.IGNORECASE)
_STATE_RE = re.compile("|".join(_STATE_MARKERS), re.IGNORECASE)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_PREF_PATTERN = re.compile(
    r"\b(?:prefers?|likes?|loves?|enjoys?|always|never|usually)\s+(.+?)(?:\.|$)",
    re.IGNORECASE,
)
_STATE_PATTERN = re.compile(
    r"\b(?:currently|now|is|are|has|have|lives? in|works at|moved to)\s+(.+?)(?:\.|$)",
    re.IGNORECASE,
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _classify_memory_type(text: str) -> MemoryType:
    if _DURABLE_RE.search(text):
        return MemoryType.DURABLE_RULE
    if _STATE_RE.search(text):
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
            match = _PREF_PATTERN.search(sentence)
            if match:
                predicate = "prefers"
                obj = match.group(1).strip()
        elif memory_type is MemoryType.CURRENT_STATE:
            match = _STATE_PATTERN.search(sentence)
            if match:
                predicate = "is"
                obj = match.group(1).strip()
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

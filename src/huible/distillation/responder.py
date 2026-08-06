"""Markdown-first persona responder.

Responds using the consolidated Markdown produced by the L0-L3 pipeline,
NOT raw vector search.  Retrieval is a lightweight lexical + temporal filter
over the Markdown store: durable rules, current states and valid observations
are scored against the user query and rendered into a persona reply that
carries evidence back-links to the raw L0 source.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from huible.distillation.records import MemoryType
from huible.distillation.store import MarkdownMemoryStore

logger = logging.getLogger(__name__)

_WORD = re.compile(r"[a-z0-9']+")


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


@dataclass(slots=True)
class MarkdownHit:
    tier: str
    memory_type: str
    key: str
    content: str
    source: str
    score: float


@dataclass(slots=True)
class MarkdownResponse:
    reply: str
    hits: list[MarkdownHit] = field(default_factory=list)


class MarkdownPersonaResponder:
    """Answers persona queries from the consolidated Markdown store."""

    def __init__(
        self,
        store: MarkdownMemoryStore,
        persona_name: str = "Pat",
        voice_instructions: str = "Warm, reflective, slightly witty, tea lover.",
    ) -> None:
        self.store = store
        self.persona_name = persona_name
        self.voice_instructions = voice_instructions

    def respond(
        self,
        query: str,
        now: datetime | None = None,
        include_evidence: bool = True,
    ) -> MarkdownResponse:
        query_tokens = _tokens(query)
        records = self.store.query(now=now)

        hits: list[MarkdownHit] = []
        for fields in records:
            content = fields.get("_body", "")
            score = self._score(query_tokens, content, fields)
            if score <= 0:
                continue
            hits.append(
                MarkdownHit(
                    tier=str(fields.get("tier", "")),
                    memory_type=str(fields.get("memory_type", "")),
                    key=str(fields.get("key", "")),
                    content=content,
                    source=self._resolve_source(fields),
                    score=score,
                )
            )

        hits.sort(key=lambda h: h.score, reverse=True)
        top = hits[:5]

        reply = self._compose_reply(query, top)
        if include_evidence:
            reply += self._render_evidence(top)

        return MarkdownResponse(reply=reply, hits=hits)

    def _resolve_source(self, fields: dict[str, Any]) -> str:
        source = str(fields.get("source", "") or "")
        if source:
            return source
        evidence_sources = str(fields.get("evidence_sources", "") or "")
        if evidence_sources:
            return evidence_sources.split(",")[0]
        return ""

    def _score(
        self,
        query_tokens: set[str],
        content: str,
        fields: dict[str, Any],
    ) -> float:
        tokens = _tokens(content)
        overlap = len(query_tokens.intersection(tokens))
        if overlap == 0:
            return 0.0
        base = overlap / max(1, len(query_tokens))
        if fields.get("memory_type") == MemoryType.DURABLE_RULE.value:
            base *= 1.2
        return round(base, 3)

    def _compose_reply(self, query: str, top: list[MarkdownHit]) -> str:
        if not top:
            return (
                "I don't have a specific markdown memory for that, but I'm "
                "happy to talk it through with you."
            )
        # Durable rules / current states take precedence as the persona's
        # grounding truth; observations fill in colour.
        durable = [h for h in top if h.memory_type == MemoryType.DURABLE_RULE.value]
        state = [h for h in top if h.memory_type == MemoryType.CURRENT_STATE.value]
        observations = [h for h in top if h.memory_type == MemoryType.OBSERVATION.value]

        lines: list[str] = []
        if durable:
            lines.append("That's a steady part of who I am: " + durable[0].content)
        if state:
            lines.append("Right now that stands as: " + state[0].content)
        if observations:
            lines.append("And I remember " + observations[0].content)
        if not lines:
            lines.append("I remember something about that: " + top[0].content)

        system = (
            f"Voice: {self.voice_instructions} (you are {self.persona_name}). "
            f"Ground every claim in the provided markdown memories."
        )
        return f"{system}\n\n" + " ".join(lines)

    def _render_evidence(self, top: list[MarkdownHit]) -> str:
        seen: set[str] = set()
        evidence = []
        for hit in top:
            if not hit.source or hit.source in seen:
                continue
            seen.add(hit.source)
            evidence.append(f"[evidence -> {hit.source}]")
        if not evidence:
            return ""
        return "\n\n" + " ".join(evidence)

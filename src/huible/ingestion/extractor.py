from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)

Tier2Model = Callable[..., Coroutine[Any, Any, dict]]


@dataclass(slots=True)
class ConversationTurn:
    speaker: str
    content: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MemoryCandidate:
    content: str
    content_type: str
    tier: str
    source_type: str
    source_ref: dict[str, Any]
    disclosure_scope: str
    memory_date: str | None = None
    participants: list[str] = field(default_factory=list)
    sensory_cues: list[str] = field(default_factory=list)
    affect_signals: list[str] = field(default_factory=list)
    confidence: float = 0.0
    extraction_metadata: dict[str, Any] = field(default_factory=dict)


_EXTRACTION_PROMPT = """You are a memory extraction engine for a deceased person's persona.
Given a conversation turn, extract potential memory candidates that capture:
- Factual information about the person (what they did, said, liked, disliked)
- Sensory details (what they smelled, heard, saw, tasted, touched)
- Emotional/affective signals (how they felt, what mood they conveyed)
- Relationship information (who they interacted with, how)

Return a JSON object with a "candidates" array. Each candidate has:
- content: the extracted memory text
- content_type: one of "narrative", "fact", "sensory", "relationship", "preference"
- tier: "accrued"
- source_type: "extraction"
- disclosure_scope: one of "private", "family", "close_friends", "all_contacts"
- memory_date: ISO date string if mentioned, null otherwise
- participants: list of people mentioned
- sensory_cues: list of sensory details extracted
- affect_signals: list of emotional cues
- confidence: 0.0-1.0 score

Only extract memories that are specific, factual, and would genuinely deepen the persona.
Reject greetings, filler, generic pleasantries, and anything too vague to be useful.
If nothing is worth extracting, return empty candidates.

Conversation turn:
Speaker: {speaker}
Content: {content}
Timestamp: {timestamp}"""


class ExtractionError(Exception):
    pass


class Extractor:
    def __init__(
        self,
        tier2_model: Tier2Model | None = None,
        confidence_threshold: float = 0.3,
        max_candidates_per_turn: int = 5,
    ) -> None:
        self._tier2_model = tier2_model
        self._confidence_threshold = confidence_threshold
        self._max_candidates = max_candidates_per_turn

    async def extract(
        self,
        turn: ConversationTurn,
        persona_id: UUID,
    ) -> list[MemoryCandidate]:
        if self._tier2_model is None:
            return self._heuristic_extract(turn)

        try:
            prompt = _EXTRACTION_PROMPT.format(
                speaker=turn.speaker,
                content=turn.content,
                timestamp=turn.timestamp.isoformat(),
            )
            payload = {"prompt": prompt, "turn": turn.content}
            raw = await self._tier2_model("extraction", payload, None)
            candidates = self._parse_response(raw)
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            logger.warning("Tier 2 extraction failed, falling back to heuristics: %s", exc)
            candidates = self._heuristic_extract(turn)

        filtered = [
            c for c in candidates
            if c.confidence >= self._confidence_threshold
        ]
        return filtered[:self._max_candidates]

    async def extract_batch(
        self,
        turns: list[ConversationTurn],
        persona_id: UUID,
    ) -> list[MemoryCandidate]:
        all_candidates: list[MemoryCandidate] = []
        for turn in turns:
            candidates = await self.extract(turn, persona_id)
            all_candidates.extend(candidates)
        return all_candidates

    def _parse_response(self, raw: dict) -> list[MemoryCandidate]:
        candidates_raw = raw.get("candidates", [])
        if not isinstance(candidates_raw, list):
            return []
        candidates: list[MemoryCandidate] = []
        for item in candidates_raw:
            if not isinstance(item, dict):
                continue
            try:
                candidate = MemoryCandidate(
                    content=str(item.get("content", "")),
                    content_type=str(item.get("content_type", "narrative")),
                    tier=str(item.get("tier", "accrued")),
                    source_type=str(item.get("source_type", "extraction")),
                    source_ref={
                        "speaker": item.get("speaker", ""),
                        "extracted_at": datetime.utcnow().isoformat(),
                    },
                    disclosure_scope=str(item.get("disclosure_scope", "family")),
                    memory_date=item.get("memory_date"),
                    participants=list(item.get("participants", [])),
                    sensory_cues=list(item.get("sensory_cues", [])),
                    affect_signals=list(item.get("affect_signals", [])),
                    confidence=float(item.get("confidence", 0.5)),
                    extraction_metadata={
                        "method": "tier2",
                        "raw": item,
                    },
                )
                candidates.append(candidate)
            except (TypeError, ValueError):
                continue
        return candidates

    def _heuristic_extract(self, turn: ConversationTurn) -> list[MemoryCandidate]:
        content = turn.content.strip()
        if len(content) < 10:
            return []
        if len(content.split()) < 4:
            return []
        return [
            MemoryCandidate(
                content=content,
                content_type="narrative",
                tier="accrued",
                source_type="extraction",
                source_ref={
                    "speaker": turn.speaker,
                    "extracted_at": turn.timestamp.isoformat(),
                },
                disclosure_scope="family",
                confidence=0.4,
                extraction_metadata={"method": "heuristic"},
            ),
        ]

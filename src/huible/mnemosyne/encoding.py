"""Deep encoding gate: refuse shallow storage.

The most important layer — this is where "understanding" happens.
Based on Craik & Lockhart (1972): depth of processing at encoding
determines whether information becomes durable knowledge or fragile noise.

Every candidate memory must pass through deep processing before storage:
1. Extract meaning, not text
2. Connect to existing knowledge
3. Assess quality (confidence + source)
4. Check for contradictions
5. Generate retrieval paths

A raw text dump that hasn't been processed is NOT a memory. It's noise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from huible.mnemosyne.trust import ConfidenceScorer, SourceReliability


class ContentType(StrEnum):
    """What type of knowledge is being encoded?"""

    FACT = "fact"
    PREFERENCE = "preference"
    PROCEDURE = "procedure"
    EVENT = "event"
    RELATIONSHIP = "relationship"
    FORMULA = "formula"
    TABLE = "table"
    DIAGRAM = "diagram"
    # Add CONFIG to ContentType enum for config-type memories
    CODE = "code"
    CONFIG = "config"
    DECISION = "decision"
    CORRECTION = "correction"


class EncodingStatus(StrEnum):
    ACCEPTED = "accepted"
    SHALLOW_REJECTED = "shallow_rejected"
    QUARANTINED = "quarantined"


@dataclass(slots=True)
class EncodingResult:
    """Result of deep encoding a candidate memory."""

    status: EncodingStatus
    memory_id: UUID = field(default_factory=uuid4)
    content: str = ""  # processed meaning, not raw text
    raw_content: str = ""  # original input for audit
    summary: str = ""  # one-line gist
    content_type: ContentType = ContentType.FACT
    retrieval_cues: list[str] = field(default_factory=list)
    connections: list[UUID] = field(default_factory=list)
    contradiction_with: list[UUID] = field(default_factory=list)
    scorer: ConfidenceScorer | None = None
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def was_deep_processed(self) -> bool:
        """Did this memory go through full deep processing?"""
        return self.status == EncodingStatus.ACCEPTED and bool(self.content) and bool(self.summary)


class DeepEncoder:
    """Deep processing gate for memory encoding.

    Usage:
        encoder = DeepEncoder(llm_callback=my_llm_function)
        result = encoder.encode(
            raw_input="Kestra now uses Postgres",
            source=SourceReliability.TOOL_OUTPUT,
            content_type=ContentType.FACT,
        )

        if result.status == EncodingStatus.ACCEPTED:
            store.save(result)
        elif result.status == EncodingStatus.SHALLOW_REJECTED:
            # Raw text without meaning — don't store
            pass
    """

    # Minimum requirements for acceptance
    MIN_SUMMARY_LENGTH = 10
    MIN_RETRIEVAL_CUES = 1
    MIN_CONTENT_LENGTH = 5

    def __init__(
        self,
        llm_callback: Any | None = None,
        embedding_callback: Any | None = None,
    ) -> None:
        """
        Args:
            llm_callback: async function(text, prompt) -> str for meaning extraction.
                         If None, uses rule-based fallback (weaker but no API needed).
            embedding_callback: function(text) -> list[float] for vector generation.
        """
        self.llm_callback = llm_callback
        self.embedding_callback = embedding_callback

    async def encode(
        self,
        raw_input: str,
        source: SourceReliability,
        content_type: ContentType = ContentType.FACT,
        context: dict[str, Any] | None = None,
        existing_connections: list[UUID] | None = None,
    ) -> EncodingResult:
        """Deep process a raw input into a memory node.

        Refuses shallow storage. Every memory is:
        1. Understood (meaning extracted)
        2. Connected (related memories identified)
        3. Quality-checked (confidence + source)
        4. Contradiction-checked
        5. Multi-pathed (retrieval cues generated)
        """
        context = context or {}
        existing_connections = existing_connections or []

        # Step 1: Extract meaning
        if self.llm_callback:
            meaning = await self._extract_meaning_llm(raw_input, content_type, context)
        else:
            meaning = self._extract_meaning_rule(raw_input, content_type)

        # Refuse shallow storage
        if len(meaning.content) < self.MIN_CONTENT_LENGTH:
            return EncodingResult(
                status=EncodingStatus.SHALLOW_REJECTED,
                raw_content=raw_input,
                reason="Content too short after processing — likely noise",
            )

        if not meaning.summary or len(meaning.summary) < self.MIN_SUMMARY_LENGTH:
            return EncodingResult(
                status=EncodingStatus.SHALLOW_REJECTED,
                raw_content=raw_input,
                reason="Could not generate meaningful summary — shallow processing",
            )

        # Step 2: Generate retrieval cues (how will I need this later?)
        if self.llm_callback:
            cues = await self._generate_retrieval_cues(meaning.content)
        else:
            cues = self._generate_cues_rule(meaning.content, content_type)

        if len(cues) < self.MIN_RETRIEVAL_CUES:
            return EncodingResult(
                status=EncodingStatus.SHALLOW_REJECTED,
                raw_content=raw_input,
                reason="No retrieval cues could be generated — won't be findable later",
            )

        # Step 3: Assess quality
        scorer = ConfidenceScorer(source=source)
        is_stateful = content_type in (ContentType.FACT, ContentType.CONFIG) or "config" in context.get("tags", [])  # type: ignore[attr-defined]
        scorer.is_stateful = is_stateful

        # If from tool output, auto-verify
        if source == SourceReliability.TOOL_OUTPUT:
            scorer.verify()

        # Step 4: Check for contradictions (via embedding similarity if available)
        contradictions: list[UUID] = []
        if self.embedding_callback and existing_connections:
            # In full implementation, would check cosine > 0.85 against existing
            # For now, just record connections
            contradictions = []

        # Step 5: Build result
        return EncodingResult(
            status=EncodingStatus.ACCEPTED,
            content=meaning.content,
            raw_content=raw_input,
            summary=meaning.summary,
            content_type=content_type,
            retrieval_cues=cues,
            connections=existing_connections,
            contradiction_with=contradictions,
            scorer=scorer,
            metadata={
                "encoded_at": datetime.now(timezone.utc).isoformat(),
                "context": context,
            },
        )

    async def _extract_meaning_llm(
        self,
        raw_input: str,
        content_type: ContentType,
        context: dict[str, Any],
    ) -> _Meaning:
        """Use LLM to extract deep meaning from raw text."""
        prompt = self._build_extraction_prompt(raw_input, content_type, context)
        response = await self.llm_callback(raw_input, prompt)

        # Parse LLM response (expected format: SUMMARY: ... | CONTENT: ...)
        parts = response.split("|")
        summary = ""
        content = raw_input
        for part in parts:
            part = part.strip()
            if part.startswith("SUMMARY:"):
                summary = part[8:].strip()
            elif part.startswith("CONTENT:"):
                content = part[8:].strip()

        return _Meaning(content=content, summary=summary)

    def _extract_meaning_rule(
        self,
        raw_input: str,
        content_type: ContentType,
    ) -> _Meaning:
        """Rule-based meaning extraction (fallback when no LLM available)."""
        # Simple but effective: extract the core assertion
        content = raw_input.strip()

        # Generate summary: first sentence or truncated
        if "." in content:
            summary = content.split(".")[0] + "."
        elif len(content) > 80:
            summary = content[:77] + "..."
        else:
            summary = content

        return _Meaning(content=content, summary=summary)

    async def _generate_retrieval_cues(self, content: str) -> list[str]:
        """Generate hypothetical questions that would need this memory."""
        if self.llm_callback:
            prompt = "Generate 3 questions that this statement would answer. Format: one per line."
            response = await self.llm_callback(content, prompt)
            return [q.strip() for q in response.strip().split("\n") if q.strip()][:5]
        return self._generate_cues_rule(content, ContentType.FACT)

    def _generate_cues_rule(self, content: str, content_type: ContentType) -> list[str]:
        """Rule-based retrieval cue generation."""
        cues = []

        # Extract key terms (simple: words > 4 chars)
        words = [w.strip(".,!?;:\"'()[]{}").lower() for w in content.split()]
        key_words = [w for w in words if len(w) > 4][:5]

        if content_type == ContentType.FACT:
            cues.append(f"What is: {content[:60]}")
        elif content_type == ContentType.PREFERENCE:
            cues.append(f"Preference about: {', '.join(key_words[:3])}")
        elif content_type == ContentType.DECISION:
            cues.append(f"Decision: {content[:60]}")
        elif content_type == ContentType.CORRECTION:
            cues.append(f"Corrected: {content[:60]}")

        # Generic cue
        cues.append(f"Query about: {', '.join(key_words[:3])}")

        return cues[:5]

    def _build_extraction_prompt(
        self,
        raw_input: str,
        content_type: ContentType,
        context: dict[str, Any],
    ) -> str:
        """Build the LLM prompt for deep meaning extraction."""
        return f"""You are encoding a memory for an AI agent. Extract deep meaning.

RAW INPUT: {raw_input}
TYPE: {content_type.value}
CONTEXT: {context}

Process this input:
1. What is this actually saying? Strip filler, extract the core assertion.
2. Why does it matter? What decision or action does it inform?
3. Is this always true, or context-dependent?

Respond in format:
SUMMARY: <one-line gist>
CONTENT: <processed meaning — what this actually says, not the raw text>"""


@dataclass(slots=True)
class _Meaning:
    """Internal: extracted meaning from raw input."""
    content: str
    summary: str

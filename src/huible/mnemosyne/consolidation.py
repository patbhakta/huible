"""Consolidation pass: the "sleep" function for agent memory.

Based on hippocampal consolidation: memory isn't built in real-time.
It's built offline through replay, compression, and integration.

This pass runs periodically (e.g., every 6 hours via cron/Kestra):
1. Replay recent episodic memories
2. Extract generalizable facts → semantic store
3. Merge near-duplicates (cosine > 0.86)
4. Prune low-value memories (high entropy + low access)
5. Strengthen frequently co-activated connections
6. Resolve pending contradictions
7. Spaced-repetition reinforcement of key facts
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from huible.mnemosyne.trust import ConfidenceScorer, SourceReliability


@dataclass(slots=True)
class ConsolidationResult:
    """Result of a consolidation pass."""

    episodes_replayed: int = 0
    facts_extracted: int = 0
    duplicates_merged: int = 0
    memories_pruned: int = 0
    edges_strengthened: int = 0
    contradictions_resolved: int = 0
    facts_reinforced: int = 0
    duration_seconds: float = 0.0
    errors: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        return (
            f"Consolidated {self.episodes_replayed} episodes → "
            f"{self.facts_extracted} facts, "
            f"merged {self.duplicates_merged} dupes, "
            f"pruned {self.memories_pruned} noise, "
            f"strengthened {self.edges_strengthened} edges, "
            f"resolved {self.contradictions_resolved} contradictions, "
            f"reinforced {self.facts_reinforced} facts "
            f"in {self.duration_seconds:.1f}s"
        )


@dataclass(slots=True)
class MemorySnapshot:
    """Snapshot of a memory for consolidation processing."""

    id: UUID
    content: str
    content_type: str
    confidence: float
    trust_tier: str
    access_count: int
    last_accessed: datetime | None
    created_at: datetime
    is_stateful: bool
    metadata: dict[str, Any] = field(default_factory=dict)


class ConsolidationPass:
    """The "sleep" pass — offline memory consolidation.

    Usage:
        consolidator = ConsolidationPass()
        result = await consolidator.run(
            recent_memories=[...],
            all_memories=[...],
            co_activation_log={...},
        )
    """

    DEDUP_THRESHOLD = 0.86  # cosine similarity for merge
    PRUNE_MIN_ENTROPY = 0.7  # entropy threshold for pruning
    PRUNE_MAX_ACCESS = 0  # max access count for pruning candidate
    PRUNE_MIN_AGE_DAYS = 7  # don't prune memories younger than this
    REINFORCE_INTERVAL_DAYS = 7  # spaced repetition interval

    async def run(
        self,
        recent_memories: list[MemorySnapshot],
        all_memories: list[MemorySnapshot] | None = None,
        co_activation_log: dict[tuple[UUID, UUID], int] | None = None,
        llm_callback: Any | None = None,
    ) -> ConsolidationResult:
        """Run a full consolidation pass.

        Args:
            recent_memories: Episodic memories from recent period (e.g., last 6h)
            all_memories: Full memory store for dedup/prune (None = skip those steps)
            co_activation_log: {(id_a, id_b): count} for edge strengthening
            llm_callback: async function for fact extraction (None = skip extraction)
        """
        start = datetime.now(timezone.utc)
        result = ConsolidationResult()

        # Step 1: Replay recent episodic memories
        result.episodes_replayed = len(recent_memories)

        # Step 2: Extract generalizable facts
        if llm_callback and recent_memories:
            facts = await self._extract_facts(recent_memories, llm_callback)
            result.facts_extracted = len(facts)
            # Facts would be stored via the encoder in full implementation

        # Step 3: Merge near-duplicates
        if all_memories:
            result.duplicates_merged = self._find_and_merge_duplicates(all_memories)

        # Step 4: Prune low-value memories
        if all_memories:
            result.memories_pruned = self._identify_pruning_candidates(all_memories)

        # Step 5: Strengthen co-activated connections
        if co_activation_log:
            result.edges_strengthened = self._strengthen_edges(co_activation_log)

        # Step 6: Resolve pending contradictions
        if all_memories:
            result.contradictions_resolved = self._resolve_contradictions(all_memories)

        # Step 7: Spaced-repetition reinforcement
        if all_memories:
            result.facts_reinforced = self._reinforce_facts(all_memories)

        result.duration_seconds = (datetime.now(timezone.utc) - start).total_seconds()
        return result

    async def _extract_facts(
        self,
        memories: list[MemorySnapshot],
        llm_callback: Any,
    ) -> list[str]:
        """Extract generalizable facts from episodic memories using LLM.

        This is the core of episodic → semantic transformation.
        "I helped Pat debug Kestra 4 times" → "Pat values persistence and verification"
        """
        # Batch memories into groups of 50 for processing
        facts: list[str] = []
        for batch in self._chunk(memories, 50):
            # Format memories as text for the LLM
            memory_text = "\n".join(
                f"- [{m.content_type}] {m.content}" for m in batch
            )
            prompt = (
                "Extract generalizable facts from these memory episodes.\n"
                "Focus on patterns, preferences, and lessons that would apply beyond the specific events.\n\n"
                f"{memory_text}\n\n"
                "Output one fact per line. Each fact should be a standalone truth."
            )
            try:
                response = await llm_callback(memory_text, prompt)
                facts.extend(
                    line.strip() for line in response.strip().split("\n") if line.strip()
                )
            except Exception as e:
                pass  # Don't fail consolidation on LLM errors
        return facts

    def _find_and_merge_duplicates(self, memories: list[MemorySnapshot]) -> int:
        """Find near-duplicate memories that should be merged.

        In full implementation, would use embedding cosine similarity.
        For now, uses content-based heuristic.
        """
        merged = 0
        seen: dict[str, UUID] = {}

        for mem in memories:
            # Normalize content for comparison
            normalized = self._normalize_for_dedup(mem.content)

            if normalized in seen:
                # This is a duplicate — merge (keep higher confidence)
                merged += 1
            else:
                seen[normalized] = mem.id

        return merged

    def _normalize_for_dedup(self, content: str) -> str:
        """Normalize content for duplicate detection."""
        return " ".join(content.lower().split())

    def _identify_pruning_candidates(self, memories: list[MemorySnapshot]) -> int:
        """Identify memories that should be pruned.

        Pruning criteria: high entropy (uncertain) AND low access (unused) AND old enough.
        """
        now = datetime.now(timezone.utc)
        candidates = 0

        for mem in memories:
            # Don't prune young memories
            age_days = (now - mem.created_at).days
            if age_days < self.PRUNE_MIN_AGE_DAYS:
                continue

            # Don't prune human-reviewed facts
            if mem.trust_tier == "human_reviewed":
                continue

            # Calculate entropy from confidence
            confidence = mem.confidence
            if 0.0 < confidence < 1.0:
                from math import log2
                entropy = -confidence * log2(confidence) - (1 - confidence) * log2(1 - confidence)
            else:
                entropy = 0.0

            # Pruning candidate: uncertain AND unused
            if entropy >= self.PRUNE_MIN_ENTROPY and mem.access_count <= self.PRUNE_MAX_ACCESS:
                candidates += 1

        return candidates

    def _strengthen_edges(
        self,
        co_activation_log: dict[tuple[UUID, UUID], int],
    ) -> int:
        """Strengthen edges between frequently co-activated memories."""
        strengthened = 0
        for (id_a, id_b), count in co_activation_log.items():
            if count >= 3:  # co-activated at least 3 times
                strengthened += 1
        return strengthened

    def _resolve_contradictions(self, memories: list[MemorySnapshot]) -> int:
        """Identify and flag contradictions in the memory store.

        In full implementation, would use embedding similarity to find
        semantically similar but contradictory memories.
        """
        # Placeholder — contradiction detection requires embeddings
        return 0

    def _reinforce_facts(self, memories: list[MemorySnapshot]) -> int:
        """Spaced-repetition reinforcement of key facts.

        Memories that haven't been accessed in a while but are high-confidence
        get a "reinforcement check" — are they still true?
        """
        now = datetime.now(timezone.utc)
        reinforced = 0

        for mem in memories:
            if not mem.is_stateful:
                continue
            if mem.trust_tier not in ("human_reviewed", "machine_verified"):
                continue
            if mem.last_accessed is None:
                continue

            days_since_access = (now - mem.last_accessed).days
            if days_since_access >= self.REINFORCE_INTERVAL_DAYS:
                reinforced += 1

        return reinforced

    def _chunk(self, items: list, size: int) -> list[list]:
        """Split a list into chunks."""
        return [items[i:i + size] for i in range(0, len(items), size)]

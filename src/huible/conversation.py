from __future__ import annotations

import csv
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from huible.ingestion.extractor import ConversationTurn as ExtractorTurn, Extractor
from huible.memory.protocol import (
    ContentType,
    DisclosureScope,
    EdgeType,
    MemoryEdge,
    MemoryNode,
    MemoryTier,
    SourceType,
)
from huible.memory.retrieval import (
    ActivatedMemory,
    ConversationTurn as RetrievalTurn,
    RetrievalConfig,
    retrieve,
)

logger = logging.getLogger(__name__)


def simple_embedding(text: str, dim: int = 64) -> list[float]:
    """Generates a normalized bag-of-words hash feature vector for text.
    Ensures text sharing keywords produces high dot-product similarity.
    """
    words = [w.strip(".,!?;:\"'()[]") for w in text.lower().split() if len(w) > 2]
    vec = [0.0] * dim
    for word in words:
        h = int(hashlib.md5(word.encode("utf-8")).hexdigest(), 16)
        idx = h % dim
        vec[idx] += 1.0
    norm = sum(x * x for x in vec) ** 0.5
    if norm > 1e-6:
        vec = [x / norm for x in vec]
    return vec


@dataclass
class PersonaConfig:
    id: UUID = field(default_factory=uuid4)
    name: str = "Deceased Persona"
    display_name: str = "Pat's Persona"
    voice_instructions: str = "Warm, reflective, slightly witty, tea lover."
    age_at_death: int = 72
    death_date: str = "2024-12-01"
    era_knowledge_boundary: str = "2024-12-01"


class InMemoryMemoryBackend:
    """In-memory reference backend implementing MemoryBackend for conversation loops and demos."""

    def __init__(self) -> None:
        self.memories: dict[UUID, MemoryNode] = {}
        self.edges: list[MemoryEdge] = []
        self.content_index: list[tuple[list[float], MemoryNode]] = []
        self.sensory_index: list[tuple[list[float], MemoryNode]] = []
        self.affect_index: list[tuple[list[float], MemoryNode]] = []

    async def store_memory(self, node: MemoryNode) -> UUID:
        self.memories[node.id] = node
        if node.embedding_content:
            self.content_index.append((node.embedding_content, node))
        if node.embedding_sensory:
            self.sensory_index.append((node.embedding_sensory, node))
        if node.embedding_affect:
            self.affect_index.append((node.embedding_affect, node))
        return node.id

    async def get_memory(self, memory_id: UUID) -> MemoryNode | None:
        return self.memories.get(memory_id)

    async def add_edge(self, edge: MemoryEdge) -> UUID:
        self.edges.append(edge)
        return edge.id

    async def get_edges(self, memory_id: UUID) -> list[MemoryEdge]:
        return [e for e in self.edges if e.source_id == memory_id or e.target_id == memory_id]

    async def search_by_content(
        self,
        persona_id: UUID,
        query_embedding: list[float],
        top_k: int = 20,
        disclosure_scope: DisclosureScope | None = None,
    ) -> list[Any]:
        from huible.memory.protocol import SearchResult

        scored = []
        for emb, node in self.content_index:
            if node.persona_id != persona_id or not node.is_active:
                continue
            dot = sum(q * e for q, e in zip(query_embedding, emb))
            if dot > 0.02:
                scored.append(SearchResult(node=node, score=dot))
        scored.sort(key=lambda x: x.score, reverse=True)
        return scored[:top_k]

    async def search_by_sensory(
        self,
        persona_id: UUID,
        query_embedding: list[float],
        top_k: int = 20,
        disclosure_scope: DisclosureScope | None = None,
    ) -> list[Any]:
        from huible.memory.protocol import SearchResult

        scored = []
        for emb, node in self.sensory_index:
            if node.persona_id != persona_id or not node.is_active:
                continue
            dot = sum(q * e for q, e in zip(query_embedding, emb))
            scored.append(SearchResult(node=node, score=max(0.0, dot)))
        scored.sort(key=lambda x: x.score, reverse=True)
        return scored[:top_k]

    async def search_by_affect(
        self,
        persona_id: UUID,
        query_embedding: list[float],
        top_k: int = 20,
        disclosure_scope: DisclosureScope | None = None,
    ) -> list[Any]:
        from huible.memory.protocol import SearchResult

        scored = []
        for emb, node in self.affect_index:
            if node.persona_id != persona_id or not node.is_active:
                continue
            dot = sum(q * e for q, e in zip(query_embedding, emb))
            scored.append(SearchResult(node=node, score=max(0.0, dot)))
        scored.sort(key=lambda x: x.score, reverse=True)
        return scored[:top_k]

    async def get_active_memories(self, persona_id: UUID, limit: int = 50) -> list[MemoryNode]:
        active = [
            m for m in self.memories.values() if m.persona_id == persona_id and m.is_active
        ]
        return active[:limit]


class HuibleCSVLoader:
    """Loads memory CSV files (such as Pat's sample_memories.csv) into Huible memory nodes."""

    @staticmethod
    async def load_csv(
        file_path: str | Path,
        persona_id: UUID,
        backend: InMemoryMemoryBackend,
    ) -> list[MemoryNode]:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"CSV file not found: {file_path}")

        loaded_nodes = []
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                content = row.get("content", "").strip()
                if not content:
                    continue

                tier_str = row.get("tier", "canonical").lower()
                try:
                    tier = MemoryTier(tier_str)
                except ValueError:
                    tier = MemoryTier.CANONICAL

                content_type_str = row.get("content_type", "fact").lower()
                try:
                    content_type = ContentType(content_type_str)
                except ValueError:
                    content_type = ContentType.FACT

                disclosure_str = row.get("disclosure_scope", "private").lower()
                try:
                    disclosure_scope = DisclosureScope(disclosure_str)
                except ValueError:
                    disclosure_scope = DisclosureScope.PRIVATE

                emb = simple_embedding(content)
                sensory_str = row.get("sensory_cues", "")
                sensory_emb = simple_embedding(sensory_str) if sensory_str else None
                affect_str = row.get("affect_signals", "")
                affect_emb = simple_embedding(affect_str) if affect_str else None

                node = MemoryNode(
                    id=uuid4(),
                    persona_id=persona_id,
                    tier=tier,
                    content=content,
                    content_type=content_type,
                    embedding_content=emb,
                    embedding_sensory=sensory_emb,
                    embedding_affect=affect_emb,
                    source_type=SourceType.FAMILY_UPLOAD,
                    disclosure_scope=disclosure_scope,
                    metadata={
                        "memory_date": row.get("memory_date"),
                        "affect_signals": affect_str,
                        "sensory_cues": sensory_str,
                    },
                )

                await backend.store_memory(node)
                loaded_nodes.append(node)

        # Build thematic edges between loaded nodes based on shared terms
        for i, node_a in enumerate(loaded_nodes):
            for node_b in loaded_nodes[i + 1 :]:
                words_a = set(node_a.content.lower().split())
                words_b = set(node_b.content.lower().split())
                common = words_a.intersection(words_b)
                if len(common) >= 2:
                    edge = MemoryEdge(
                        id=uuid4(),
                        source_id=node_a.id,
                        target_id=node_b.id,
                        edge_type=EdgeType.THEMATIC,
                        weight=0.8,
                    )
                    await backend.add_edge(edge)

        logger.info(f"Loaded {len(loaded_nodes)} memories into store for persona {persona_id}")
        return loaded_nodes


class HuibleConversationLoop:
    """End-to-end conversation loop for Huible memory engine:
    Inbound message -> Spreading Activation Retrieval -> Context Prompt -> Response Generator -> Memory Candidate Extraction.
    """

    def __init__(
        self,
        persona: PersonaConfig,
        backend: InMemoryMemoryBackend,
        disclosure_tier: DisclosureScope = DisclosureScope.PRIVATE,
        llm_client: Any | None = None,
    ) -> None:
        self.persona = persona
        self.backend = backend
        self.disclosure_tier = disclosure_tier
        self.llm_client = llm_client
        self.retrieval_history: list[RetrievalTurn] = []
        self.turn_history: list[dict[str, str]] = []
        self.extractor = Extractor(tier2_model=None)

    async def turn(self, user_message: str, speaker_name: str = "Pat") -> dict[str, Any]:
        """Executes a complete conversation turn with memory retrieval and synthesis."""
        query_emb = simple_embedding(user_message)

        # 1. Spreading Activation Retrieval over memory graph
        config = RetrievalConfig(
            activation_threshold=0.05,
            decay_factor=0.6,
            suppression_window=10,
        )
        activated_memories: list[ActivatedMemory] = await retrieve(
            backend=self.backend,
            persona_id=self.persona.id,
            query_embedding_content=query_emb,
            query_embedding_sensory=None,
            query_embedding_affect=None,
            conversation_history=self.retrieval_history,
            config=config,
            disclosure_tier=self.disclosure_tier,
        )

        # 2. Track activated memory IDs for feedback suppression
        activated_ids = [m.node.id for m in activated_memories]
        self.retrieval_history.append(RetrievalTurn(activated_memory_ids=activated_ids))

        # 3. Assemble Prompt Context
        memory_blocks = []
        for mem in activated_memories:
            memory_blocks.append(f"[{mem.node.content_type.value.upper()}] {mem.node.content}")
        memories_text = "\n".join(memory_blocks) if memory_blocks else "No specific memory activated."

        history_text = "\n".join(
            f"{t['speaker']}: {t['content']}" for t in self.turn_history[-10:]
        )

        system_prompt = (
            f"You are embodying {self.persona.name}. "
            f"Voice & Style: {self.persona.voice_instructions}. "
            f"Era Boundary: {self.persona.era_knowledge_boundary}."
        )

        full_prompt = (
            f"SYSTEM: {system_prompt}\n\n"
            f"ACTIVATED MEMORIES:\n{memories_text}\n\n"
            f"CONVERSATION HISTORY:\n{history_text}\n\n"
            f"{speaker_name}: {user_message}\n"
            f"{self.persona.name}:"
        )

        # 4. Generate Response
        if self.llm_client:
            response_text = await self.llm_client(full_prompt)
        else:
            response_text = self._synthesize_fallback_response(user_message, activated_memories)

        # 5. Record Turn History
        self.turn_history.append({"speaker": speaker_name, "content": user_message})
        self.turn_history.append({"speaker": self.persona.name, "content": response_text})

        # 6. Extract potential new memories from turn
        ext_turn = ExtractorTurn(speaker=speaker_name, content=user_message)
        candidates = await self.extractor.extract(ext_turn, self.persona.id)

        return {
            "prompt": full_prompt,
            "response": response_text,
            "activated_memories": [
                {"content": m.node.content, "type": m.node.content_type.value, "activation": round(m.activation, 3)}
                for m in activated_memories
            ],
            "extracted_candidates": len(candidates),
            "turn_number": len(self.turn_history) // 2,
        }

    def _synthesize_fallback_response(
        self, user_message: str, activated: list[ActivatedMemory]
    ) -> str:
        """Synthesizes a natural persona response based on activated memories."""
        if not activated:
            return f"I appreciate you bringing that up, {self.persona.name} here. Tell me a bit more about what's on your mind."

        top_memory = activated[0].node
        content = top_memory.content

        if "tea" in content.lower():
            return f"Ah yes, Earl Grey with oat milk on a quiet Sunday morning in Seattle... nothing compares to that warmth."
        elif "gujarat" in content.lower() or "grandfather" in content.lower():
            return f"My grandfather was a tea merchant back in Gujarat before moving to the US. I always remember the wooden tea chest and spices."
        elif "hermes" in content.lower():
            return f"Yes, I know about Hermes. Long refactoring sessions can get frustrating when context is lost, but we're fixing that."
        elif "huible" in content.lower():
            return f"Huible memory engine uses spreading activation across temporal-affective graphs — true memory continuity."
        else:
            return f"That reminds me of this: {content}. It's something very close to my heart."

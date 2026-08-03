from __future__ import annotations

import math
from typing import Any
from uuid import UUID, uuid4

import pytest

from huible.memory.protocol import (
    DisclosureScope,
    MemoryEdge,
    MemoryNode,
    SearchResult,
)
from tests.f1.corpus import SyntheticCorpus


class CosineFakeBackend:
    """In-memory backend with actual cosine similarity search."""

    def __init__(self) -> None:
        self._memories: dict[UUID, MemoryNode] = {}
        self._edges: list[MemoryEdge] = []
        self._content_vectors: list[tuple[list[float], UUID]] = []
        self._sensory_vectors: list[tuple[list[float], UUID]] = []
        self._affect_vectors: list[tuple[list[float], UUID]] = []

    def bulk_load(
        self,
        memories: list[MemoryNode],
        edges: list[MemoryEdge],
    ) -> None:
        for m in memories:
            self._memories[m.id] = m
            if m.embedding_content:
                self._content_vectors.append((m.embedding_content, m.id))
            if m.embedding_sensory:
                self._sensory_vectors.append((m.embedding_sensory, m.id))
            if m.embedding_affect:
                self._affect_vectors.append((m.embedding_affect, m.id))
        self._edges = list(edges)

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b, strict=False))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(x * x for x in b))
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)

    async def _search(
        self,
        vectors: list[tuple[list[float], UUID]],
        query: list[float],
        top_k: int,
    ) -> list[SearchResult]:
        if not vectors:
            return []
        scores: list[tuple[float, UUID]] = []
        for vec, node_id in vectors:
            sim = self._cosine(vec, query)
            if sim > 0.0:
                scores.append((sim, node_id))
        scores.sort(key=lambda x: x[0], reverse=True)
        results: list[SearchResult] = []
        for score, node_id in scores[:top_k]:
            node = self._memories.get(node_id)
            if node:
                results.append(SearchResult(node=node, score=score))
        return results

    async def store_memory(self, node: MemoryNode) -> Any:
        self._memories[node.id] = node
        if node.embedding_content:
            self._content_vectors.append((node.embedding_content, node.id))
        if node.embedding_sensory:
            self._sensory_vectors.append((node.embedding_sensory, node.id))
        if node.embedding_affect:
            self._affect_vectors.append((node.embedding_affect, node.id))
        return node.id

    async def get_memory(self, memory_id: Any) -> MemoryNode | None:
        return self._memories.get(memory_id)

    async def search_by_content(
        self,
        persona_id: Any,
        query_embedding: list[float],
        top_k: int = 20,
        disclosure_scope: DisclosureScope | None = None,
    ) -> list[SearchResult]:
        return await self._search(self._content_vectors, query_embedding, top_k)

    async def search_by_sensory(
        self,
        persona_id: Any,
        query_embedding: list[float],
        top_k: int = 20,
        disclosure_scope: DisclosureScope | None = None,
    ) -> list[SearchResult]:
        return await self._search(self._sensory_vectors, query_embedding, top_k)

    async def search_by_affect(
        self,
        persona_id: Any,
        query_embedding: list[float],
        top_k: int = 20,
        disclosure_scope: DisclosureScope | None = None,
    ) -> list[SearchResult]:
        return await self._search(self._affect_vectors, query_embedding, top_k)

    async def get_edges(self, memory_id: Any) -> list[MemoryEdge]:
        return [e for e in self._edges if e.source_id == memory_id]

    async def add_edge(self, edge: MemoryEdge) -> Any:
        self._edges.append(edge)
        return edge.id

    async def supersede_memory(self, old_id: Any, new_node: MemoryNode) -> Any:
        old = self._memories.get(old_id)
        if old:
            self._memories[old_id] = MemoryNode(
                id=old.id,
                persona_id=old.persona_id,
                tier=old.tier,
                content=old.content,
                content_type=old.content_type,
                is_active=False,
            )
        self._memories[new_node.id] = new_node
        return new_node.id

    async def get_active_memories(self, persona_id: Any, limit: int = 50) -> list[MemoryNode]:
        active = [m for m in self._memories.values() if m.is_active]
        return active[:limit]

    async def quarantine_candidate(self, entry: Any) -> Any:
        return uuid4()

    def count_memories(self) -> int:
        return len(self._memories)

    def count_edges(self) -> int:
        return len(self._edges)

    def count_by_tier(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for m in self._memories.values():
            counts[m.tier.value] = counts.get(m.tier.value, 0) + 1
        return counts

    def count_by_scope(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for m in self._memories.values():
            counts[m.disclosure_scope.value] = counts.get(m.disclosure_scope.value, 0) + 1
        return counts

    def count_by_type(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for m in self._memories.values():
            counts[m.content_type.value] = counts.get(m.content_type.value, 0) + 1
        return counts

    def has_multi_vector_embeddings(self) -> bool:
        for m in self._memories.values():
            if m.embedding_content and m.embedding_sensory and m.embedding_affect:
                return True
        return False


CORPUS_SIZE = 1050
CORPUS_EDGES = 3000


@pytest.fixture(scope="session")
def corpus() -> SyntheticCorpus:
    c = SyntheticCorpus(n_memories=CORPUS_SIZE, n_edges=CORPUS_EDGES, seed=42)
    c.generate()
    return c


@pytest.fixture(scope="session")
def backend(corpus: SyntheticCorpus) -> CosineFakeBackend:
    b = CosineFakeBackend()
    b.bulk_load(corpus.memories, corpus.edges)
    return b


@pytest.fixture
def query_embedding_for_topic() -> list[float]:
    import hashlib
    import math
    topic = "fishing"
    h = hashlib.sha512(f"0:{topic}".encode()).digest()
    result: list[float] = []
    for i in range(1536):
        chunk = h[i % len(h) : i % len(h) + 4]
        val = int.from_bytes(chunk, "big") / 0xFFFFFFFF
        result.append(val * 2.0 - 1.0)
    norm = math.sqrt(sum(x * x for x in result))
    return [x / norm for x in result]

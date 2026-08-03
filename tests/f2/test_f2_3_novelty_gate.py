from __future__ import annotations

from uuid import uuid4

from huible.memory.protocol import EdgeType, MemoryEdge, MemoryNode, MemoryTier, SearchResult
from tests.f2.conftest import PERSONA_ID, _make_context


class TestF2_3_NoveltyGate:
    """F2.3: Novelty gate requires candidate connects to >= 1 existing node via edge."""

    async def test_no_backend_passes(self, novelty_gate):
        result = await novelty_gate.evaluate({"content": "test"}, _make_context())
        assert result.outcome.value == "pass"

    async def test_empty_graph_ambiguous(self, novelty_gate):
        from unittest.mock import AsyncMock
        backend = AsyncMock()
        backend.search_by_content.return_value = []
        backend.get_active_memories.return_value = []

        result = await novelty_gate.evaluate(
            {"content": "First memory", "embedding_content": [0.1] * 10},
            _make_context(backend=backend),
        )
        assert result.outcome.value == "ambiguous"

    async def test_connected_memory_passes(self, novelty_gate):
        from unittest.mock import AsyncMock

        emb = [0.1] * 10
        mock_node = MemoryNode(
            id=uuid4(), persona_id=PERSONA_ID, tier=MemoryTier.ACCRUED,
            content="Related memory", embedding_content=emb,
        )
        backend = AsyncMock()
        backend.search_by_content.return_value = [SearchResult(node=mock_node, score=0.8)]
        backend.get_edges.return_value = [
            MemoryEdge(
                id=uuid4(), source_id=mock_node.id,
                target_id=uuid4(), edge_type=EdgeType.THEMATIC,
            )
        ]

        result = await novelty_gate.evaluate(
            {"content": "Connected memory", "embedding_content": emb},
            _make_context(backend=backend),
        )
        assert result.outcome.value == "pass"

    async def test_orphan_high_similarity_with_edges_passes(self, novelty_gate):
        from unittest.mock import AsyncMock

        emb = [0.1] * 10
        mock_node = MemoryNode(
            id=uuid4(), persona_id=PERSONA_ID, tier=MemoryTier.ACCRUED,
            content="Close match", embedding_content=emb,
        )
        backend = AsyncMock()
        backend.search_by_content.return_value = [SearchResult(node=mock_node, score=0.95)]
        backend.get_edges.return_value = [
            MemoryEdge(
                id=uuid4(), source_id=mock_node.id,
                target_id=uuid4(), edge_type=EdgeType.THEMATIC,
            )
        ]

        result = await novelty_gate.evaluate(
            {"content": "Very similar", "embedding_content": emb},
            _make_context(backend=backend),
        )
        assert result.outcome.value == "pass"

    async def test_orphan_high_similarity_no_edges_ambiguous(self, novelty_gate):
        from unittest.mock import AsyncMock

        emb = [0.1] * 10
        mock_node = MemoryNode(
            id=uuid4(), persona_id=PERSONA_ID, tier=MemoryTier.ACCRUED,
            content="Close match", embedding_content=emb,
        )
        backend = AsyncMock()
        backend.search_by_content.return_value = [SearchResult(node=mock_node, score=0.95)]
        backend.get_edges.return_value = []

        result = await novelty_gate.evaluate(
            {"content": "Very similar", "embedding_content": emb},
            _make_context(backend=backend),
        )
        assert result.outcome.value == "ambiguous"

    async def test_low_similarity_no_edges_ambiguous(self, novelty_gate):
        from unittest.mock import AsyncMock

        emb = [0.1] * 10
        mock_node = MemoryNode(
            id=uuid4(), persona_id=PERSONA_ID, tier=MemoryTier.ACCRUED,
            content="Distant memory", embedding_content=emb,
        )
        backend = AsyncMock()
        backend.search_by_content.return_value = [SearchResult(node=mock_node, score=0.2)]
        backend.get_edges.return_value = [
            MemoryEdge(
                id=uuid4(), source_id=mock_node.id,
                target_id=uuid4(), edge_type=EdgeType.THEMATIC,
            )
        ]

        result = await novelty_gate.evaluate(
            {"content": "Test", "embedding_content": emb},
            _make_context(backend=backend),
        )
        assert result.outcome.value == "ambiguous"

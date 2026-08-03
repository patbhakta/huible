from __future__ import annotations

from uuid import uuid4

from huible.memory.protocol import MemoryNode, MemoryTier, SearchResult
from tests.f2.conftest import PERSONA_ID, _make_context


class TestF2_2_DeduplicationGate:
    """F2.2: Deduplication gate rejects near-duplicates (cosine > 0.92)."""

    async def test_no_backend_passes(self, dedup_gate):
        result = await dedup_gate.evaluate(
            {"content": "test", "embedding_content": [0.1] * 10},
            _make_context(),
        )
        assert result.outcome.value == "pass"

    async def test_no_embedding_ambiguous(self, dedup_gate):
        result = await dedup_gate.evaluate(
            {"content": "test"},
            _make_context(backend=True),
        )
        assert result.outcome.value == "ambiguous"

    async def test_duplicate_detected(self, dedup_gate):
        mock_node = MemoryNode(
            id=uuid4(), persona_id=PERSONA_ID, tier=MemoryTier.ACCRUED,
            content="Dad loved fishing", embedding_content=[0.1] * 10,
        )
        from unittest.mock import AsyncMock
        backend = AsyncMock()
        backend.search_by_content.return_value = [SearchResult(node=mock_node, score=0.95)]

        result = await dedup_gate.evaluate(
            {"content": "Dad loved fishing", "embedding_content": [0.1] * 10},
            _make_context(backend=backend),
        )
        assert result.outcome.value == "fail"

    async def test_borderline_similarity_ambiguous(self, dedup_gate):
        from unittest.mock import AsyncMock

        emb_a = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        emb_b = [0.89, 0.456, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        mock_node = MemoryNode(
            id=uuid4(), persona_id=PERSONA_ID, tier=MemoryTier.ACCRUED,
            content="Similar text", embedding_content=emb_b,
        )
        backend = AsyncMock()
        backend.search_by_content.return_value = [SearchResult(node=mock_node, score=0.89)]

        result = await dedup_gate.evaluate(
            {"content": "Similar but not identical", "embedding_content": emb_a},
            _make_context(backend=backend),
        )
        assert result.outcome.value == "ambiguous"

    async def test_unique_content_passes(self, dedup_gate):
        from unittest.mock import AsyncMock

        mock_node = MemoryNode(
            id=uuid4(), persona_id=PERSONA_ID, tier=MemoryTier.ACCRUED,
            content="Completely different",
            embedding_content=[0.5, 0.1, 0.2, 0.8, 0.3, 0.5, 0.1, 0.2, 0.8, 0.3],
        )
        backend = AsyncMock()
        backend.search_by_content.return_value = [SearchResult(node=mock_node, score=0.3)]

        emb_new = [0.9, 0.4, 0.7, 0.1, 0.6, 0.9, 0.4, 0.7, 0.1, 0.6]
        result = await dedup_gate.evaluate(
            {"content": "New memory", "embedding_content": emb_new},
            _make_context(backend=backend),
        )
        assert result.outcome.value == "pass"

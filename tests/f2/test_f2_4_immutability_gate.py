from __future__ import annotations

from uuid import uuid4

from huible.memory.protocol import MemoryNode, MemoryTier, SearchResult
from tests.f2.conftest import PERSONA_ID, _make_context


class TestF2_4_ImmutabilityGate:
    """F2.4: Immutability gate must not conflict with canonical memories."""

    async def test_no_backend_passes(self, immutability_gate):
        result = await immutability_gate.evaluate({"content": "test"}, _make_context())
        assert result.outcome.value == "pass"

    async def test_no_canonical_memories_passes(self, immutability_gate):
        from unittest.mock import AsyncMock
        backend = AsyncMock()
        backend.search_by_content.return_value = []

        result = await immutability_gate.evaluate(
            {"content": "test", "embedding_content": [0.1] * 10},
            _make_context(backend=backend),
        )
        assert result.outcome.value == "pass"

    async def test_canonical_conflict_with_tier2_fail(self, immutability_gate):
        from unittest.mock import AsyncMock

        mock_node = MemoryNode(
            id=uuid4(), persona_id=PERSONA_ID, tier=MemoryTier.CANONICAL,
            content="Dad died in 2020", embedding_content=[0.1] * 10,
        )
        backend = AsyncMock()
        backend.search_by_content.return_value = [SearchResult(node=mock_node, score=0.9)]

        async def mock_tier2(gate_name, candidate, ctx):
            return {"outcome": "fail", "reason": "Contradicts canonical: death date conflict"}

        result = await immutability_gate.evaluate(
            {"content": "Dad is still alive", "embedding_content": [0.1] * 10},
            _make_context(backend=backend, tier2_model=mock_tier2),
        )
        assert result.outcome.value == "fail"

    async def test_canonical_no_conflict_passes(self, immutability_gate):
        from unittest.mock import AsyncMock

        mock_node = MemoryNode(
            id=uuid4(), persona_id=PERSONA_ID, tier=MemoryTier.CANONICAL,
            content="Dad loved fishing", embedding_content=[0.1] * 10,
        )
        backend = AsyncMock()
        backend.search_by_content.return_value = [SearchResult(node=mock_node, score=0.9)]

        async def mock_tier2(gate_name, candidate, ctx):
            return {"outcome": "pass", "reason": "No conflict"}

        result = await immutability_gate.evaluate(
            {"content": "Dad went fishing every summer", "embedding_content": [0.1] * 10},
            _make_context(backend=backend, tier2_model=mock_tier2),
        )
        assert result.outcome.value == "pass"

    async def test_canonical_without_tier2_ambiguous(self, immutability_gate):
        from unittest.mock import AsyncMock

        mock_node = MemoryNode(
            id=uuid4(), persona_id=PERSONA_ID, tier=MemoryTier.CANONICAL,
            content="Dad loved fishing", embedding_content=[0.1] * 10,
        )
        backend = AsyncMock()
        backend.search_by_content.return_value = [SearchResult(node=mock_node, score=0.9)]

        result = await immutability_gate.evaluate(
            {"content": "Dad never went fishing", "embedding_content": [0.1] * 10},
            _make_context(backend=backend),
        )
        assert result.outcome.value == "ambiguous"

    async def test_derived_memory_no_conflict(self, immutability_gate):
        from unittest.mock import AsyncMock

        mock_node = MemoryNode(
            id=uuid4(), persona_id=PERSONA_ID, tier=MemoryTier.DERIVED,
            content="Dad might have liked fishing", embedding_content=[0.1] * 10,
        )
        backend = AsyncMock()
        backend.search_by_content.return_value = [SearchResult(node=mock_node, score=0.7)]

        result = await immutability_gate.evaluate(
            {"content": "Dad loved fishing", "embedding_content": [0.1] * 10},
            _make_context(backend=backend),
        )
        assert result.outcome.value == "pass"

from __future__ import annotations

from uuid import uuid4

import pytest

from huible.memory.protocol import (
    EdgeType,
    MemoryEdge,
    MemoryNode,
    MemoryTier,
    SearchResult,
)

PERSONA_ID = uuid4()


def _mock_backend():
    from unittest.mock import AsyncMock

    backend = AsyncMock()
    backend.store_memory.return_value = uuid4()
    existing_node = MemoryNode(
        id=uuid4(), persona_id=PERSONA_ID, tier=MemoryTier.ACCRUED,
        content="Related memory", embedding_content=[0.5] * 10,
    )
    existing_edge = MemoryEdge(
        id=uuid4(), source_id=existing_node.id,
        target_id=uuid4(), edge_type=EdgeType.THEMATIC,
    )
    backend.search_by_content.return_value = [
        SearchResult(node=existing_node, score=0.8),
    ]
    backend.get_edges.return_value = [existing_edge]
    backend.get_active_memories.return_value = [existing_node]
    backend.add_edge.return_value = uuid4()
    return backend


@pytest.fixture
def mock_backend():
    return _mock_backend()


@pytest.fixture
def persona_id() -> uuid4:
    return PERSONA_ID

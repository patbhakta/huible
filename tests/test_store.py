from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from huible.memory.models import Base, QuarantineRow
from huible.memory.protocol import (
    ContentType,
    DisclosureScope,
    EdgeType,
    MemoryEdge,
    MemoryNode,
    MemoryTier,
    QuarantineEntry,
    QuarantinePriority,
    QuarantineStatus,
    SourceType,
)
from huible.memory.store import PostgresMemoryBackend


def _make_persona_id() -> UUID:
    return uuid4()


def _make_node(
    persona_id: UUID | None = None,
    **overrides,
) -> MemoryNode:
    defaults = {
        "id": uuid4(),
        "persona_id": persona_id or _make_persona_id(),
        "tier": MemoryTier.ACCRUED,
        "content": "Bob loved fishing on Lake Travis.",
        "content_type": ContentType.NARRATIVE,
        "source_type": SourceType.EXTRACTION,
        "disclosure_scope": DisclosureScope.FAMILY,
        "metadata": {},
    }
    defaults.update(overrides)
    return MemoryNode(**defaults)


@pytest.fixture
async def engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def configured_backend(engine):
    be = PostgresMemoryBackend.__new__(PostgresMemoryBackend)
    be._engine = engine
    be._session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False,
    )
    return be


class TestPostgresMemoryBackendStoreMemory:
    async def test_store_and_get_memory(self, configured_backend):
        node = _make_node()
        stored_id = await configured_backend.store_memory(node)

        retrieved = await configured_backend.get_memory(stored_id)
        assert retrieved is not None
        assert retrieved.id == node.id
        assert retrieved.content == node.content
        assert retrieved.tier == MemoryTier.ACCRUED
        assert retrieved.disclosure_scope == DisclosureScope.FAMILY

    async def test_get_memory_not_found(self, configured_backend):
        result = await configured_backend.get_memory(uuid4())
        assert result is None

    async def test_store_multiple_memories(self, configured_backend):
        persona_id = uuid4()
        nodes = [_make_node(persona_id=persona_id, content=f"Memory {i}") for i in range(5)]
        stored_ids = []
        for node in nodes:
            stored_id = await configured_backend.store_memory(node)
            stored_ids.append(stored_id)

        for stored_id, node in zip(stored_ids, nodes, strict=True):
            retrieved = await configured_backend.get_memory(stored_id)
            assert retrieved is not None
            assert retrieved.content == node.content


class TestPostgresMemoryBackendEdges:
    async def test_add_and_get_edges(self, configured_backend):
        source = await configured_backend.store_memory(_make_node())
        target = await configured_backend.store_memory(_make_node())

        edge = MemoryEdge(
            id=uuid4(),
            source_id=source,
            target_id=target,
            edge_type=EdgeType.THEMATIC,
            weight=0.8,
        )
        edge_id = await configured_backend.add_edge(edge)

        assert edge_id == edge.id

        edges = await configured_backend.get_edges(source)
        assert len(edges) == 1
        assert edges[0].edge_type == EdgeType.THEMATIC
        assert edges[0].weight == 0.8

    async def test_no_edges(self, configured_backend):
        node_id = await configured_backend.store_memory(_make_node())
        edges = await configured_backend.get_edges(node_id)
        assert edges == []


class TestPostgresMemoryBackendActiveMemories:
    async def test_get_active_memories(self, configured_backend):
        persona_id = uuid4()
        for _ in range(5):
            await configured_backend.store_memory(_make_node(persona_id=persona_id))

        active = await configured_backend.get_active_memories(persona_id, limit=10)
        assert len(active) == 5

    async def test_get_active_memories_limit(self, configured_backend):
        persona_id = uuid4()
        for _ in range(10):
            await configured_backend.store_memory(_make_node(persona_id=persona_id))

        active = await configured_backend.get_active_memories(persona_id, limit=3)
        assert len(active) == 3


class TestPostgresMemoryBackendSupersede:
    async def test_supersede_memory(self, configured_backend):
        persona_id = uuid4()
        old = _make_node(persona_id=persona_id, content="Old version")
        old_id = await configured_backend.store_memory(old)

        new = _make_node(persona_id=persona_id, content="New version", version=2)
        new_id = await configured_backend.supersede_memory(old_id, new)

        old_retrieved = await configured_backend.get_memory(old_id)
        assert old_retrieved is not None
        assert old_retrieved.is_active is False
        assert old_retrieved.superseded_by == new_id

        new_retrieved = await configured_backend.get_memory(new_id)
        assert new_retrieved is not None
        assert new_retrieved.is_active is True
        assert new_retrieved.supersedes == old_id
        assert new_retrieved.version == 2


class TestPostgresMemoryBackendQuarantine:
    async def test_quarantine_candidate(self, configured_backend):
        persona_id = uuid4()
        entry = QuarantineEntry(
            id=uuid4(),
            candidate_data={"content": "Suspicious memory"},
            persona_id=persona_id,
            failed_gates=["safety", "novelty"],
            priority=QuarantinePriority.HIGH,
            status=QuarantineStatus.PENDING,
        )
        q_id = await configured_backend.quarantine_candidate(entry)

        async with configured_backend._session() as session:
            result = await session.execute(
                select(QuarantineRow).where(QuarantineRow.id == q_id),
            )
            row = result.scalar_one()

        assert row.candidate_data == {"content": "Suspicious memory"}
        assert row.failed_gates == ["safety", "novelty"]
        assert row.priority == "high"
        assert row.status == "pending"


@pytest.mark.skip(reason="Vector search requires PostgreSQL + pgvector")
class TestPostgresMemoryBackendSearch:
    async def test_search_by_content_fallback(self, configured_backend):
        pass

    async def test_search_by_sensory_fallback(self, configured_backend):
        pass

    async def test_search_by_affect_fallback(self, configured_backend):
        pass

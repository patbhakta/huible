from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from huible.memory.models import Base, MemoryEdgeRow, MemoryRow, PersonaRow, QuarantineRow


@pytest.fixture
def db_url(monkeypatch):
    return "sqlite+aiosqlite:///:memory:"


@pytest.fixture
async def engine(db_url):
    eng = create_async_engine(db_url, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def session(session_factory):
    async with session_factory() as sess:
        yield sess


def _make_persona(**overrides) -> PersonaRow:
    defaults = {
        "id": uuid4(),
        "name": "Bob Mitchell",
        "display_name": "Bob",
        "voice_instructions": "Speak warmly",
    }
    defaults.update(overrides)
    return PersonaRow(**defaults)


def _make_memory(**overrides) -> MemoryRow:
    pid = overrides.pop("persona_id", uuid4())
    defaults = {
        "id": uuid4(),
        "persona_id": pid,
        "tier": "accrued",
        "content": "Bob loved fishing on Lake Travis.",
        "content_type": "narrative",
        "source_type": "extraction",
        "disclosure_scope": "family",
        "metadata_": {},
    }
    defaults.update(overrides)
    return MemoryRow(**defaults)


def _make_edge(**overrides) -> MemoryEdgeRow:
    defaults = {
        "id": uuid4(),
        "source_id": uuid4(),
        "target_id": uuid4(),
        "edge_type": "thematic",
        "weight": 0.8,
        "metadata_": {},
    }
    defaults.update(overrides)
    return MemoryEdgeRow(**defaults)


def _make_quarantine(**overrides) -> QuarantineRow:
    pid = overrides.pop("persona_id", uuid4())
    defaults = {
        "id": uuid4(),
        "candidate_data": {"content": "test"},
        "persona_id": pid,
        "failed_gates": ["novelty"],
        "priority": "medium",
        "status": "pending",
    }
    defaults.update(overrides)
    return QuarantineRow(**defaults)


class TestPersonaRow:
    async def test_create_and_retrieve(self, session):
        persona = _make_persona()
        session.add(persona)
        await session.commit()

        result = await session.execute(select(PersonaRow).where(PersonaRow.id == persona.id))
        row = result.scalar_one()

        assert row.name == "Bob Mitchell"
        assert row.display_name == "Bob"
        assert row.voice_instructions == "Speak warmly"
        assert row.metadata_ == {}
        assert row.created_at is not None

    async def test_default_values(self, session):
        persona = PersonaRow(id=uuid4(), name="Alice")
        session.add(persona)
        await session.commit()

        result = await session.execute(select(PersonaRow).where(PersonaRow.id == persona.id))
        row = result.scalar_one()

        assert row.display_name is None
        assert row.voice_instructions == ""
        assert row.era_knowledge_boundary == "2020-01-01"
        assert row.metadata_ == {}


class TestMemoryRow:
    async def test_create_and_retrieve(self, session):
        persona_id = uuid4()
        memory = _make_memory(persona_id=persona_id)
        session.add(memory)
        await session.commit()

        result = await session.execute(select(MemoryRow).where(MemoryRow.id == memory.id))
        row = result.scalar_one()

        assert row.persona_id == persona_id
        assert row.tier == "accrued"
        assert row.content == "Bob loved fishing on Lake Travis."
        assert row.content_type == "narrative"
        assert row.source_type == "extraction"
        assert row.disclosure_scope == "family"
        assert row.version == 1
        assert row.is_active is True

    async def test_all_tiers(self, session):
        for tier in ["canonical", "derived", "accrued", "world"]:
            session.add(_make_memory(tier=tier))
        await session.commit()

        result = await session.execute(select(MemoryRow.tier).distinct())
        tiers = sorted(r[0] for r in result.all())
        assert tiers == ["accrued", "canonical", "derived", "world"]

    async def test_all_content_types(self, session):
        for ct in ["narrative", "fact", "sensory", "relationship", "preference"]:
            session.add(_make_memory(content_type=ct))
        await session.commit()

        result = await session.execute(select(MemoryRow.content_type).distinct())
        types = sorted(r[0] for r in result.all())
        assert types == ["fact", "narrative", "preference", "relationship", "sensory"]

    async def test_all_disclosure_scopes(self, session):
        for ds in ["private", "family", "close_friends", "all_contacts"]:
            session.add(_make_memory(disclosure_scope=ds))
        await session.commit()

        result = await session.execute(select(MemoryRow.disclosure_scope).distinct())
        scopes = sorted(r[0] for r in result.all())
        assert scopes == ["all_contacts", "close_friends", "family", "private"]


class TestMemoryEdgeRow:
    async def test_create_and_retrieve(self, session):
        edge = _make_edge()
        session.add(edge)
        await session.commit()

        result = await session.execute(select(MemoryEdgeRow).where(MemoryEdgeRow.id == edge.id))
        row = result.scalar_one()

        assert row.edge_type == "thematic"
        assert row.weight == 0.8

    async def test_all_edge_types(self, session):
        types = [
            "shared_participant", "temporal_proximity", "thematic",
            "causal", "contradiction", "elaboration",
        ]
        for et in types:
            session.add(_make_edge(edge_type=et))
        await session.commit()

        result = await session.execute(select(MemoryEdgeRow.edge_type).distinct())
        db_types = sorted(r[0] for r in result.all())
        assert db_types == sorted(types)


class TestQuarantineRow:
    async def test_create_and_retrieve(self, session):
        q = _make_quarantine()
        session.add(q)
        await session.commit()

        result = await session.execute(select(QuarantineRow).where(QuarantineRow.id == q.id))
        row = result.scalar_one()

        assert row.candidate_data == {"content": "test"}
        assert row.failed_gates == ["novelty"]
        assert row.priority == "medium"
        assert row.status == "pending"

    async def test_priority_statuses(self, session):
        for p in ["critical", "high", "medium", "low"]:
            session.add(_make_quarantine(priority=p))
        await session.commit()

        result = await session.execute(select(QuarantineRow.priority).distinct())
        priorities = sorted(r[0] for r in result.all())
        assert priorities == ["critical", "high", "low", "medium"]

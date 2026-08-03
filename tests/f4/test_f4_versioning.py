from __future__ import annotations

from huible.memory.protocol import MemoryTier
from tests.f4.conftest import _make_node


class TestF4_1_AppendOnlySupersession:
    """F4.1: Memories are superseded, never overwritten. Old version becomes inactive."""

    async def test_supersede_marks_old_inactive(self, backend, persona_id):
        old = _make_node(persona_id, "Dad loved fishing")
        await backend.store_memory(old)

        new = _make_node(persona_id, "Dad loved fishing on Lake Travis every summer")
        await backend.supersede_memory(old.id, new)

        retrieved_old = await backend.get_memory(old.id)
        assert retrieved_old is not None
        assert retrieved_old.is_active is False

    async def test_supersede_creates_new_active(self, backend, persona_id):
        old = _make_node(persona_id, "Dad loved fishing")
        await backend.store_memory(old)

        new = _make_node(persona_id, "Dad loved fishing on Lake Travis")
        await backend.supersede_memory(old.id, new)

        retrieved_new = await backend.get_memory(new.id)
        assert retrieved_new is not None
        assert retrieved_new.is_active is True
        assert retrieved_new.content == "Dad loved fishing on Lake Travis"

    async def test_supersede_sets_version_chain(self, backend, persona_id):
        old = _make_node(persona_id, "Original memory", version=1)
        await backend.store_memory(old)

        new = _make_node(persona_id, "Updated memory", version=2, supersedes=old.id)
        await backend.supersede_memory(old.id, new)

        retrieved_new = await backend.get_memory(new.id)
        assert retrieved_new.supersedes == old.id
        assert retrieved_new.version == 2


class TestF4_2_ActiveMemoriesOnly:
    """F4.2: Queries default to active memories only."""

    async def test_get_active_excludes_superseded(self, backend, persona_id):
        old = _make_node(persona_id, "Old version")
        await backend.store_memory(old)

        new = _make_node(persona_id, "New version")
        await backend.supersede_memory(old.id, new)

        active = await backend.get_active_memories(persona_id, limit=50)
        active_ids = {m.id for m in active}
        assert old.id not in active_ids
        assert new.id in active_ids

    async def test_superseded_still_retrievable_by_id(self, backend, persona_id):
        old = _make_node(persona_id, "Old memory")
        await backend.store_memory(old)

        new = _make_node(persona_id, "New memory")
        await backend.supersede_memory(old.id, new)

        retrieved = await backend.get_memory(old.id)
        assert retrieved is not None
        assert retrieved.content == "Old memory"
        assert retrieved.is_active is False


class TestF4_3_VersionHistory:
    """F4.3: Full version chain is maintained for audit."""

    async def test_multiple_supersessions_tracked(self, backend, persona_id):
        v1 = _make_node(persona_id, "Version 1", version=1)
        await backend.store_memory(v1)

        v2 = _make_node(persona_id, "Version 2", version=2, supersedes=v1.id)
        await backend.supersede_memory(v1.id, v2)

        v3 = _make_node(persona_id, "Version 3", version=3, supersedes=v2.id)
        await backend.supersede_memory(v2.id, v3)

        r_v1 = await backend.get_memory(v1.id)
        r_v2 = await backend.get_memory(v2.id)
        r_v3 = await backend.get_memory(v3.id)

        assert r_v1.is_active is False
        assert r_v2.is_active is False
        assert r_v3.is_active is True
        assert r_v3.supersedes == v2.id

    async def test_content_preserved_across_versions(self, backend, persona_id):
        v1 = _make_node(persona_id, "First memory content")
        await backend.store_memory(v1)

        v2 = _make_node(persona_id, "Second memory content")
        await backend.supersede_memory(v1.id, v2)

        r_v1 = await backend.get_memory(v1.id)
        assert r_v1.content == "First memory content"


class TestF4_4_CanonicalTierProtection:
    """F4.4: Canonical memories have extra protection (INV-CI)."""

    async def test_canonical_stored_correctly(self, backend, persona_id):
        canonical = _make_node(persona_id, "Bob was born in Austin", tier=MemoryTier.CANONICAL)
        await backend.store_memory(canonical)

        retrieved = await backend.get_memory(canonical.id)
        assert retrieved.tier == MemoryTier.CANONICAL
        assert retrieved.content == "Bob was born in Austin"

    async def test_canonical_has_active_flag(self, backend, persona_id):
        canonical = _make_node(persona_id, "Core fact", tier=MemoryTier.CANONICAL)
        await backend.store_memory(canonical)

        active = await backend.get_active_memories(persona_id, limit=50)
        canonical_active = [m for m in active if m.tier == MemoryTier.CANONICAL]
        assert len(canonical_active) >= 1

"""INV-16: Append-Only Versioning

Invariant: Memories are superseded, never overwritten. The content of a
superseded memory must remain immutable. A new version is created with a
reference to the old version.

This invariant is enforced at the backend level. The supersede_memory
operation must:
1. Mark the old memory as inactive (never delete or overwrite)
2. Create a new active memory with a version chain
3. Preserve the original content of the old memory exactly
"""

from __future__ import annotations

from tests.invariants.conftest import PERSONA_ID, make_node


class TestInv16ContentNeverOverwritten:
    """INV-16a: Original memory content is never modified by supersession."""

    async def test_content_preserved_after_supersede(self, backend):
        original = make_node(PERSONA_ID, content="Dad loved fishing on Lake Travis")
        await backend.store_memory(original)

        replacement = make_node(PERSONA_ID, content="Dad loved deep-sea fishing")
        await backend.supersede_memory(original.id, replacement)

        old = await backend.get_memory(original.id)
        assert old is not None
        assert old.content == "Dad loved fishing on Lake Travis"
        assert old.is_active is False

    async def test_multiple_supersessions_preserve_all_versions(self, backend):
        v1 = make_node(PERSONA_ID, content="Version 1", version=1)
        await backend.store_memory(v1)

        v2 = make_node(PERSONA_ID, content="Version 2", version=2, supersedes=v1.id)
        await backend.supersede_memory(v1.id, v2)

        v3 = make_node(PERSONA_ID, content="Version 3", version=3, supersedes=v2.id)
        await backend.supersede_memory(v2.id, v3)

        r1 = await backend.get_memory(v1.id)
        r2 = await backend.get_memory(v2.id)
        r3 = await backend.get_memory(v3.id)

        assert r1.content == "Version 1"
        assert r2.content == "Version 2"
        assert r3.content == "Version 3"

        assert r1.is_active is False
        assert r2.is_active is False
        assert r3.is_active is True


class TestInv16SupersedeCreatesNewRecord:
    """INV-16b: Supersession always creates a new record, never modifies the old."""

    async def test_supersede_preserves_old_id(self, backend):
        original = make_node(PERSONA_ID, content="Original")
        await backend.store_memory(original)

        new = make_node(PERSONA_ID, content="Updated")
        new_id = await backend.supersede_memory(original.id, new)

        assert new_id == new.id
        assert new_id != original.id

        old = await backend.get_memory(original.id)
        assert old is not None
        assert old.id == original.id

    async def test_old_memory_still_retrievable(self, backend):
        original = make_node(PERSONA_ID, content="Historical fact")
        await backend.store_memory(original)

        updated = make_node(PERSONA_ID, content="Updated fact")
        await backend.supersede_memory(original.id, updated)

        old = await backend.get_memory(original.id)
        assert old is not None
        assert old.content == "Historical fact"


class TestInv16VersionChainIntegrity:
    """INV-16c: The version chain must be complete and consistent."""

    async def test_only_one_active_after_supersessions(self, backend):
        versions = []
        prev_id = None
        for i in range(1, 6):
            v = make_node(PERSONA_ID, f"Version {i}", version=i, supersedes=prev_id)
            if prev_id is None:
                await backend.store_memory(v)
            else:
                await backend.supersede_memory(prev_id, v)
            versions.append(v)
            prev_id = v.id

        for i, v in enumerate(versions):
            retrieved = await backend.get_memory(v.id)
            if i == len(versions) - 1:
                assert retrieved.is_active is True
            else:
                assert retrieved.is_active is False

    async def test_content_chain_preserved(self, backend):
        v1 = make_node(PERSONA_ID, content="Version 1", version=1)
        await backend.store_memory(v1)

        v2 = make_node(PERSONA_ID, content="Version 2", version=2, supersedes=v1.id)
        await backend.supersede_memory(v1.id, v2)

        v3 = make_node(PERSONA_ID, content="Version 3", version=3, supersedes=v2.id)
        await backend.supersede_memory(v2.id, v3)

        r1 = await backend.get_memory(v1.id)
        r3 = await backend.get_memory(v3.id)

        assert r1.content == "Version 1"
        assert r3.content == "Version 3"
        assert r1.is_active is False
        assert r3.is_active is True

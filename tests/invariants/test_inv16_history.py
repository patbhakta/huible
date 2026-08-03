from __future__ import annotations

import dataclasses
from datetime import date
from uuid import UUID, uuid4

from huible.adjudication.history import (
    MemoryHistory,
)
from huible.memory.protocol import (
    ContentType,
    DisclosureScope,
    MemoryNode,
    MemoryTier,
    SourceType,
)


class FakeHistoryStore:
    def __init__(self) -> None:
        self._memories: dict[UUID, MemoryNode] = {}

    async def get_memory(self, memory_id: UUID) -> MemoryNode | None:
        return self._memories.get(memory_id)

    async def get_all_versions(self, memory_id: UUID) -> list[MemoryNode]:
        root = self._memories.get(memory_id)
        if root is None:
            return []
        versions = [root]
        current = root
        while current.superseded_by is not None:
            next_id = current.superseded_by
            if next_id in self._memories:
                versions.append(self._memories[next_id])
                current = self._memories[next_id]
            else:
                break
        return versions

    def add(self, node: MemoryNode) -> None:
        self._memories[node.id] = node


PERSONA_ID = uuid4()


def _make_node(
    content: str,
    version: int = 1,
    is_active: bool = True,
    supersedes: UUID | None = None,
    superseded_by: UUID | None = None,
    tier: MemoryTier = MemoryTier.ACCRUED,
    node_id: UUID | None = None,
    **kwargs,
) -> MemoryNode:
    return MemoryNode(
        id=node_id or uuid4(),
        persona_id=PERSONA_ID,
        tier=tier,
        content=content,
        content_type=ContentType.NARRATIVE,
        memory_date=date(2015, 7, 15),
        source_type=SourceType.EXTRACTION,
        disclosure_scope=DisclosureScope.FAMILY,
        version=version,
        is_active=is_active,
        supersedes=supersedes,
        superseded_by=superseded_by,
        **kwargs,
    )


class TestVersionChain:
    async def test_single_version_chain(self):
        store = FakeHistoryStore()
        node = _make_node("Original memory")
        store.add(node)

        history = MemoryHistory(store)
        chain = await history.get_version_chain(node.id)

        assert chain.version_count == 1
        assert chain.current is not None
        assert chain.current.memory.content == "Original memory"
        assert chain.is_unchanged is True

    async def test_multi_version_chain(self):
        store = FakeHistoryStore()
        v1_id = uuid4()
        v2_id = uuid4()
        v1 = _make_node("Version 1", version=1, node_id=v1_id)
        v1 = dataclasses.replace(v1, is_active=False, superseded_by=v2_id)
        v2 = _make_node("Version 2", version=2, supersedes=v1_id, node_id=v2_id)
        store.add(v1)
        store.add(v2)

        history = MemoryHistory(store)
        chain = await history.get_version_chain(v1_id)

        assert chain.version_count == 2
        assert chain.versions[0].version == 1
        assert chain.versions[0].is_active is False
        assert chain.versions[1].version == 2
        assert chain.versions[1].is_active is True
        assert chain.current.version == 2
        assert chain.is_unchanged is False

    async def test_long_version_chain(self):
        store = FakeHistoryStore()
        ids = [uuid4() for _ in range(5)]
        nodes: list[MemoryNode] = []
        for i in range(5):
            supersedes = ids[i - 1] if i > 0 else None
            node = _make_node(
                f"Version {i+1}", version=i+1,
                supersedes=supersedes, node_id=ids[i],
            )
            nodes.append(node)
        for i in range(5):
            if i < 4:
                nodes[i] = dataclasses.replace(nodes[i], is_active=False, superseded_by=ids[i + 1])
            store.add(nodes[i])

        history = MemoryHistory(store)
        chain = await history.get_version_chain(ids[0])

        assert chain.version_count == 5
        assert chain.latest.version == 5
        assert chain.current.version == 5


class TestAuditTrail:
    async def test_audit_trail_has_actions(self):
        store = FakeHistoryStore()
        v1 = _make_node("Version 1", version=1)
        store.add(v1)

        history = MemoryHistory(store)
        trail = await history.get_full_audit_trail(v1.id)

        assert len(trail) == 1
        assert trail[0].action == "created"
        assert trail[0].is_active is True

    async def test_audit_trail_superseded(self):
        store = FakeHistoryStore()
        v1_id = uuid4()
        v2_id = uuid4()
        v1 = _make_node("Version 1", version=1, node_id=v1_id)
        v1 = dataclasses.replace(v1, is_active=False, superseded_by=v2_id)
        v2 = _make_node("Version 2", version=2, supersedes=v1_id, node_id=v2_id)
        store.add(v1)
        store.add(v2)

        history = MemoryHistory(store)
        trail = await history.get_full_audit_trail(v1_id)

        assert len(trail) == 2
        assert trail[0].action == "created"
        assert trail[0].is_active is False
        assert trail[1].action == "superseded"
        assert trail[1].is_active is True


class TestVersionDiff:
    async def test_diff_detects_content_change(self):
        store = FakeHistoryStore()
        v1 = _make_node("Original content")
        v2 = _make_node("Updated content")
        store.add(v1)
        store.add(v2)

        history = MemoryHistory(store)
        diff = await history.diff_versions(v1.id, v2.id)

        assert "content" in diff
        assert diff["content"] == ("Original content", "Updated content")

    async def test_diff_detects_tier_change(self):
        store = FakeHistoryStore()
        v1 = _make_node("Same content", tier=MemoryTier.ACCRUED)
        v2 = _make_node("Same content", tier=MemoryTier.CANONICAL)
        store.add(v1)
        store.add(v2)

        history = MemoryHistory(store)
        diff = await history.diff_versions(v1.id, v2.id)

        assert "tier" in diff
        assert diff["tier"] == ("accrued", "canonical")

    async def test_diff_no_change(self):
        store = FakeHistoryStore()
        v1 = _make_node("Same content", tier=MemoryTier.ACCRUED)
        v2 = _make_node("Same content", tier=MemoryTier.ACCRUED)
        store.add(v1)
        store.add(v2)

        history = MemoryHistory(store)
        diff = await history.diff_versions(v1.id, v2.id)

        assert len(diff) == 0


class TestQuarantineEligibility:
    async def test_active_accrued_is_eligible(self):
        store = FakeHistoryStore()
        node = _make_node("Test", tier=MemoryTier.ACCRUED, is_active=True)
        store.add(node)

        history = MemoryHistory(store)
        assert await history.is_quarantine_eligible(node.id) is True

    async def test_canonical_is_not_eligible(self):
        store = FakeHistoryStore()
        node = _make_node("Test", tier=MemoryTier.CANONICAL, is_active=True)
        store.add(node)

        history = MemoryHistory(store)
        assert await history.is_quarantine_eligible(node.id) is False

    async def test_inactive_is_not_eligible(self):
        store = FakeHistoryStore()
        node = _make_node("Test", tier=MemoryTier.ACCRUED, is_active=False)
        store.add(node)

        history = MemoryHistory(store)
        assert await history.is_quarantine_eligible(node.id) is False

    async def test_nonexistent_is_not_eligible(self):
        store = FakeHistoryStore()
        history = MemoryHistory(store)
        assert await history.is_quarantine_eligible(uuid4()) is False

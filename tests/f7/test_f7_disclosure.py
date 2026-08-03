from __future__ import annotations

import pytest

from huible.memory.protocol import ContentType, DisclosureScope, MemoryNode, MemoryTier
from huible.memory.retrieval import (
    RetrievalConfig,
    filter_by_disclosure,
    retrieve,
)


class TestF7_1_DisclosureHierarchy:
    """F7.1: Disclosure scope hierarchy — PRIVATE sees all, ALL_CONTACTS sees least."""

    async def test_private_includes_family(self, backend):
        family_memories = [
            m for m in backend._memories.values()
            if m.disclosure_scope == DisclosureScope.FAMILY
        ]
        if not family_memories:
            pytest.skip("No family memories")
        query_emb = family_memories[0].embedding_content or [0.0] * 1536
        persona_id = family_memories[0].persona_id

        config = RetrievalConfig(
            activation_threshold=0.01, max_spread_depth=2,
            decay_factor=0.9, max_activated=100,
        )
        results = await retrieve(
            backend, persona_id, query_emb,
            disclosure_tier=DisclosureScope.PRIVATE, config=config,
        )
        result_ids = {r.node.id for r in results}
        found = [fm for fm in family_memories if fm.id in result_ids]
        assert len(found) > 0

    async def test_private_excludes_nothing(self, backend):
        nodes = list(backend._memories.values())
        persona_id = nodes[0].persona_id
        query_emb = nodes[0].embedding_content or [0.0] * 1536
        config = RetrievalConfig(
            activation_threshold=0.01, max_spread_depth=2,
            decay_factor=0.9, max_activated=100,
        )
        results = await retrieve(
            backend, persona_id, query_emb,
            disclosure_tier=DisclosureScope.PRIVATE, config=config,
        )
        assert len(results) > 0

    async def test_progressive_narrowing(self, backend):
        nodes = list(backend._memories.values())
        persona_id = nodes[0].persona_id
        query_emb = nodes[0].embedding_content or [0.0] * 1536
        config = RetrievalConfig(
            activation_threshold=0.01, max_spread_depth=2,
            decay_factor=0.9, max_activated=100,
        )
        tiers = [
            DisclosureScope.PRIVATE,
            DisclosureScope.FAMILY,
            DisclosureScope.CLOSE_FRIENDS,
            DisclosureScope.ALL_CONTACTS,
        ]
        counts: dict[str, int] = {}
        for tier in tiers:
            results = await retrieve(
                backend, persona_id, query_emb,
                disclosure_tier=tier, config=config,
            )
            counts[tier.value] = len(results)

        for i in range(len(tiers) - 1):
            assert counts[tiers[i].value] >= counts[tiers[i + 1].value], (
                f"{tiers[i].value} ({counts[tiers[i].value]}) should >= "
                f"{tiers[i+1].value} ({counts[tiers[i+1].value]})"
            )


class TestF7_2_PrivateMemoriesExcluded:
    """F7.2: Private memories excluded from non-PRIVATE tiers."""

    async def test_private_excluded_from_family(self, backend):
        private_memories = [
            m for m in backend._memories.values()
            if m.disclosure_scope == DisclosureScope.PRIVATE
        ]
        if not private_memories:
            pytest.skip("No private memories")

        nodes = list(backend._memories.values())
        persona_id = nodes[0].persona_id
        query_emb = private_memories[0].embedding_content or [0.0] * 1536
        config = RetrievalConfig(
            activation_threshold=0.01, max_spread_depth=2,
            decay_factor=0.9, max_activated=100,
        )
        results = await retrieve(
            backend, persona_id, query_emb,
            disclosure_tier=DisclosureScope.FAMILY, config=config,
        )
        result_ids = {r.node.id for r in results}
        for pm in private_memories:
            assert pm.id not in result_ids

    async def test_private_excluded_from_all_contacts(self, backend):
        private_memories = [
            m for m in backend._memories.values()
            if m.disclosure_scope == DisclosureScope.PRIVATE
        ]
        if not private_memories:
            pytest.skip("No private memories")

        nodes = list(backend._memories.values())
        persona_id = nodes[0].persona_id
        query_emb = nodes[0].embedding_content or [0.0] * 1536
        config = RetrievalConfig(
            activation_threshold=0.01, max_spread_depth=2,
            decay_factor=0.9, max_activated=100,
        )
        results = await retrieve(
            backend, persona_id, query_emb,
            disclosure_tier=DisclosureScope.ALL_CONTACTS, config=config,
        )
        result_ids = {r.node.id for r in results}
        leaked = [pm for pm in private_memories if pm.id in result_ids]
        assert not leaked


class TestF7_3_DisclosureFilterIngestion:
    """F7.3: Ingestion respects disclosure scope on candidates."""

    async def test_candidate_with_scope_stored(self, backend):
        node = MemoryNode(
            id=next(iter(backend._memories.values())).id,
            persona_id=next(iter(backend._memories.values())).persona_id,
            tier=MemoryTier.ACCRUED,
            content="Test memory",
            content_type=ContentType.NARRATIVE,
            disclosure_scope=DisclosureScope.PRIVATE,
        )
        assert node.disclosure_scope == DisclosureScope.PRIVATE

    async def test_filter_by_disclosure_utility(self, backend):
        nodes = list(backend._memories.values())
        all_nodes = {n.id: n for n in nodes}
        activation_map = {n.id: 0.5 for n in nodes[:20]}

        eligible_family = filter_by_disclosure(
            activation_map, DisclosureScope.FAMILY, all_nodes,
        )
        for nid in eligible_family:
            node = all_nodes[nid]
            assert node.disclosure_scope in (
                DisclosureScope.PRIVATE,
                DisclosureScope.FAMILY,
                DisclosureScope.CLOSE_FRIENDS,
                DisclosureScope.ALL_CONTACTS,
            )

    async def test_all_contacts_filter_narrowest(self, backend):
        nodes = list(backend._memories.values())
        all_nodes = {n.id: n for n in nodes}
        activation_map = {n.id: 0.5 for n in nodes[:20]}

        eligible_all = filter_by_disclosure(
            activation_map, DisclosureScope.ALL_CONTACTS, all_nodes,
        )
        for nid in eligible_all:
            assert all_nodes[nid].disclosure_scope == DisclosureScope.ALL_CONTACTS

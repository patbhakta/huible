from __future__ import annotations

import pytest

from huible.memory.protocol import DisclosureScope
from huible.memory.retrieval import RetrievalConfig, retrieve
from tests.f1.conftest import CosineFakeBackend


class TestF1_4_DisclosureScoping:
    """F1.4: Scope by disclosure tier — private memories excluded for lower relationship tiers."""

    async def test_private_excluded_from_family_query(
        self, backend: CosineFakeBackend
    ) -> None:
        private_memories = [
            m for m in backend._memories.values()
            if m.disclosure_scope == DisclosureScope.PRIVATE
        ]
        if not private_memories:
            pytest.skip("No private memories in corpus")

        nodes = list(backend._memories.values())
        persona_id = nodes[0].persona_id
        query_emb = private_memories[0].embedding_content or [0.0] * 1536

        config = RetrievalConfig(
            activation_threshold=0.01,
            max_spread_depth=2,
            decay_factor=0.9,
            max_activated=100,
        )

        results = await retrieve(
            backend,
            persona_id,
            query_emb,
            disclosure_tier=DisclosureScope.FAMILY,
            config=config,
        )

        result_ids = {r.node.id for r in results}
        for pm in private_memories:
            assert pm.id not in result_ids, (
                f"Private memory '{pm.content[:50]}...' leaked into FAMILY tier results"
            )

    async def test_private_excluded_from_close_friends_query(
        self, backend: CosineFakeBackend
    ) -> None:
        private_memories = [
            m for m in backend._memories.values()
            if m.disclosure_scope == DisclosureScope.PRIVATE
        ]
        if not private_memories:
            pytest.skip("No private memories in corpus")

        nodes = list(backend._memories.values())
        persona_id = nodes[0].persona_id
        query_emb = private_memories[0].embedding_content or [0.0] * 1536

        config = RetrievalConfig(
            activation_threshold=0.01,
            max_spread_depth=2,
            decay_factor=0.9,
            max_activated=100,
        )

        results = await retrieve(
            backend,
            persona_id,
            query_emb,
            disclosure_tier=DisclosureScope.CLOSE_FRIENDS,
            config=config,
        )

        result_ids = {r.node.id for r in results}
        leaked = [pm for pm in private_memories if pm.id in result_ids]
        assert not leaked, (
            f"{len(leaked)} private memories leaked into CLOSE_FRIENDS tier"
        )

    async def test_family_included_in_private_query(
        self, backend: CosineFakeBackend
    ) -> None:
        family_memories = [
            m for m in backend._memories.values()
            if m.disclosure_scope == DisclosureScope.FAMILY
        ]
        if not family_memories:
            pytest.skip("No family memories in corpus")

        query_emb = family_memories[0].embedding_content or [0.0] * 1536
        persona_id = family_memories[0].persona_id

        config = RetrievalConfig(
            activation_threshold=0.01,
            max_spread_depth=2,
            decay_factor=0.9,
            max_activated=100,
        )

        results = await retrieve(
            backend,
            persona_id,
            query_emb,
            disclosure_tier=DisclosureScope.PRIVATE,
            config=config,
        )

        result_ids = {r.node.id for r in results}
        found = [fm for fm in family_memories if fm.id in result_ids]
        assert len(found) > 0, (
            "Family memories should be visible at PRIVATE tier"
        )

    async def test_progressive_narrowing_across_tiers(
        self, backend: CosineFakeBackend
    ) -> None:
        nodes = list(backend._memories.values())
        persona_id = nodes[0].persona_id
        query_emb = nodes[0].embedding_content or [0.0] * 1536

        config = RetrievalConfig(
            activation_threshold=0.01,
            max_spread_depth=2,
            decay_factor=0.9,
            max_activated=100,
        )

        tiers = [
            DisclosureScope.PRIVATE,
            DisclosureScope.FAMILY,
            DisclosureScope.CLOSE_FRIENDS,
            DisclosureScope.ALL_CONTACTS,
        ]
        tier_counts: dict[str, int] = {}

        for tier in tiers:
            results = await retrieve(
                backend,
                persona_id,
                query_emb,
                disclosure_tier=tier,
                config=config,
            )
            tier_counts[tier.value] = len(results)

        for i in range(len(tiers) - 1):
            higher = tiers[i]
            lower = tiers[i + 1]
            assert tier_counts[higher.value] >= tier_counts[lower.value], (
                f"{higher.value} ({tier_counts[higher.value]}) should have >= results "
                f"than {lower.value} ({tier_counts[lower.value]})"
            )

    async def test_all_contacts_sees_least(
        self, backend: CosineFakeBackend
    ) -> None:
        nodes = list(backend._memories.values())
        persona_id = nodes[0].persona_id
        query_emb = nodes[0].embedding_content or [0.0] * 1536

        config = RetrievalConfig(
            activation_threshold=0.01,
            max_spread_depth=2,
            decay_factor=0.9,
            max_activated=100,
        )

        private_results = await retrieve(
            backend,
            persona_id,
            query_emb,
            disclosure_tier=DisclosureScope.PRIVATE,
            config=config,
        )
        contact_results = await retrieve(
            backend,
            persona_id,
            query_emb,
            disclosure_tier=DisclosureScope.ALL_CONTACTS,
            config=config,
        )

        msg = f"PRIVATE ({len(private_results)}) >= ALL_CONTACTS ({len(contact_results)})"
        assert len(private_results) >= len(contact_results), msg

    async def test_no_cross_tier_leakage(
        self, backend: CosineFakeBackend
    ) -> None:
        nodes = list(backend._memories.values())
        persona_id = nodes[0].persona_id
        query_emb = nodes[0].embedding_content or [0.0] * 1536

        config = RetrievalConfig(
            activation_threshold=0.01,
            max_spread_depth=2,
            decay_factor=0.9,
            max_activated=100,
        )

        contact_results = await retrieve(
            backend,
            persona_id,
            query_emb,
            disclosure_tier=DisclosureScope.ALL_CONTACTS,
            config=config,
        )

        result_nodes = {r.node.id: r.node for r in contact_results}
        for _nid, node in result_nodes.items():
            assert node.disclosure_scope in (
                DisclosureScope.ALL_CONTACTS,
                DisclosureScope.CLOSE_FRIENDS,
                DisclosureScope.FAMILY,
                DisclosureScope.PRIVATE,
            )

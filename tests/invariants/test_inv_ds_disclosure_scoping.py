"""INV-DS: Disclosure Scoping

Invariant: Relationship tier determines memory access. A memory with a
higher disclosure scope must NEVER be visible to a lower tier. This is
a hard guarantee — not a soft preference.

Hierarchy: ALL_CONTACTS < CLOSE_FRIENDS < FAMILY < PRIVATE

PRIVATE memories must never appear in retrieval results for FAMILY,
CLOSE_FRIENDS, or ALL_CONTACTS queries.
"""

from __future__ import annotations

from uuid import uuid4

from huible.memory.protocol import (
    DisclosureScope,
)
from huible.memory.retrieval import (
    RetrievalConfig,
    retrieve,
)
from tests.invariants.conftest import PERSONA_ID, make_node

QUERY_VEC = [0.1] * 1536

TIER_PRIVATE_EXCLUDED_FROM = [
    DisclosureScope.FAMILY,
    DisclosureScope.CLOSE_FRIENDS,
    DisclosureScope.ALL_CONTACTS,
]

TIER_FAMILY_EXCLUDED_FROM = [
    DisclosureScope.CLOSE_FRIENDS,
    DisclosureScope.ALL_CONTACTS,
]

TIER_CLOSE_FRIENDS_EXCLUDED_FROM = [
    DisclosureScope.ALL_CONTACTS,
]


class TestInvDSPrivateNeverLeaked:
    """INV-DSa: PRIVATE memories never visible to non-PRIVATE queries."""

    async def test_private_excluded_from_family(self, backend):
        private = make_node(
            PERSONA_ID,
            content="Private family secret",
            disclosure_scope=DisclosureScope.PRIVATE,
            embedding_content=[0.5] * 1536,
        )
        await backend.store_memory(private)

        results = await retrieve(
            backend,
            PERSONA_ID,
            QUERY_VEC,
            disclosure_tier=DisclosureScope.FAMILY,
            config=RetrievalConfig(activation_threshold=0.0, seed_top_k=100, max_activated=100),
        )

        result_ids = {am.node.id for am in results}
        assert private.id not in result_ids

    async def test_private_excluded_from_close_friends(self, backend):
        private = make_node(
            PERSONA_ID,
            content="Very private",
            disclosure_scope=DisclosureScope.PRIVATE,
            embedding_content=[0.5] * 1536,
        )
        await backend.store_memory(private)

        results = await retrieve(
            backend,
            PERSONA_ID,
            QUERY_VEC,
            disclosure_tier=DisclosureScope.CLOSE_FRIENDS,
            config=RetrievalConfig(activation_threshold=0.0, seed_top_k=100, max_activated=100),
        )

        result_ids = {am.node.id for am in results}
        assert private.id not in result_ids

    async def test_private_excluded_from_all_contacts(self, backend):
        private = make_node(
            PERSONA_ID,
            content="Intensely private",
            disclosure_scope=DisclosureScope.PRIVATE,
            embedding_content=[0.5] * 1536,
        )
        await backend.store_memory(private)

        results = await retrieve(
            backend,
            PERSONA_ID,
            QUERY_VEC,
            disclosure_tier=DisclosureScope.ALL_CONTACTS,
            config=RetrievalConfig(activation_threshold=0.0, seed_top_k=100, max_activated=100),
        )

        result_ids = {am.node.id for am in results}
        assert private.id not in result_ids

    async def test_private_visible_to_private_tier(self, backend):
        private = make_node(
            PERSONA_ID,
            content="Private memory",
            disclosure_scope=DisclosureScope.PRIVATE,
            embedding_content=[0.5] * 1536,
        )
        await backend.store_memory(private)

        results = await retrieve(
            backend,
            PERSONA_ID,
            QUERY_VEC,
            disclosure_tier=DisclosureScope.PRIVATE,
            config=RetrievalConfig(activation_threshold=0.0, seed_top_k=100, max_activated=100),
        )

        result_ids = {am.node.id for am in results}
        assert private.id in result_ids


class TestInvDSFamilyNotLeakedToLower:
    """INV-DSb: FAMILY memories never visible to CLOSE_FRIENDS or ALL_CONTACTS."""

    async def test_family_excluded_from_close_friends(self, backend):
        family = make_node(
            PERSONA_ID,
            content="Family story",
            disclosure_scope=DisclosureScope.FAMILY,
            embedding_content=[0.5] * 1536,
        )
        await backend.store_memory(family)

        results = await retrieve(
            backend,
            PERSONA_ID,
            QUERY_VEC,
            disclosure_tier=DisclosureScope.CLOSE_FRIENDS,
            config=RetrievalConfig(activation_threshold=0.0, seed_top_k=100, max_activated=100),
        )

        result_ids = {am.node.id for am in results}
        assert family.id not in result_ids

    async def test_family_excluded_from_all_contacts(self, backend):
        family = make_node(
            PERSONA_ID,
            content="Family memory",
            disclosure_scope=DisclosureScope.FAMILY,
            embedding_content=[0.5] * 1536,
        )
        await backend.store_memory(family)

        results = await retrieve(
            backend,
            PERSONA_ID,
            QUERY_VEC,
            disclosure_tier=DisclosureScope.ALL_CONTACTS,
            config=RetrievalConfig(activation_threshold=0.0, seed_top_k=100, max_activated=100),
        )

        result_ids = {am.node.id for am in results}
        assert family.id not in result_ids


class TestInvDSCloseFriendsNotLeakedToAllContacts:
    """INV-DSc: CLOSE_FRIENDS memories never visible to ALL_CONTACTS."""

    async def test_close_friends_excluded_from_all_contacts(self, backend):
        cf = make_node(
            PERSONA_ID,
            content="Friend memory",
            disclosure_scope=DisclosureScope.CLOSE_FRIENDS,
            embedding_content=[0.5] * 1536,
        )
        await backend.store_memory(cf)

        results = await retrieve(
            backend,
            PERSONA_ID,
            QUERY_VEC,
            disclosure_tier=DisclosureScope.ALL_CONTACTS,
            config=RetrievalConfig(activation_threshold=0.0, seed_top_k=100, max_activated=100),
        )

        result_ids = {am.node.id for am in results}
        assert cf.id not in result_ids


class TestInvDSProgressiveNarrowing:
    """INV-DSd: Higher tiers see everything lower tiers see, plus more."""

    async def test_all_contacts_sees_all_contacts_scope(self, backend):
        mem = make_node(
            PERSONA_ID,
            content="Public memory",
            disclosure_scope=DisclosureScope.ALL_CONTACTS,
            embedding_content=[0.5] * 1536,
        )
        await backend.store_memory(mem)

        for tier in [
            DisclosureScope.ALL_CONTACTS,
            DisclosureScope.CLOSE_FRIENDS,
            DisclosureScope.FAMILY,
            DisclosureScope.PRIVATE,
        ]:
            results = await retrieve(
                backend,
                PERSONA_ID,
                QUERY_VEC,
                disclosure_tier=tier,
                config=RetrievalConfig(activation_threshold=0.0, seed_top_k=100, max_activated=100),
            )
            result_ids = {am.node.id for am in results}
            assert mem.id in result_ids, f"ALL_CONTACTS memory not visible to {tier.value}"

    async def test_exhaustive_no_leakage_across_all_scopes(self, backend):
        for _ in range(20):
            scope = DisclosureScope.PRIVATE
            mem = make_node(
                PERSONA_ID,
                content=f"Private mem {uuid4()}",
                disclosure_scope=scope,
                embedding_content=[0.5] * 1536,
            )
            await backend.store_memory(mem)

        results = await retrieve(
            backend,
            PERSONA_ID,
            QUERY_VEC,
            disclosure_tier=DisclosureScope.FAMILY,
            config=RetrievalConfig(activation_threshold=0.0, seed_top_k=100, max_activated=100),
        )

        for am in results:
            assert am.node.disclosure_scope != DisclosureScope.PRIVATE, (
                f"PRIVATE memory leaked to FAMILY tier: {am.node.content[:50]}"
            )

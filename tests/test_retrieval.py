from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

import pytest

from huible.memory.protocol import (
    ContentType,
    DisclosureScope,
    EdgeType,
    MemoryEdge,
    MemoryNode,
    MemoryTier,
    SearchResult,
)
from huible.memory.retrieval import (
    ActivatedMemory,
    ConversationTurn,
    RetrievalConfig,
    apply_motif_escalation,
    apply_suppression,
    cluster_by_theme,
    filter_by_disclosure,
    get_recently_activated,
    multi_vector_search,
    retrieve,
    spread_activation,
)


class FakeMemoryBackend:
    def __init__(self) -> None:
        self._memories: dict[MemoryNode, MemoryNode] = {}
        self._edges: list[MemoryEdge] = []
        self._content_index: list[tuple[list[float], list[MemoryNode]]] = []
        self._sensory_index: list[tuple[list[float], list[MemoryNode]]] = []
        self._affect_index: list[tuple[list[float], list[MemoryNode]]] = []

    def add_memory(self, node: MemoryNode) -> None:
        self._memories[node.id] = node

    def store_edge(self, edge: MemoryEdge) -> None:
        self._edges.append(edge)

    def index_content(self, embedding: list[float], nodes: list[MemoryNode]) -> None:
        self._content_index.append((embedding, nodes))

    def index_sensory(self, embedding: list[float], nodes: list[MemoryNode]) -> None:
        self._sensory_index.append((embedding, nodes))

    def index_affect(self, embedding: list[float], nodes: list[MemoryNode]) -> None:
        self._affect_index.append((embedding, nodes))

    async def store_memory(self, node: MemoryNode) -> Any:
        self._memories[node.id] = node
        return node.id

    async def get_memory(self, memory_id: Any) -> MemoryNode | None:
        return self._memories.get(memory_id)

    async def search_by_content(
        self,
        persona_id: Any,
        query_embedding: list[float],
        top_k: int = 20,
        disclosure_scope: DisclosureScope | None = None,
    ) -> list[SearchResult]:
        return await self._search_index(self._content_index, top_k)

    async def search_by_sensory(
        self,
        persona_id: Any,
        query_embedding: list[float],
        top_k: int = 20,
        disclosure_scope: DisclosureScope | None = None,
    ) -> list[SearchResult]:
        return await self._search_index(self._sensory_index, top_k)

    async def search_by_affect(
        self,
        persona_id: Any,
        query_embedding: list[float],
        top_k: int = 20,
        disclosure_scope: DisclosureScope | None = None,
    ) -> list[SearchResult]:
        return await self._search_index(self._affect_index, top_k)

    async def _search_index(
        self, index: list[tuple[list[float], list[MemoryNode]]], top_k: int
    ) -> list[SearchResult]:
        if not index:
            return []
        _embedding, nodes = index[0]
        results = [SearchResult(node=n, score=0.9) for n in nodes]
        return results[:top_k]

    async def get_edges(self, memory_id: Any) -> list[MemoryEdge]:
        return [e for e in self._edges if e.source_id == memory_id]

    async def add_edge(self, edge: MemoryEdge) -> Any:
        self._edges.append(edge)
        return edge.id

    async def supersede_memory(self, old_id: Any, new_node: MemoryNode) -> Any:
        old = self._memories.get(old_id)
        if old:
            self._memories[old_id] = MemoryNode(
                id=old.id,
                persona_id=old.persona_id,
                tier=old.tier,
                content=old.content,
                content_type=old.content_type,
                is_active=False,
            )
        self._memories[new_node.id] = new_node
        return new_node.id

    async def get_active_memories(self, persona_id: Any, limit: int = 50) -> list[MemoryNode]:
        active = [m for m in self._memories.values() if m.is_active]
        return active[:limit]

    async def quarantine_candidate(self, entry: Any) -> Any:
        return uuid4()


PERSONA_ID = uuid4()


def make_node(
    content: str = "test memory",
    content_type: ContentType = ContentType.NARRATIVE,
    disclosure: DisclosureScope = DisclosureScope.FAMILY,
) -> MemoryNode:
    return MemoryNode(
        id=uuid4(),
        persona_id=PERSONA_ID,
        tier=MemoryTier.ACCRUED,
        content=content,
        content_type=content_type,
        disclosure_scope=disclosure,
    )


def make_edge(source_id: Any, target_id: Any, weight: float = 1.0) -> MemoryEdge:
    return MemoryEdge(
        id=uuid4(),
        source_id=source_id,
        target_id=target_id,
        edge_type=EdgeType.THEMATIC,
        weight=weight,
    )


@pytest.fixture
def backend() -> FakeMemoryBackend:
    return FakeMemoryBackend()


class TestMultiVectorSearch:
    async def test_content_only_search(self, backend: FakeMemoryBackend) -> None:
        nodes = [make_node(f"memory-{i}") for i in range(5)]
        for n in nodes:
            backend.add_memory(n)
        backend.index_content([0.0] * 10, nodes)

        results = await multi_vector_search(backend, PERSONA_ID, [0.0] * 10, top_k=3)
        assert len(results) == 3
        assert all(isinstance(r, SearchResult) for r in results)

    async def test_multi_vector_merges_results(self, backend: FakeMemoryBackend) -> None:
        content_nodes = [make_node(f"content-{i}") for i in range(3)]
        sensory_nodes = [make_node(f"sensory-{i}") for i in range(3)]

        for n in content_nodes + sensory_nodes:
            backend.add_memory(n)
        backend.index_content([0.0] * 10, content_nodes)
        backend.index_sensory([0.0] * 10, sensory_nodes)

        results = await multi_vector_search(
            backend,
            PERSONA_ID,
            [0.0] * 10,
            query_embedding_sensory=[0.0] * 10,
            top_k=10,
        )
        assert len(results) == 6

    async def test_top_k_limits_results(self, backend: FakeMemoryBackend) -> None:
        nodes = [make_node(f"memory-{i}") for i in range(20)]
        for n in nodes:
            backend.add_memory(n)
        backend.index_content([0.0] * 10, nodes)

        results = await multi_vector_search(backend, PERSONA_ID, [0.0] * 10, top_k=5)
        assert len(results) == 5


class TestFeedbackSuppression:
    def test_get_recently_activated_basic(self) -> None:
        id1, id2, id3 = uuid4(), uuid4(), uuid4()
        history = [
            ConversationTurn(activated_memory_ids=[id1, id2]),
            ConversationTurn(activated_memory_ids=[id3]),
        ]
        recent = get_recently_activated(history, last_n_turns=10)
        assert recent == {id1, id2, id3}

    def test_get_recently_activated_respects_window(self) -> None:
        id1, id2 = uuid4(), uuid4()
        history = [
            ConversationTurn(activated_memory_ids=[id1]),
            ConversationTurn(activated_memory_ids=[id2]),
        ]
        recent = get_recently_activated(history, last_n_turns=1)
        assert recent == {id2}

    def test_get_recently_activated_empty_history(self) -> None:
        recent = get_recently_activated([], last_n_turns=10)
        assert recent == set()

    def test_apply_suppression_reduces_activation(self) -> None:
        id1, id2 = uuid4(), uuid4()
        activation_map: dict = {id1: 0.9, id2: 0.7}
        apply_suppression(activation_map, {id1}, suppression_factor=0.1)
        assert activation_map[id1] == pytest.approx(0.09)
        assert activation_map[id2] == 0.7

    def test_apply_suppression_nonexistent_id_noop(self) -> None:
        activation_map: dict = {uuid4(): 0.9}
        apply_suppression(activation_map, {uuid4()}, suppression_factor=0.1)
        assert next(iter(activation_map.values())) == 0.9

    def test_suppression_makes_same_query_different(self) -> None:
        id1, id2 = uuid4(), uuid4()
        query_activation = {id1: 0.8, id2: 0.7}
        first_results = dict(query_activation)
        apply_suppression(first_results, {id1}, suppression_factor=0.1)
        assert first_results[id1] != query_activation[id1]
        assert first_results[id2] == query_activation[id2]


class TestGraphTraversal:
    async def test_spread_activation_propagates(self, backend: FakeMemoryBackend) -> None:
        n1 = make_node("node 1")
        n2 = make_node("node 2")
        n3 = make_node("node 3")
        for n in (n1, n2, n3):
            backend.add_memory(n)
        backend.store_edge(make_edge(n1.id, n2.id, weight=0.8))
        backend.store_edge(make_edge(n2.id, n3.id, weight=0.9))

        config = RetrievalConfig(
            activation_threshold=0.1,
            decay_factor=0.6,
            max_spread_depth=2,
        )
        activation_map = {n1.id: 0.9}
        result = await spread_activation(backend, activation_map, config)

        assert n1.id in result
        assert n2.id in result
        assert result[n2.id] == pytest.approx(0.9 * 0.8 * 0.6, abs=1e-9)

    async def test_spread_respects_decay(self, backend: FakeMemoryBackend) -> None:
        n1 = make_node("node 1")
        n2 = make_node("node 2")
        for n in (n1, n2):
            backend.add_memory(n)
        backend.store_edge(make_edge(n1.id, n2.id, weight=1.0))

        config = RetrievalConfig(
            activation_threshold=0.1,
            decay_factor=0.5,
            max_spread_depth=1,
        )
        activation_map = {n1.id: 1.0}
        result = await spread_activation(backend, activation_map, config)
        assert result[n2.id] == pytest.approx(0.5)

    async def test_spread_stops_below_threshold(self, backend: FakeMemoryBackend) -> None:
        n1 = make_node("node 1")
        n2 = make_node("node 2")
        for n in (n1, n2):
            backend.add_memory(n)
        backend.store_edge(make_edge(n1.id, n2.id, weight=0.01))

        config = RetrievalConfig(
            activation_threshold=0.5,
            decay_factor=0.6,
            max_spread_depth=3,
        )
        activation_map = {n1.id: 0.5}
        result = await spread_activation(backend, activation_map, config)
        assert n2.id not in result or result[n2.id] < config.activation_threshold

    async def test_spread_traverses_edges_not_just_neighbors(
        self, backend: FakeMemoryBackend
    ) -> None:
        n1 = make_node("seed")
        n2 = make_node("one hop")
        n3 = make_node("two hops - never seed")
        for n in (n1, n2, n3):
            backend.add_memory(n)
        backend.store_edge(make_edge(n1.id, n2.id, weight=1.0))
        backend.store_edge(make_edge(n2.id, n3.id, weight=1.0))

        config = RetrievalConfig(
            activation_threshold=0.01,
            decay_factor=0.9,
            max_spread_depth=3,
        )
        activation_map = {n1.id: 1.0}
        result = await spread_activation(backend, activation_map, config)
        assert n2.id in result
        assert n3.id in result


class TestMotifDetection:
    def test_cluster_by_theme_groups_content_types(self) -> None:
        nodes: dict = {}
        narr1 = make_node("n1", ContentType.NARRATIVE)
        narr2 = make_node("n2", ContentType.NARRATIVE)
        fact1 = make_node("f1", ContentType.FACT)
        for n in (narr1, narr2, fact1):
            nodes[n.id] = n
        activations = {n.id: 0.8 for n in (narr1, narr2, fact1)}
        motifs = cluster_by_theme(activations, nodes, max_themes=5)
        narrative_group = [g for g in motifs if len(g) == 2]
        assert len(narrative_group) == 1
        fact_group = [g for g in motifs if len(g) == 1]
        assert len(fact_group) == 1

    def test_motif_escalation_boosts_groups_above_threshold(self) -> None:
        n1, n2, n3, n4 = (make_node(f"m{i}") for i in range(4))
        for n in (n2, n3, n4):
            n = MemoryNode(
                id=n.id,
                persona_id=n.persona_id,
                tier=n.tier,
                content=n.content,
                content_type=ContentType.FACT,
                disclosure_scope=n.disclosure_scope,
            )
        all_nodes = {n1.id: n1}
        for n in (n2, n3, n4):
            all_nodes[n.id] = MemoryNode(
                id=n.id,
                persona_id=n.persona_id,
                tier=n.tier,
                content=n.content,
                content_type=ContentType.FACT,
                disclosure_scope=n.disclosure_scope,
            )
        activation_map = {n.id: 0.5 for n in (n1, n2, n3, n4)}
        config = RetrievalConfig(
            motif_threshold=3,
            motif_boost_factor=1.3,
            motif_max_themes=5,
        )
        apply_motif_escalation(activation_map, all_nodes, config)
        boosted_count = sum(1 for v in activation_map.values() if v > 0.5)
        assert boosted_count >= 3


class TestDisclosureFiltering:
    def test_family_tier_excludes_private(self) -> None:
        n_private = make_node("private", disclosure=DisclosureScope.PRIVATE)
        n_family = make_node("family", disclosure=DisclosureScope.FAMILY)
        all_nodes = {n_private.id: n_private, n_family.id: n_family}
        eligible = filter_by_disclosure(
            {n_private.id, n_family.id}, DisclosureScope.FAMILY, all_nodes
        )
        assert n_private.id not in eligible
        assert n_family.id in eligible

    def test_all_contacts_includes_most_scopes(self) -> None:
        scopes = [
            DisclosureScope.PRIVATE,
            DisclosureScope.FAMILY,
            DisclosureScope.CLOSE_FRIENDS,
            DisclosureScope.ALL_CONTACTS,
        ]
        nodes = [make_node(f"mem-{s.value}", disclosure=s) for s in scopes]
        all_nodes = {n.id: n for n in nodes}
        eligible = filter_by_disclosure(
            set(all_nodes.keys()), DisclosureScope.CLOSE_FRIENDS, all_nodes
        )
        assert len(eligible) == 2
        assert not any(
            n.disclosure_scope == DisclosureScope.PRIVATE and n.id in eligible for n in nodes
        )
        assert not any(
            n.disclosure_scope == DisclosureScope.FAMILY and n.id in eligible for n in nodes
        )

    def test_family_tier_includes_most(self) -> None:
        scopes = [
            DisclosureScope.PRIVATE,
            DisclosureScope.FAMILY,
            DisclosureScope.CLOSE_FRIENDS,
            DisclosureScope.ALL_CONTACTS,
        ]
        nodes = [make_node(f"mem-{s.value}", disclosure=s) for s in scopes]
        all_nodes = {n.id: n for n in nodes}
        eligible = filter_by_disclosure(set(all_nodes.keys()), DisclosureScope.FAMILY, all_nodes)
        assert len(eligible) == 3

    def test_private_tier_includes_all(self) -> None:
        scopes = [
            DisclosureScope.PRIVATE,
            DisclosureScope.FAMILY,
            DisclosureScope.CLOSE_FRIENDS,
            DisclosureScope.ALL_CONTACTS,
        ]
        nodes = [make_node(f"mem-{s.value}", disclosure=s) for s in scopes]
        all_nodes = {n.id: n for n in nodes}
        eligible = filter_by_disclosure(set(all_nodes.keys()), DisclosureScope.PRIVATE, all_nodes)
        assert len(eligible) == 4


class TestRetrieveIntegration:
    async def test_retrieve_returns_activated_memories(self, backend: FakeMemoryBackend) -> None:
        nodes = [make_node(f"memory-{i}") for i in range(10)]
        for n in nodes:
            backend.add_memory(n)
        backend.index_content([0.0] * 10, nodes)

        results = await retrieve(
            backend,
            PERSONA_ID,
            [0.0] * 10,
            disclosure_tier=DisclosureScope.FAMILY,
        )
        assert len(results) > 0
        assert all(isinstance(r, ActivatedMemory) for r in results)
        assert results[0].activation >= results[-1].activation

    async def test_retrieve_applies_suppression(self, backend: FakeMemoryBackend) -> None:
        nodes = [make_node(f"memory-{i}") for i in range(10)]
        for n in nodes:
            backend.add_memory(n)
        backend.index_content([0.0] * 10, nodes)

        results_no_suppression = await retrieve(
            backend,
            PERSONA_ID,
            [0.0] * 10,
            disclosure_tier=DisclosureScope.FAMILY,
        )

        history = [ConversationTurn(activated_memory_ids=[results_no_suppression[0].node.id])]
        results_with_suppression = await retrieve(
            backend,
            PERSONA_ID,
            [0.0] * 10,
            conversation_history=history,
            disclosure_tier=DisclosureScope.FAMILY,
        )

        if results_with_suppression:
            suppressed = next(
                (
                    r
                    for r in results_with_suppression
                    if r.node.id == results_no_suppression[0].node.id
                ),
                None,
            )
            if suppressed:
                assert suppressed.activation < results_no_suppression[0].activation

    async def test_retrieve_traverses_edges(self, backend: FakeMemoryBackend) -> None:
        n1 = make_node("seed")
        n2 = make_node("neighbor")
        for n in (n1, n2):
            backend.add_memory(n)
        backend.index_content([0.0] * 10, [n1])
        backend.store_edge(make_edge(n1.id, n2.id, weight=1.0))

        config = RetrievalConfig(
            activation_threshold=0.01,
            max_spread_depth=2,
            decay_factor=0.9,
        )
        results = await retrieve(
            backend,
            PERSONA_ID,
            [0.0] * 10,
            config=config,
            disclosure_tier=DisclosureScope.FAMILY,
        )
        result_ids = {r.node.id for r in results}
        assert n2.id in result_ids

    async def test_retrieve_filters_disclosure(self, backend: FakeMemoryBackend) -> None:
        n_public = make_node("public", disclosure=DisclosureScope.ALL_CONTACTS)
        n_private = make_node("private", disclosure=DisclosureScope.PRIVATE)
        for n in (n_public, n_private):
            backend.add_memory(n)
        backend.index_content([0.0] * 10, [n_public, n_private])

        results = await retrieve(
            backend,
            PERSONA_ID,
            [0.0] * 10,
            disclosure_tier=DisclosureScope.CLOSE_FRIENDS,
        )
        result_ids = {r.node.id for r in results}
        assert n_private.id not in result_ids


class TestPerformance:
    async def test_algorithm_overhead_small(self) -> None:
        NUM_SEEDS = 20
        NUM_EDGE_TARGETS = 200

        backend = FakeMemoryBackend()
        seed_nodes = [make_node(f"memory-{i}") for i in range(NUM_SEEDS)]
        for n in seed_nodes:
            backend.add_memory(n)
        backend.index_content([0.0] * 10, seed_nodes)

        target_nodes = [make_node(f"target-{i}") for i in range(NUM_EDGE_TARGETS)]
        for n in target_nodes:
            backend.add_memory(n)

        for i, target in enumerate(target_nodes):
            source_node = seed_nodes[i % NUM_SEEDS]
            backend.store_edge(make_edge(source_node.id, target.id, weight=0.8))

        config = RetrievalConfig(
            activation_threshold=0.01,
            max_spread_depth=2,
            decay_factor=0.6,
            max_activated=50,
        )

        start = time.perf_counter()
        results = await retrieve(
            backend,
            PERSONA_ID,
            [0.0] * 10,
            config=config,
            disclosure_tier=DisclosureScope.FAMILY,
        )
        elapsed = time.perf_counter() - start

        assert elapsed < 0.2, f"Retrieval took {elapsed:.3f}s, expected < 0.2s"
        assert len(results) <= 50

    async def test_large_activation_map_computation(self) -> None:
        n1 = make_node("seed")
        backend = FakeMemoryBackend()
        backend.add_memory(n1)
        backend.index_content([0.0] * 10, [n1])

        targets = [make_node(f"t-{i}") for i in range(5000)]
        for t in targets:
            backend.add_memory(t)
            backend.store_edge(make_edge(n1.id, t.id, weight=0.9))

        config = RetrievalConfig(
            activation_threshold=0.01,
            max_spread_depth=1,
            decay_factor=0.9,
            max_activated=50,
        )

        activation_map: dict = {n1.id: 1.0}
        start = time.perf_counter()
        result = await spread_activation(backend, activation_map, config)
        elapsed = time.perf_counter() - start

        assert len(result) == 5001
        assert elapsed < 0.1, f"Spread took {elapsed:.3f}s for 5K edges"


class TestConfigDefaults:
    def test_default_config_matches_spec(self) -> None:
        config = RetrievalConfig()
        assert config.activation_threshold == 0.3
        assert config.max_activated == 50
        assert config.decay_factor == 0.6
        assert config.suppression_window == 10
        assert config.max_spread_depth == 3
        assert config.rrf_k == 60


class HybridFakeMemoryBackend(FakeMemoryBackend):
    """Fake backend with a scriptable lexical lane (HU-2309 W2)."""

    def __init__(self) -> None:
        super().__init__()
        self._lexical_index: list[MemoryNode] = []
        self.lexical_raises: Exception | None = None
        self.lexical_queries: list[str] = []

    def index_lexical(self, nodes: list[MemoryNode]) -> None:
        self._lexical_index = list(nodes)

    async def search_lexical(
        self,
        persona_id: Any,
        query: str,
        top_k: int = 20,
        disclosure_scope: DisclosureScope | None = None,
    ) -> list[SearchResult]:
        self.lexical_queries.append(query)
        if self.lexical_raises is not None:
            raise self.lexical_raises
        return [SearchResult(node=n, score=1.0) for n in self._lexical_index[:top_k]]


class TestHybridSeedSearch:
    """W2: lexical lane RRF-fused with the vector seed lanes."""

    async def test_lexical_only_match_rescues_exact_topic(self) -> None:
        # Vector lane: two conceptual matches. Lexical lane: the surname /
        # proper-noun memory the embedding lane missed entirely.
        vec_a, vec_b = make_node("janice explains boss"), make_node("coffee order")
        surname = make_node("My full name is Chandler Muriel Bing.")
        backend = HybridFakeMemoryBackend()
        backend.add_memory(vec_a)
        backend.add_memory(vec_b)
        backend.add_memory(surname)
        backend.index_content([0.0] * 10, [vec_a, vec_b])
        backend.index_lexical([surname])

        results = await multi_vector_search(
            backend,
            PERSONA_ID,
            [0.0] * 10,
            top_k=3,
            query_text="What is your last name?",
            lexical_floor=0.3,
        )

        by_id = {r.node.id: r for r in results}
        assert surname.id in by_id
        # Lexical-only seed enters at the floor, below real vector matches.
        assert by_id[surname.id].score == 0.3
        assert by_id[vec_a.id].score == 0.9
        assert backend.lexical_queries == ["What is your last name?"]

    async def test_overlap_doc_outranks_single_lane_docs(self) -> None:
        a, b, c = make_node("a"), make_node("b"), make_node("c")
        backend = HybridFakeMemoryBackend()
        for n in (a, b, c):
            backend.add_memory(n)
        backend.index_content([0.0] * 10, [a, b])
        backend.index_lexical([b, c])

        results = await multi_vector_search(
            backend,
            PERSONA_ID,
            [0.0] * 10,
            top_k=5,
            query_text="b",
            lexical_floor=0.3,
        )

        # b is in both lanes -> top; a and c tie at 1/61, a first-seen wins.
        assert [r.node.id for r in results] == [b.id, a.id, c.id]
        # Vector scores are never rewritten by the fusion.
        assert results[0].score == 0.9
        assert results[1].score == 0.9
        assert results[2].score == 0.3

    async def test_no_query_text_keeps_pure_vector_behavior(self) -> None:
        a, b = make_node("a"), make_node("b")
        backend = HybridFakeMemoryBackend()
        backend.index_content([0.0] * 10, [a, b])
        backend.index_lexical([b])

        results = await multi_vector_search(
            backend, PERSONA_ID, [0.0] * 10, top_k=5
        )

        assert [r.node.id for r in results] == [a.id, b.id]
        assert backend.lexical_queries == []

    async def test_whitespace_query_text_keeps_pure_vector_behavior(self) -> None:
        a = make_node("a")
        backend = HybridFakeMemoryBackend()
        backend.index_content([0.0] * 10, [a])

        results = await multi_vector_search(
            backend, PERSONA_ID, [0.0] * 10, top_k=5, query_text="   "
        )
        assert [r.node.id for r in results] == [a.id]
        assert backend.lexical_queries == []

    async def test_backend_without_lexical_capability_degrades(self) -> None:
        a, b = make_node("a"), make_node("b")
        backend = FakeMemoryBackend()  # no search_lexical method at all
        backend.index_content([0.0] * 10, [a, b])

        results = await multi_vector_search(
            backend, PERSONA_ID, [0.0] * 10, top_k=5, query_text="anything"
        )
        assert [r.node.id for r in results] == [a.id, b.id]

    async def test_unsupported_lexical_backend_degrades(self) -> None:
        from huible.memory.protocol import LexicalSearchUnsupported

        a, b = make_node("a"), make_node("b")
        backend = HybridFakeMemoryBackend()
        backend.lexical_raises = LexicalSearchUnsupported("sqlite has no fts")
        backend.index_content([0.0] * 10, [a, b])

        results = await multi_vector_search(
            backend, PERSONA_ID, [0.0] * 10, top_k=5, query_text="anything"
        )
        assert [r.node.id for r in results] == [a.id, b.id]

    async def test_top_k_truncates_fused_set(self) -> None:
        vec_nodes = [make_node(f"v-{i}") for i in range(5)]
        lexical_node = make_node("lexical only")
        backend = HybridFakeMemoryBackend()
        backend.index_content([0.0] * 10, vec_nodes)
        backend.index_lexical([lexical_node])

        results = await multi_vector_search(
            backend, PERSONA_ID, [0.0] * 10, top_k=3, query_text="q"
        )
        assert len(results) == 3

    async def test_retrieve_includes_lexical_only_seed_end_to_end(self) -> None:
        surname = make_node("My full name is Chandler Muriel Bing.")
        other = make_node("unrelated")
        backend = HybridFakeMemoryBackend()
        backend.add_memory(surname)
        backend.add_memory(other)
        backend.index_content([0.0] * 10, [other])
        backend.index_lexical([surname])

        results = await retrieve(
            backend,
            PERSONA_ID,
            [0.0] * 10,
            query_text="What is your last name?",
        )

        ids = {r.node.id for r in results}
        assert surname.id in ids
        # Floor == activation_threshold == the final inclusion gate.
        activation_by_id = {r.node.id: r.activation for r in results}
        assert activation_by_id[surname.id] == pytest.approx(0.3)

    async def test_retrieve_disclosure_filter_still_applies_to_lexical_seeds(
        self,
    ) -> None:
        secret = make_node(
            "My full name is Chandler Muriel Bing.",
            disclosure=DisclosureScope.PRIVATE,
        )
        backend = HybridFakeMemoryBackend()
        backend.add_memory(secret)
        backend.index_lexical([secret])

        results = await retrieve(
            backend,
            PERSONA_ID,
            [0.0] * 10,
            query_text="What is your last name?",
        )
        assert results == []

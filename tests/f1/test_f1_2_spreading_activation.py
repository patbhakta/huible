from __future__ import annotations

from uuid import uuid4

from huible.memory.protocol import DisclosureScope, EdgeType
from huible.memory.retrieval import RetrievalConfig, retrieve, spread_activation
from tests.f1.conftest import CosineFakeBackend


def _make_edge(source_id, target_id, edge_type, weight=1.0):
    return MemoryEdge(
        id=uuid4(),
        source_id=source_id,
        target_id=target_id,
        edge_type=edge_type,
        weight=weight,
    )


from huible.memory.protocol import MemoryEdge  # noqa: E402


class TestF1_2_SpreadingActivation:
    """F1.2: Spreading activation retrieval — must traverse edges, not just cosine."""

    async def test_retrieval_reaches_non_seed_nodes_via_edges(
        self, backend: CosineFakeBackend,
    ) -> None:
        seed_node = next(iter(backend._memories.values()))
        persona_id = seed_node.persona_id
        query_emb = seed_node.embedding_content or [0.0] * 1536

        edges_from_seed = [e for e in backend._edges if e.source_id == seed_node.id]
        if not edges_from_seed:
            for mem in list(backend._memories.values())[1:11]:
                e = _make_edge(seed_node.id, mem.id, EdgeType.THEMATIC)
                backend._edges.append(e)
                edges_from_seed.append(e)

        neighbor_ids = {e.target_id for e in edges_from_seed}

        config = RetrievalConfig(
            activation_threshold=0.01,
            max_spread_depth=3,
            decay_factor=0.9,
            seed_top_k=5,
            max_activated=50,
        )

        results = await retrieve(
            backend, persona_id, query_emb,
            disclosure_tier=DisclosureScope.PRIVATE,
            config=config,
        )

        result_ids = {r.node.id for r in results}
        reached_neighbors = result_ids & neighbor_ids
        assert len(reached_neighbors) > 0, (
            "Spreading activation did not reach any edge-connected neighbors"
        )

    async def test_spread_propagates_beyond_direct_seeds(
        self, backend: CosineFakeBackend,
    ) -> None:
        seed = next(iter(backend._memories.values()))

        activation_map = {seed.id: 1.0}
        config = RetrievalConfig(
            activation_threshold=0.01,
            max_spread_depth=3,
            decay_factor=0.8,
        )

        result = await spread_activation(backend, activation_map, config)

        direct_edges = [e for e in backend._edges if e.source_id == seed.id]
        direct_targets = {e.target_id for e in direct_edges}

        indirect_targets = set()
        for dt in direct_targets:
            for e in backend._edges:
                if e.source_id == dt and e.target_id != seed.id:
                    indirect_targets.add(e.target_id)

        non_seed_reached = set(result.keys()) - {seed.id}
        assert len(non_seed_reached) > 0, "Spread reached no nodes beyond seed"
        if indirect_targets:
            reached_indirect = non_seed_reached & indirect_targets
            assert len(reached_indirect) > 0, (
                "Spreading activation did not reach multi-hop neighbors"
            )

    async def test_not_pure_cosine_similarity(
        self, backend: CosineFakeBackend,
    ) -> None:
        seed = next(iter(backend._memories.values()))

        activation_map: dict = {seed.id: 0.9}
        config = RetrievalConfig(
            activation_threshold=0.01,
            max_spread_depth=3,
            decay_factor=0.9,
        )
        after_spread = await spread_activation(backend, activation_map, config)

        edge_reached = set(after_spread.keys()) - {seed.id}
        assert len(edge_reached) > 0, (
            "No nodes reached via edges — spreading activation is not working"
        )

    async def test_edge_traversal_depth(
        self, backend: CosineFakeBackend,
    ) -> None:
        seed = next(iter(backend._memories.values()))

        shallow_config = RetrievalConfig(
            activation_threshold=0.05, max_spread_depth=1, decay_factor=0.6,
        )
        deep_config = RetrievalConfig(
            activation_threshold=0.05, max_spread_depth=3, decay_factor=0.6,
        )

        shallow_map: dict = {seed.id: 1.0}
        deep_map: dict = {seed.id: 1.0}
        await spread_activation(backend, shallow_map, shallow_config)
        await spread_activation(backend, deep_map, deep_config)

        shallow_reached = len(shallow_map) - 1
        deep_reached = len(deep_map) - 1
        assert deep_reached >= shallow_reached, (
            f"Deeper spread ({deep_reached}) should reach >= shallow ({shallow_reached})"
        )

    async def test_edge_types_traversed(
        self, backend: CosineFakeBackend,
    ) -> None:
        seed = next(iter(backend._memories.values()))
        nodes = list(backend._memories.values())

        edge_types_seen: set[str] = set()
        for e in backend._edges:
            if e.source_id == seed.id:
                edge_types_seen.add(e.edge_type.value)

        if len(edge_types_seen) < 2:
            for i, target in enumerate(nodes[1:6]):
                et = list(EdgeType)[i % len(list(EdgeType))]
                backend._edges.append(
                    _make_edge(seed.id, target.id, et, weight=0.9)
                )
                edge_types_seen.add(et.value)

        activation_map: dict = {seed.id: 1.0}
        config = RetrievalConfig(
            activation_threshold=0.01, max_spread_depth=2, decay_factor=0.9,
        )
        await spread_activation(backend, activation_map, config)

        non_seed = set(activation_map.keys()) - {seed.id}
        assert len(non_seed) > 0, "No nodes reached through any edge type"

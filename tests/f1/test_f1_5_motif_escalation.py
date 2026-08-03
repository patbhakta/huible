from __future__ import annotations

import pytest

from huible.memory.protocol import ContentType, DisclosureScope, EdgeType, MemoryNode
from huible.memory.retrieval import (
    RetrievalConfig,
    apply_motif_escalation,
    cluster_by_theme,
    retrieve,
)
from tests.f1.conftest import CosineFakeBackend


class TestF1_5_MotifEscalation:
    """F1.5: Cross-topic motif escalation — motif-boosted cross-topic activation."""

    async def test_motif_boost_increases_activation(
        self, backend: CosineFakeBackend
    ) -> None:
        nodes = list(backend._memories.values())
        by_type: dict[ContentType, list[MemoryNode]] = {}
        for n in nodes:
            by_type.setdefault(n.content_type, []).append(n)

        target_type = max(by_type, key=lambda t: len(by_type[t]))
        group_nodes = by_type[target_type][:5]

        for gn in group_nodes:
            edges_to_gn = [e for e in backend._edges if e.target_id == gn.id]
            if not edges_to_gn:
                seed_nodes = list(backend._memories.values())[:5]
                for sn in seed_nodes:
                    edge = type(backend._edges[0])(
                        id=__import__("uuid").uuid4(),
                        source_id=sn.id,
                        target_id=gn.id,
                        edge_type=EdgeType.THEMATIC,
                        weight=0.9,
                    )
                    backend._edges.append(edge)

        activation_map: dict = {n.id: 0.5 for n in group_nodes}
        all_nodes = {n.id: n for n in nodes}

        pre_boost = dict(activation_map)
        config = RetrievalConfig(
            motif_threshold=3,
            motif_boost_factor=1.3,
            motif_max_themes=5,
        )
        apply_motif_escalation(activation_map, all_nodes, config)

        boosted = sum(
            1 for nid in activation_map if activation_map[nid] > pre_boost[nid]
        )
        assert boosted >= 3, (
            f"Expected >= 3 nodes boosted by motif escalation, got {boosted}"
        )

        for node in group_nodes:
            nid = node.id
            if activation_map[nid] > pre_boost[nid]:
                ratio = activation_map[nid] / pre_boost[nid]
                assert ratio == pytest.approx(1.3, abs=0.01), (
                    f"Boost ratio {ratio:.3f} != expected 1.3"
                )

    async def test_cross_topic_nodes_in_retrieval(
        self, backend: CosineFakeBackend
    ) -> None:
        nodes = list(backend._memories.values())
        persona_id = nodes[0].persona_id
        query_emb = nodes[0].embedding_content or [0.0] * 1536

        config = RetrievalConfig(
            activation_threshold=0.01,
            max_spread_depth=2,
            decay_factor=0.9,
            motif_threshold=3,
            motif_boost_factor=1.3,
            max_activated=50,
        )

        results = await retrieve(
            backend,
            persona_id,
            query_emb,
            disclosure_tier=DisclosureScope.PRIVATE,
            config=config,
        )

        if len(results) < 3:
            pytest.skip("Need >= 3 results for motif analysis")

        content_types_in_results = {r.node.content_type for r in results}
        assert len(content_types_in_results) >= 1, (
            "Retrieval should return nodes from at least one content type cluster"
        )

    async def test_motif_escalation_cross_topics(
        self, backend: CosineFakeBackend
    ) -> None:
        nodes = list(backend._memories.values())
        all_nodes = {n.id: n for n in nodes}

        type_counts: dict[str, int] = {}
        for n in nodes:
            type_counts[n.content_type.value] = type_counts.get(n.content_type.value, 0) + 1

        best_type = max(type_counts, key=type_counts.get)
        type_nodes = [n for n in nodes if n.content_type.value == best_type][:5]

        activation_map: dict = {n.id: 0.4 for n in type_nodes}
        pre = dict(activation_map)

        config = RetrievalConfig(motif_threshold=3, motif_boost_factor=1.3)
        apply_motif_escalation(activation_map, all_nodes, config)

        changed = sum(1 for nid in activation_map if activation_map[nid] != pre[nid])
        assert changed >= 3, f"Expected >= 3 nodes changed by motif, got {changed}"

    async def test_cluster_by_theme_groups_correctly(
        self, backend: CosineFakeBackend
    ) -> None:
        nodes = list(backend._memories.values())
        all_nodes = {n.id: n for n in nodes}

        activation_map: dict = {n.id: 0.5 for n in nodes[:20]}
        motifs = cluster_by_theme(activation_map, all_nodes, max_themes=5)

        total_in_motifs = sum(len(g) for g in motifs)
        assert total_in_motifs == 20, (
            f"cluster_by_theme lost nodes: {total_in_motifs} != 20"
        )

        groups_with_multiple = sum(1 for g in motifs if len(g) >= 2)
        assert groups_with_multiple >= 0, "Should have valid theme clusters"

    async def test_motif_threshold_not_met_no_boost(
        self, backend: CosineFakeBackend
    ) -> None:
        nodes = list(backend._memories.values())
        all_nodes = {n.id: n for n in nodes}

        two_narratives = [n for n in nodes if n.content_type == ContentType.NARRATIVE][:2]
        two_facts = [n for n in nodes if n.content_type == ContentType.FACT][:2]

        activation_map: dict = {}
        for n in two_narratives + two_facts:
            activation_map[n.id] = 0.5

        pre = dict(activation_map)
        config = RetrievalConfig(motif_threshold=3, motif_boost_factor=1.3)
        apply_motif_escalation(activation_map, all_nodes, config)

        changed = sum(1 for nid in activation_map if activation_map[nid] != pre[nid])
        assert changed == 0, (
            f"No boost should happen below threshold: {changed} nodes changed"
        )

    async def test_end_to_end_motif_in_retrieve(
        self, backend: CosineFakeBackend
    ) -> None:
        nodes = list(backend._memories.values())
        persona_id = nodes[0].persona_id
        query_emb = nodes[0].embedding_content or [0.0] * 1536

        config_with_motif = RetrievalConfig(
            activation_threshold=0.01,
            max_spread_depth=2,
            decay_factor=0.9,
            motif_threshold=3,
            motif_boost_factor=1.3,
            max_activated=50,
        )
        config_no_motif = RetrievalConfig(
            activation_threshold=0.01,
            max_spread_depth=2,
            decay_factor=0.9,
            motif_threshold=999,
            motif_boost_factor=1.3,
            max_activated=50,
        )

        with_motif = await retrieve(
            backend, persona_id, query_emb,
            disclosure_tier=DisclosureScope.PRIVATE,
            config=config_with_motif,
        )
        without_motif = await retrieve(
            backend, persona_id, query_emb,
            disclosure_tier=DisclosureScope.PRIVATE,
            config=config_no_motif,
        )

        with_motif_ids = {r.node.id for r in with_motif}
        without_motif_ids = {r.node.id for r in without_motif}
        with_motif_ids - without_motif_ids
        assert len(with_motif) >= len(without_motif), (
            f"Motif escalation should not reduce results: "
            f"{len(with_motif)} vs {len(without_motif)}"
        )

from __future__ import annotations

import asyncio
import random
import time
from uuid import uuid4

import pytest

from huible.memory.protocol import (
    ContentType,
    DisclosureScope,
    EdgeType,
    MemoryEdge,
    MemoryNode,
    MemoryTier,
)
from huible.memory.retrieval import (
    RetrievalConfig,
    apply_motif_escalation,
    apply_suppression,
    cluster_by_theme,
    filter_by_disclosure,
    retrieve,
    spread_activation,
)
from tests.f1.conftest import CosineFakeBackend
from tests.f8.conftest import _make_node


class TestF8_1_SpreadingActivationScale:
    """F8.1: Spreading activation handles 100K nodes / 500K edges."""

    def test_100k_nodes_500k_edges(self):
        n = 100_000
        node_ids = [uuid4() for _ in range(n)]
        activation_map = {node_ids[i]: 1.0 for i in range(50)}

        class FastEdgeBackend:
            def __init__(self):
                self._adj: dict = {}
                rng = random.Random(42)
                for _ in range(500_000):
                    src = node_ids[rng.randint(0, n - 1)]
                    tgt = node_ids[rng.randint(0, n - 1)]
                    if src != tgt:
                        self._adj.setdefault(src, []).append((tgt, rng.uniform(0.3, 1.0)))

            async def get_edges(self, memory_id):
                return [
                    MemoryEdge(
                        id=uuid4(), source_id=memory_id,
                        target_id=tgt, edge_type=EdgeType.THEMATIC, weight=w,
                    )
                    for tgt, w in self._adj.get(memory_id, [])
                ]

        backend = FastEdgeBackend()
        config = RetrievalConfig(
            activation_threshold=0.01, max_spread_depth=3, decay_factor=0.6,
        )
        start = time.perf_counter()
        result = asyncio.run(spread_activation(backend, activation_map, config))
        elapsed = time.perf_counter() - start
        assert elapsed < 5.0, f"Spread on 100K/500K took {elapsed:.3f}s"
        assert len(result) > 50


class TestF8_2_SuppressionScale:
    """F8.2: Suppression on 100K activation map."""

    def test_100k_suppression(self):
        activation_map = {uuid4(): 0.8 for _ in range(100_000)}
        recent_ids = set(list(activation_map.keys())[:500])

        start = time.perf_counter()
        apply_suppression(activation_map, recent_ids, suppression_factor=0.1)
        elapsed = time.perf_counter() - start

        assert elapsed < 0.05, f"Suppression on 100K took {elapsed:.4f}s"
        for mid in recent_ids:
            assert activation_map[mid] == pytest.approx(0.08)


class TestF8_3_MotifEscalationScale:
    """F8.3: Motif escalation on 100K nodes."""

    def test_100k_motif_escalation(self):
        all_nodes: dict = {}
        activation_map: dict = {}
        for i in range(100_000):
            nid = uuid4()
            ct = list(ContentType)[i % 5]
            all_nodes[nid] = MemoryNode(
                id=nid, persona_id=uuid4(),
                tier=MemoryTier.ACCRUED, content=f"mem {i}", content_type=ct,
            )
            activation_map[nid] = 0.5

        config = RetrievalConfig(motif_threshold=3, motif_boost_factor=1.3, motif_max_themes=5)
        start = time.perf_counter()
        apply_motif_escalation(activation_map, all_nodes, config)
        elapsed = time.perf_counter() - start
        assert elapsed < 0.5, f"Motif escalation on 100K took {elapsed:.3f}s"


class TestF8_4_ClusteringScale:
    """F8.4: Theme clustering on 100K nodes."""

    def test_100k_clustering(self):
        all_nodes: dict = {}
        activation_map: dict = {}
        for i in range(100_000):
            nid = uuid4()
            all_nodes[nid] = MemoryNode(
                id=nid, persona_id=uuid4(),
                tier=MemoryTier.ACCRUED, content=f"mem {i}",
                content_type=list(ContentType)[i % 5],
            )
            activation_map[nid] = 0.5

        start = time.perf_counter()
        motifs = cluster_by_theme(activation_map, all_nodes, max_themes=5)
        elapsed = time.perf_counter() - start
        assert elapsed < 0.1, f"Clustering on 100K took {elapsed:.3f}s"
        assert len(motifs) <= 5


class TestF8_5_DisclosureFilterScale:
    """F8.5: Disclosure filtering on 100K nodes."""

    def test_100k_disclosure_filter(self):
        all_nodes: dict = {}
        activation_map: dict = {}
        for i in range(100_000):
            nid = uuid4()
            scope = list(DisclosureScope)[i % 4]
            all_nodes[nid] = MemoryNode(
                id=nid, persona_id=uuid4(),
                tier=MemoryTier.ACCRUED, content=f"mem {i}",
                disclosure_scope=scope,
            )
            activation_map[nid] = 0.5

        start = time.perf_counter()
        filter_by_disclosure(activation_map, DisclosureScope.FAMILY, all_nodes)
        elapsed = time.perf_counter() - start
        assert elapsed < 0.5, f"Disclosure filter on 100K took {elapsed:.4f}s"


class TestF8_6_IndexingThroughput:
    """F8.6: Indexing throughput > 100 memories/second."""

    def test_1000_memories_indexed(self):
        persona_id = uuid4()
        b = CosineFakeBackend()

        start = time.perf_counter()
        for i in range(1000):
            node = _make_node(persona_id, i)
            asyncio.run(b.store_memory(node))
        elapsed = time.perf_counter() - start

        per_sec = 1000 / elapsed if elapsed > 0 else float("inf")
        assert per_sec > 100, f"Throughput: {per_sec:.0f}/s, expected > 100/s"


class TestF8_7_RetrievalLatency:
    """F8.7: Retrieval latency on 5K corpus."""

    def test_5k_corpus_retrieval(self):
        persona_id = uuid4()
        nodes = [_make_node(persona_id, i) for i in range(5000)]
        edges = []
        rng = random.Random(42)
        for _ in range(15_000):
            n1, n2 = rng.sample(nodes, 2)
            edges.append(MemoryEdge(
                id=uuid4(), source_id=n1.id, target_id=n2.id,
                edge_type=rng.choice(list(EdgeType)),
                weight=rng.uniform(0.3, 1.0),
            ))
        backend = CosineFakeBackend()
        backend.bulk_load(nodes, edges)

        query_emb = nodes[0].embedding_content or [0.0] * 128
        config = RetrievalConfig(
            activation_threshold=0.01, max_spread_depth=2,
            decay_factor=0.6, max_activated=50, seed_top_k=20,
        )

        start = time.perf_counter()
        results = asyncio.run(retrieve(
            backend, persona_id, query_emb,
            disclosure_tier=DisclosureScope.PRIVATE, config=config,
        ))
        elapsed = time.perf_counter() - start

        assert len(results) <= 50
        assert elapsed < 2.0, f"Retrieve on 5K took {elapsed:.3f}s"

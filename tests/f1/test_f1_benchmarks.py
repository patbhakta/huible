from __future__ import annotations

import asyncio
import hashlib
import math
import random
import time
from datetime import date
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
    retrieve,
    spread_activation,
)
from tests.f1.conftest import CosineFakeBackend


def _make_node(persona_id, idx, emb_dim=128) -> MemoryNode:
    h = hashlib.sha512(f"node-{idx}".encode()).digest()
    emb = []
    for i in range(emb_dim):
        chunk = h[i % len(h) : i % len(h) + 4]
        val = int.from_bytes(chunk, "big") / 0xFFFFFFFF
        emb.append(val * 2.0 - 1.0)
    norm = math.sqrt(sum(x * x for x in emb))
    emb = [x / norm for x in emb]

    return MemoryNode(
        id=uuid4(),
        persona_id=persona_id,
        tier=MemoryTier.ACCRUED,
        content=f"memory {idx}",
        content_type=list(ContentType)[idx % 5],
        embedding_content=emb,
        embedding_sensory=emb[:],
        embedding_affect=emb[:64],
        memory_date=date(2000, 1, 1),
        disclosure_scope=list(DisclosureScope)[idx % 4],
        metadata={"topic": f"topic_{idx % 20}"},
    )


class TestAlgorithmBenchmarks:
    """Benchmarks for pure algorithm performance (no backend I/O)."""

    def test_spread_activation_100k_nodes_500k_edges(self) -> None:
        n = 100_000
        random.Random(42)
        node_ids = [uuid4() for _ in range(n)]

        activation_map: dict = {}
        for i in range(50):
            activation_map[node_ids[i]] = 1.0

        class FastEdgeBackend:
            def __init__(self, n_edges):
                self._adj: dict = {}
                rng2 = random.Random(42)
                for _ in range(n_edges):
                    src = node_ids[rng2.randint(0, n - 1)]
                    tgt = node_ids[rng2.randint(0, n - 1)]
                    if src != tgt:
                        self._adj.setdefault(src, []).append((tgt, rng2.uniform(0.3, 1.0)))

            async def get_edges(self, memory_id):
                return [
                    MemoryEdge(
                        id=uuid4(), source_id=memory_id,
                        target_id=tgt, edge_type=EdgeType.THEMATIC, weight=w,
                    )
                    for tgt, w in self._adj.get(memory_id, [])
                ]

        backend = FastEdgeBackend(500_000)

        config = RetrievalConfig(
            activation_threshold=0.01,
            max_spread_depth=3,
            decay_factor=0.6,
        )

        start = time.perf_counter()
        result = asyncio.run(spread_activation(backend, activation_map, config))
        elapsed = time.perf_counter() - start

        assert elapsed < 5.0, f"Spread on 100K/500K took {elapsed:.3f}s, expected < 5.0s"
        assert len(result) > 50

    def test_suppression_100k_map(self) -> None:
        activation_map: dict = {uuid4(): 0.8 for _ in range(100_000)}
        recent_ids = set(list(activation_map.keys())[:500])

        start = time.perf_counter()
        apply_suppression(activation_map, recent_ids, suppression_factor=0.1)
        elapsed = time.perf_counter() - start

        assert elapsed < 0.05, f"Suppression on 100K took {elapsed:.4f}s, expected < 0.05s"
        for mid in recent_ids:
            assert activation_map[mid] == pytest.approx(0.08)

    def test_motif_escalation_100k_map(self) -> None:
        random.Random(42)
        all_nodes: dict = {}
        activation_map: dict = {}
        for i in range(100_000):
            nid = uuid4()
            ct = list(ContentType)[i % 5]
            node = MemoryNode(
                id=nid,
                persona_id=uuid4(),
                tier=MemoryTier.ACCRUED,
                content=f"mem {i}",
                content_type=ct,
            )
            all_nodes[nid] = node
            activation_map[nid] = 0.5

        config = RetrievalConfig(
            motif_threshold=3,
            motif_boost_factor=1.3,
            motif_max_themes=5,
        )

        start = time.perf_counter()
        apply_motif_escalation(activation_map, all_nodes, config)
        elapsed = time.perf_counter() - start

        assert elapsed < 0.5, f"Motif escalation on 100K took {elapsed:.3f}s, expected < 0.5s"

    def test_cluster_by_theme_100k(self) -> None:
        all_nodes: dict = {}
        activation_map: dict = {}
        for i in range(100_000):
            nid = uuid4()
            ct = list(ContentType)[i % 5]
            all_nodes[nid] = MemoryNode(
                id=nid,
                persona_id=uuid4(),
                tier=MemoryTier.ACCRUED,
                content=f"mem {i}",
                content_type=ct,
            )
            activation_map[nid] = 0.5

        start = time.perf_counter()
        motifs = cluster_by_theme(activation_map, all_nodes, max_themes=5)
        elapsed = time.perf_counter() - start

        assert elapsed < 0.1, f"Clustering on 100K took {elapsed:.3f}s, expected < 0.1s"
        assert len(motifs) <= 5

    def test_disclosure_filter_100k(self) -> None:
        from huible.memory.retrieval import filter_by_disclosure

        all_nodes: dict = {}
        activation_map: dict = {}
        for i in range(100_000):
            nid = uuid4()
            scope = list(DisclosureScope)[i % 4]
            all_nodes[nid] = MemoryNode(
                id=nid,
                persona_id=uuid4(),
                tier=MemoryTier.ACCRUED,
                content=f"mem {i}",
                disclosure_scope=scope,
            )
            activation_map[nid] = 0.5

        start = time.perf_counter()
        filter_by_disclosure(activation_map, DisclosureScope.FAMILY, all_nodes)
        elapsed = time.perf_counter() - start

        assert elapsed < 0.5, f"Disclosure filter on 100K took {elapsed:.4f}s, expected < 0.5s"


class TestInMemoryRetrievalBenchmarks:
    """Benchmarks for in-memory backend retrieval at realistic test scale.

    Note: The spec target of 50-node activation in < 200ms on 100K corpus
    requires PostgreSQL + pgvector HNSW indexes. These benchmarks validate
    the algorithm pipeline at in-memory scale. The 100K+pgvector benchmark
    should be run against a real database instance.
    """

    def test_retrieve_5k_corpus(self) -> None:
        backend, persona_id, nodes = _build_backend(5_000, 15_000)
        query_emb = nodes[0].embedding_content or [0.0] * 128

        config = RetrievalConfig(
            activation_threshold=0.01,
            max_spread_depth=2,
            decay_factor=0.6,
            max_activated=50,
            seed_top_k=20,
        )

        start = time.perf_counter()
        results = asyncio.run(retrieve(
            backend, persona_id, query_emb,
            disclosure_tier=DisclosureScope.PRIVATE,
            config=config,
        ))
        elapsed = time.perf_counter() - start

        assert len(results) <= 50
        assert elapsed < 2.0, f"Retrieve on 5K in-memory took {elapsed:.3f}s"

    def test_indexing_throughput(self) -> None:
        persona_id = uuid4()
        b = CosineFakeBackend()

        start = time.perf_counter()
        for i in range(1000):
            node = _make_node(persona_id, i)
            asyncio.run(b.store_memory(node))
        elapsed = time.perf_counter() - start

        per_sec = 1000 / elapsed if elapsed > 0 else float("inf")
        assert per_sec > 100, f"Indexing throughput: {per_sec:.0f}/s, expected > 100/s"


def _build_backend(n_nodes: int, n_edges: int) -> tuple[CosineFakeBackend, uuid4, list]:
    rng = random.Random(42)
    persona_id = uuid4()
    nodes = [_make_node(persona_id, i) for i in range(n_nodes)]

    edges = []
    for _ in range(n_edges):
        n1, n2 = rng.sample(nodes, 2)
        edges.append(MemoryEdge(
            id=uuid4(),
            source_id=n1.id,
            target_id=n2.id,
            edge_type=rng.choice(list(EdgeType)),
            weight=rng.uniform(0.3, 1.0),
        ))

    backend = CosineFakeBackend()
    backend.bulk_load(nodes, edges)
    return backend, persona_id, nodes

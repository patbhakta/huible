from __future__ import annotations

from tests.f1.conftest import CosineFakeBackend
from tests.f1.corpus import SyntheticCorpus


class TestF1_1_Indexing:
    """F1.1: Index a corpus of memories with multi-vector embeddings (1000+ memories)."""

    def test_corpus_size_meets_minimum(self, corpus: SyntheticCorpus) -> None:
        assert len(corpus.memories) >= 1000, (
            f"Corpus has {len(corpus.memories)} memories, need >= 1000"
        )

    def test_all_memories_stored(self, backend: CosineFakeBackend) -> None:
        assert backend.count_memories() >= 1000

    def test_all_tiers_represented(self, backend: CosineFakeBackend) -> None:
        tiers = backend.count_by_tier()
        for tier in ["canonical", "derived", "accrued", "world"]:
            assert tiers.get(tier, 0) > 0, f"Tier '{tier}' has 0 memories"

    def test_all_disclosure_scopes_represented(self, backend: CosineFakeBackend) -> None:
        scopes = backend.count_by_scope()
        for scope in ["private", "family", "close_friends", "all_contacts"]:
            assert scopes.get(scope, 0) > 0, f"Scope '{scope}' has 0 memories"

    def test_all_content_types_represented(self, backend: CosineFakeBackend) -> None:
        types = backend.count_by_type()
        for ct in ["narrative", "fact", "sensory", "relationship", "preference"]:
            assert types.get(ct, 0) > 0, f"Content type '{ct}' has 0 memories"

    def test_multi_vector_embeddings_present(self, backend: CosineFakeBackend) -> None:
        assert backend.has_multi_vector_embeddings(), (
            "No memories have all three embedding vectors (content, sensory, affect)"
        )

    def test_edges_created(self, corpus: SyntheticCorpus) -> None:
        assert len(corpus.edges) >= 2000, (
            f"Corpus has {len(corpus.edges)} edges, need >= 2000"
        )

    def test_bulk_load_preserves_count(self, corpus: SyntheticCorpus) -> None:
        fresh = CosineFakeBackend()
        fresh.bulk_load(corpus.memories, corpus.edges)
        assert fresh.count_memories() == len(corpus.memories)
        assert fresh.count_edges() == len(corpus.edges)

    def test_memories_retrievable_after_indexing(
        self, corpus: SyntheticCorpus, backend: CosineFakeBackend
    ) -> None:
        import asyncio

        async def _check() -> None:
            for mem in corpus.memories[:50]:
                found = await backend.get_memory(mem.id)
                assert found is not None
                assert found.id == mem.id
                assert found.content == mem.content

        asyncio.run(_check())

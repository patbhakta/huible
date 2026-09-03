"""W1 embeddings provider layer tests (HU-2309 v1.8 M-0R-A).

Covers the provider registry, the legacy byte-compat lane, provider routing of
the API query embedder, and the trace-score passthrough contract. The real
ONNX model path is an opt-in integration test (downloads bge-small-en-v1.5 on
first run): set ``RUN_LOCAL_ONNX_TESTS=1`` with ``fastembed`` installed.
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

from huible.api.settings import Settings
from huible.conversation import simple_embedding
from huible.embeddings import (
    DEFAULT_EMBEDDINGS_MODEL,
    LEGACY_DIM,
    LOCAL_ONNX_DIM,
    LegacyTokenHashEmbedder,
    LocalOnnxEmbedder,
    build_embedder,
    provider_dim,
    reset_embedder_cache,
)
from huible.memory.protocol import MemoryNode

# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------


def test_provider_dim_mapping():
    assert provider_dim("local_onnx") == LOCAL_ONNX_DIM
    assert provider_dim("legacy") == LEGACY_DIM
    assert provider_dim("fake") == LEGACY_DIM


def test_legacy_embedder_matches_simple_embedding_byte_for_byte():
    emb = LegacyTokenHashEmbedder()
    assert emb.dim == LEGACY_DIM
    expected = simple_embedding("Joey doesn't share food!", dim=LEGACY_DIM)
    assert emb.embed_passage(["Joey doesn't share food!"])[0] == expected
    assert emb.embed_query(["Joey doesn't share food!"])[0] == expected


def test_legacy_embedder_is_deterministic():
    a = build_embedder("legacy").embed_query(["determinism probe"])
    b = build_embedder("legacy").embed_query(["determinism probe"])
    assert a == b


def test_unknown_provider_raises():
    with pytest.raises(ValueError, match="unknown EMBEDDING_PROVIDER"):
        build_embedder("openai")


def test_settings_blank_provider_normalizes_to_fake():
    assert Settings(embedding_provider="").embedding_provider == "fake"


def test_settings_schema_dim_follows_provider():
    assert Settings(embedding_provider="fake").embedding_schema_dim == LEGACY_DIM
    assert Settings(embedding_provider="legacy").embedding_schema_dim == LEGACY_DIM
    assert Settings(embedding_provider="local_onnx").embedding_schema_dim == LOCAL_ONNX_DIM


# ---------------------------------------------------------------------------
# API query-embedder routing (monkeypatched settings; no model needed)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_embedder_cache()
    yield
    reset_embedder_cache()


def test_embed_query_text_routes_legacy(monkeypatch):
    import huible.embeddings as em

    monkeypatch.setattr(
        em, "get_settings", lambda: Settings(embedding_provider="legacy")
    )
    vec = em.embed_query_text("routing probe")
    assert vec == simple_embedding("routing probe", dim=LEGACY_DIM)


def test_embed_query_text_routes_local_onnx(monkeypatch):
    import huible.embeddings as em

    class _FakeOnnx:
        def __init__(self) -> None:
            self.dim = LOCAL_ONNX_DIM
            self.queries: list[str] = []

        def embed_passage(self, texts):
            return [[0.5] * LOCAL_ONNX_DIM for _ in texts]

        def embed_query(self, texts):
            self.queries.extend(texts)
            return [[1.0] * LOCAL_ONNX_DIM for _ in texts]

    fake = _FakeOnnx()
    monkeypatch.setattr(
        em, "get_settings",
        lambda: Settings(
            embedding_provider="local_onnx", embeddings_model=DEFAULT_EMBEDDINGS_MODEL
        ),
    )
    monkeypatch.setattr(
        em, "build_embedder", lambda provider, model: fake
    )
    vec = em.embed_query_text("query path")
    assert len(vec) == LOCAL_ONNX_DIM
    assert fake.queries == ["query path"]
    # passage path also lands on the same provider instance
    pvec = em.embed_passage_text("stored memory")
    assert all(x == 0.5 for x in pvec)


# ---------------------------------------------------------------------------
# Real ONNX lane (opt-in integration: needs fastembed + first-run download)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("RUN_LOCAL_ONNX_TESTS") != "1",
    reason="set RUN_LOCAL_ONNX_TESTS=1 with fastembed installed to run the model",
)
def test_local_onnx_embedder_real_model():
    emb = LocalOnnxEmbedder()
    assert emb.dim == LOCAL_ONNX_DIM
    q1 = emb.embed_query(["What does Joey love to eat?"])[0]
    q2 = emb.embed_query(["What does Joey love to eat?"])[0]
    p = emb.embed_passage(["Joey loves meatball subs."])[0]
    assert len(q1) == LOCAL_ONNX_DIM and len(p) == LOCAL_ONNX_DIM
    assert q1 == q2  # deterministic
    # asymmetric prefix: query/passage vectors differ for the same text
    assert q1 != p


# ---------------------------------------------------------------------------
# Trace-score passthrough (M-0R-A observability prerequisite)
# ---------------------------------------------------------------------------


def _node(content: str = "mob soccer story") -> MemoryNode:
    return MemoryNode(id=uuid4(), persona_id=uuid4(), tier="accrued", content=content)


def test_view_reports_real_activation_score():
    from huible.api.app import _view

    node = _node()
    view = _view(node, {node.id: 0.87})
    assert view.activation_score == pytest.approx(0.87)


def test_view_defaults_to_zero_without_score():
    from huible.api.app import _view

    node = _node()
    assert _view(node).activation_score == 0.0
    assert _view(node, {}).activation_score == 0.0

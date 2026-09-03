"""Embeddings provider layer (W1, HU-2309 v1.8 §1.7.2 / M-0R-A).

Greenfield client that makes the previously dead ``EMBEDDING_PROVIDER``
setting live. The chat retrieval path (:func:`huible.api.app._embed`) and the
provisioning script both route through :func:`embed_query_text` /
:class:`Embedder` so query vectors, stored vectors, and the schema dim always
share one contract (the HU-1435 dim-skip guard stays as the loud safety net).

Providers
---------
``local_onnx``
    Local CPU ONNX ``BAAI/bge-small-en-v1.5`` (384-dim) via ``fastembed``.
    Zero marginal cost, offline after the one-time model download,
    deterministic, and decoupled from the chat-token budget (TL pick,
    R&D option (a)).
``legacy`` / ``fake``
    The Stage-1 token-hash embedding (:func:`huible.conversation.simple_embedding`)
    emitted at the legacy schema dim (1536). Byte-identical to the pre-W1
    ``_embed`` behavior so existing deployments keep working until the
    one-window cutover (schema migration + full re-embed) runs.

The dimension contract is central: mixing a query dim with a different
stored-vector column dim silently empties retrieval (HU-1435/RC-2), so the
provider reports its dim and every write/query path derives lengths from the
same source of truth.
"""

from __future__ import annotations

import logging
import threading
from typing import Protocol

from huible.api.settings import get_settings
from huible.conversation import simple_embedding

logger = logging.getLogger(__name__)

#: Published model for the local ONNX lane (TL pick, 2026-08-31).
DEFAULT_EMBEDDINGS_MODEL = "BAAI/bge-small-en-v1.5"
#: bge-small-en-v1.5 output dimension.
LOCAL_ONNX_DIM = 384
#: Pre-W1 schema dim (``memories.embedding_content`` / ``embedding_sensory``).
LEGACY_DIM = 1536

PROVIDER_LOCAL_ONNX = "local_onnx"
PROVIDER_LEGACY = "legacy"
#: Historic spelling kept as an alias; blank env already normalizes to it.
PROVIDER_FAKE = "fake"

_EMBEDDING_PROVIDERS = frozenset({PROVIDER_LOCAL_ONNX, PROVIDER_LEGACY, PROVIDER_FAKE})


class Embedder(Protocol):
    """Minimal contract shared by every provider."""

    dim: int

    def embed_passage(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, texts: list[str]) -> list[list[float]]: ...


class LocalOnnxEmbedder:
    """CPU ONNX embedder (bge-small-en-v1.5, 384-dim) backed by ``fastembed``.

    Requires the ``fastembed`` package (a core dependency since W1). The model
    downloads once into the fastembed cache; after that the embedder is fully
    offline. ``bge-small-en-v1.5`` is an asymmetric retrieval model, so queries
    go through ``query_embed`` (which applies the model's query prefix) and
    stored memories through plain ``embed``.
    """

    def __init__(self, model_name: str = DEFAULT_EMBEDDINGS_MODEL) -> None:
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:  # pragma: no cover - environment guard
            raise RuntimeError(
                "EMBEDDING_PROVIDER=local_onnx requires the 'fastembed' package; "
                "install it (pip install fastembed) or fall back to "
                "EMBEDDING_PROVIDER=legacy."
            ) from exc
        self._model_name = model_name
        self._model = TextEmbedding(model_name=model_name)
        self.dim = LOCAL_ONNX_DIM

    def embed_passage(self, texts: list[str]) -> list[list[float]]:
        return [list(vec) for vec in self._model.embed(texts)]

    def embed_query(self, texts: list[str]) -> list[list[float]]:
        return [list(vec) for vec in self._model.query_embed(texts)]


class LegacyTokenHashEmbedder:
    """Stage-1 token-hash embedder pinned to the legacy schema dim (1536).

    Wrap of :func:`huible.conversation.simple_embedding` at dim 1536 — the
    exact semantics of the pre-W1 ``app._embed`` so deployments that have not
    run the 384-dim cutover keep byte-identical vectors. Queries and passages
    use the same symmetric hash (that is the legacy behavior).
    """

    def __init__(self, dim: int = LEGACY_DIM) -> None:
        self.dim = dim

    def embed_passage(self, texts: list[str]) -> list[list[float]]:
        return [simple_embedding(t, dim=self.dim) for t in texts]

    def embed_query(self, texts: list[str]) -> list[list[float]]:
        return [simple_embedding(t, dim=self.dim) for t in texts]


def provider_dim(provider: str) -> int:
    """Schema dim implied by a provider value."""
    return LOCAL_ONNX_DIM if provider == PROVIDER_LOCAL_ONNX else LEGACY_DIM


def build_embedder(provider: str, model_name: str = DEFAULT_EMBEDDINGS_MODEL) -> Embedder:
    """Construct the embedder for a provider value (no caching)."""
    if provider == PROVIDER_LOCAL_ONNX:
        return LocalOnnxEmbedder(model_name=model_name)
    if provider in (PROVIDER_LEGACY, PROVIDER_FAKE):
        return LegacyTokenHashEmbedder()
    raise ValueError(
        f"unknown EMBEDDING_PROVIDER {provider!r}; expected one of {sorted(_EMBEDDING_PROVIDERS)}"
    )


_embedder_lock = threading.Lock()
_embedder: Embedder | None = None
_embedder_key: tuple[str, str] | None = None


def get_embedder() -> Embedder:
    """Process-cached embedder bound to the current settings.

    Settings are process-cached themselves (a provider flip requires a
    container restart), so a single cached instance keyed on
    ``(provider, model)`` is consistent with that contract.
    """
    global _embedder, _embedder_key
    settings = get_settings()
    key = (settings.embedding_provider, settings.embeddings_model)
    if _embedder is None or _embedder_key != key:
        with _embedder_lock:
            if _embedder is None or _embedder_key != key:
                _embedder = build_embedder(*key)
                _embedder_key = key
                logger.info(
                    "embeddings provider wired: %s model=%s dim=%d",
                    settings.embedding_provider,
                    settings.embeddings_model,
                    _embedder.dim,
                )
    return _embedder


def reset_embedder_cache() -> None:
    """Drop the process-cached embedder (test hook for provider flips)."""
    global _embedder, _embedder_key
    with _embedder_lock:
        _embedder = None
        _embedder_key = None


def embed_query_text(text: str) -> list[float]:
    """Embed one retrieval query via the active provider (W1 query path)."""
    return get_embedder().embed_query([text])[0]


def embed_passage_text(text: str) -> list[float]:
    """Embed one stored-memory text via the active provider (W1 write path)."""
    return get_embedder().embed_passage([text])[0]

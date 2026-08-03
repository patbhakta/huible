from __future__ import annotations

import hashlib
import logging
import struct
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

EmbeddingFn = Callable[..., Coroutine[Any, Any, list[float]]]


@dataclass(slots=True)
class EmbeddingConfig:
    content_dim: int = 1536
    sensory_dim: int = 1536
    affect_dim: int = 512

    sensory_prefix: str = "sensory: "
    affect_prefix: str = "affect: "


@dataclass(slots=True)
class MultiVectorEmbeddings:
    content: list[float] = field(default_factory=list)
    sensory: list[float] = field(default_factory=list)
    affect: list[float] = field(default_factory=list)


class Embedder:
    def __init__(
        self,
        embedding_fn: EmbeddingFn | None = None,
        config: EmbeddingConfig | None = None,
    ) -> None:
        self._embedding_fn = embedding_fn
        self._config = config or EmbeddingConfig()

    async def embed(
        self,
        text: str,
        sensory_cues: list[str] | None = None,
        affect_signals: list[str] | None = None,
    ) -> MultiVectorEmbeddings:
        content_embedding = await self._get_content_embedding(text)

        sensory_embedding: list[float] = []
        if sensory_cues:
            sensory_embedding = await self._get_sensory_embedding(text, sensory_cues)
        elif self._config.sensory_dim > 0:
            sensory_embedding = self._deterministic_fallback(
                text, self._config.sensory_prefix, self._config.sensory_dim
            )

        affect_embedding: list[float] = []
        if affect_signals:
            affect_embedding = await self._get_affect_embedding(text, affect_signals)
        elif self._config.affect_dim > 0:
            affect_embedding = self._deterministic_fallback(
                text, self._config.affect_prefix, self._config.affect_dim
            )

        return MultiVectorEmbeddings(
            content=content_embedding,
            sensory=sensory_embedding,
            affect=affect_embedding,
        )

    async def embed_batch(
        self,
        texts: list[str],
        sensory_cues_list: list[list[str] | None] | None = None,
        affect_signals_list: list[list[str] | None] | None = None,
    ) -> list[MultiVectorEmbeddings]:
        results: list[MultiVectorEmbeddings] = []
        for i, text in enumerate(texts):
            sensory = None
            if sensory_cues_list and i < len(sensory_cues_list):
                sensory = sensory_cues_list[i]
            affect = None
            if affect_signals_list and i < len(affect_signals_list):
                affect = affect_signals_list[i]
            result = await self.embed(text, sensory, affect)
            results.append(result)
        return results

    async def _get_content_embedding(self, text: str) -> list[float]:
        if self._embedding_fn is not None:
            try:
                return await self._embedding_fn(text)
            except Exception:
                logger.warning(
                    "Embedding fn failed for content, using fallback", exc_info=True,
                )
        return self._deterministic_fallback(text, "", self._config.content_dim)

    async def _get_sensory_embedding(
        self, text: str, sensory_cues: list[str]
    ) -> list[float]:
        if self._embedding_fn is not None:
            try:
                combined = self._config.sensory_prefix + "; ".join(sensory_cues)
                return await self._embedding_fn(combined)
            except Exception:
                logger.warning(
                    "Embedding fn failed for sensory, using fallback", exc_info=True,
                )
        return self._deterministic_fallback(
            text, self._config.sensory_prefix, self._config.sensory_dim
        )

    async def _get_affect_embedding(
        self, text: str, affect_signals: list[str]
    ) -> list[float]:
        if self._embedding_fn is not None:
            try:
                combined = self._config.affect_prefix + "; ".join(affect_signals)
                return await self._embedding_fn(combined)
            except Exception:
                logger.warning(
                    "Embedding fn failed for affect, using fallback", exc_info=True,
                )
        return self._deterministic_fallback(
            text, self._config.affect_prefix, self._config.affect_dim
        )

    @staticmethod
    def _deterministic_fallback(text: str, prefix: str, dim: int) -> list[float]:
        key = (prefix + text).encode("utf-8")
        h = hashlib.sha512(key).digest()
        full_hash = h + hashlib.sha512(h + key).digest()
        n_blocks = (dim + 15) // 16
        embedding: list[float] = []
        for i in range(n_blocks):
            block = full_hash[(i * 8) % len(full_hash):(i * 8 + 8) % len(full_hash)]
            if len(block) < 8:
                block = block + full_hash[:8 - len(block)]
            val = struct.unpack("<d", block)[0]
            embedding.append(val)
        while len(embedding) < dim:
            embedding.extend(embedding[:dim - len(embedding)])
        return embedding[:dim]

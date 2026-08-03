from __future__ import annotations

from collections.abc import Callable, Coroutine
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from huible.ingestion.batch import BatchResult, IngestionWorker
    from huible.ingestion.embedder import Embedder, EmbeddingConfig, MultiVectorEmbeddings
    from huible.ingestion.extractor import (
        ConversationTurn,
        Extractor,
        MemoryCandidate,
    )
    from huible.ingestion.writer import MemoryWriter, WriteResult

Tier2Model = Callable[..., Coroutine[Any, Any, dict]]


class GateOutcome(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    AMBIGUOUS = "ambiguous"


@runtime_checkable
class Gate(Protocol):
    name: str

    async def evaluate(self, candidate: dict, context: GateContext) -> GateResult: ...


class GateResult:
    __slots__ = ("details", "outcome", "reason", "score")

    def __init__(
        self,
        outcome: GateOutcome,
        reason: str = "",
        score: float = 0.0,
        details: dict | None = None,
    ) -> None:
        self.outcome = outcome
        self.reason = reason
        self.score = score
        self.details = details or {}

    def __repr__(self) -> str:
        return f"GateResult(outcome={self.outcome.value!r}, reason={self.reason!r})"


class GateContext:
    __slots__ = ("backend", "config", "persona_id", "tier2_model")

    def __init__(
        self,
        persona_id,
        backend=None,
        tier2_model=None,
        config=None,
    ) -> None:
        self.persona_id = persona_id
        self.backend = backend
        self.tier2_model = tier2_model
        self.config = config or {}


def __getattr__(name: str):
    _LAZY = {
        "BatchResult": ("huible.ingestion.batch", "BatchResult"),
        "IngestionWorker": ("huible.ingestion.batch", "IngestionWorker"),
        "Embedder": ("huible.ingestion.embedder", "Embedder"),
        "EmbeddingConfig": ("huible.ingestion.embedder", "EmbeddingConfig"),
        "MultiVectorEmbeddings": ("huible.ingestion.embedder", "MultiVectorEmbeddings"),
        "ConversationTurn": ("huible.ingestion.extractor", "ConversationTurn"),
        "Extractor": ("huible.ingestion.extractor", "Extractor"),
        "MemoryCandidate": ("huible.ingestion.extractor", "MemoryCandidate"),
        "MemoryWriter": ("huible.ingestion.writer", "MemoryWriter"),
        "WriteResult": ("huible.ingestion.writer", "WriteResult"),
    }
    if name in _LAZY:
        module, attr = _LAZY[name]
        import importlib
        mod = importlib.import_module(module)
        return getattr(mod, attr)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BatchResult",
    "ConversationTurn",
    "Embedder",
    "EmbeddingConfig",
    "Extractor",
    "Gate",
    "GateContext",
    "GateOutcome",
    "GateResult",
    "IngestionWorker",
    "MemoryCandidate",
    "MemoryWriter",
    "MultiVectorEmbeddings",
    "WriteResult",
]

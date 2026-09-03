from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any, Protocol, runtime_checkable
from uuid import UUID


class MemoryTier(str, Enum):
    CANONICAL = "canonical"
    DERIVED = "derived"
    ACCRUED = "accrued"
    WORLD = "world"


class ContentType(str, Enum):
    NARRATIVE = "narrative"
    FACT = "fact"
    SENSORY = "sensory"
    RELATIONSHIP = "relationship"
    PREFERENCE = "preference"


class SourceType(str, Enum):
    EXTRACTION = "extraction"
    FAMILY_UPLOAD = "family_upload"
    CANONICAL_SEED = "canonical_seed"
    INFERENCE = "inference"


class DisclosureScope(str, Enum):
    PRIVATE = "private"
    FAMILY = "family"
    CLOSE_FRIENDS = "close_friends"
    ALL_CONTACTS = "all_contacts"


class EdgeType(str, Enum):
    SHARED_PARTICIPANT = "shared_participant"
    TEMPORAL_PROXIMITY = "temporal_proximity"
    THEMATIC = "thematic"
    CAUSAL = "causal"
    CONTRADICTION = "contradiction"
    ELABORATION = "elaboration"


class QuarantineStatus(str, Enum):
    PENDING = "pending"
    ADJUDICATED = "adjudicated"
    PROMOTED = "promoted"
    REJECTED = "rejected"


class QuarantinePriority(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(slots=True, frozen=True)
class MemoryNode:
    id: UUID
    persona_id: UUID
    tier: MemoryTier
    content: str
    content_type: ContentType = ContentType.NARRATIVE

    embedding_content: list[float] | None = None
    embedding_sensory: list[float] | None = None
    embedding_affect: list[float] | None = None

    valid_from: datetime | None = None
    valid_to: datetime | None = None
    memory_date: date | None = None
    source_date: datetime = field(default_factory=lambda: datetime.now())

    source_type: SourceType = SourceType.EXTRACTION
    source_ref: dict[str, Any] = field(default_factory=dict)

    disclosure_scope: DisclosureScope = DisclosureScope.FAMILY

    supersedes: UUID | None = None
    superseded_by: UUID | None = None
    version: int = 1
    is_active: bool = True

    approved_by: UUID | None = None
    approved_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now())

    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class MemoryEdge:
    id: UUID
    source_id: UUID
    target_id: UUID
    edge_type: EdgeType
    weight: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now())


@dataclass(slots=True, frozen=True)
class QuarantineEntry:
    id: UUID
    candidate_data: dict[str, Any]
    persona_id: UUID
    failed_gates: list[str]
    priority: QuarantinePriority = QuarantinePriority.LOW
    status: QuarantineStatus = QuarantineStatus.PENDING
    adjudicated_by: UUID | None = None
    adjudicated_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now())


@dataclass(slots=True)
class SearchResult:
    node: MemoryNode
    score: float


class LexicalSearchUnsupported(RuntimeError):
    """Raised by backends whose storage engine has no FTS capability.

    HU-2309 W2: the lexical lane is optional — retrieval must degrade to the
    vector-only lane (the pre-W2 behavior) when a backend cannot serve
    ``search_lexical`` (e.g. the SQLite test engine has no ``tsvector``).
    Callers catch this specific type, never a bare ``Exception``.
    """


@dataclass(slots=True)
class IngestionResult:
    accepted: bool = False
    rejected: bool = False
    quarantined: bool = False
    gate: str | None = None
    reason: str | None = None
    memory: MemoryNode | None = None
    gates: list[str] | None = None
    priority: QuarantinePriority | None = None


@runtime_checkable
class MemoryBackend(Protocol):
    async def store_memory(self, node: MemoryNode) -> UUID: ...

    async def get_memory(self, memory_id: UUID) -> MemoryNode | None: ...

    async def search_by_content(
        self,
        persona_id: UUID,
        query_embedding: list[float],
        top_k: int = 20,
        disclosure_scope: DisclosureScope | None = None,
    ) -> list[SearchResult]: ...

    async def search_by_sensory(
        self,
        persona_id: UUID,
        query_embedding: list[float],
        top_k: int = 20,
        disclosure_scope: DisclosureScope | None = None,
    ) -> list[SearchResult]: ...

    async def search_by_affect(
        self,
        persona_id: UUID,
        query_embedding: list[float],
        top_k: int = 20,
        disclosure_scope: DisclosureScope | None = None,
    ) -> list[SearchResult]: ...

    async def get_edges(self, memory_id: UUID) -> list[MemoryEdge]: ...

    async def add_edge(self, edge: MemoryEdge) -> UUID: ...

    async def supersede_memory(
        self,
        old_id: UUID,
        new_node: MemoryNode,
    ) -> UUID: ...

    async def get_active_memories(
        self,
        persona_id: UUID,
        limit: int = 50,
    ) -> list[MemoryNode]: ...

    async def quarantine_candidate(
        self,
        entry: QuarantineEntry,
    ) -> UUID: ...

    async def get_all_versions(self, memory_id: UUID) -> list[MemoryNode]: ...


@runtime_checkable
class LexicalSearchBackend(Protocol):
    """Optional capability protocol: a backend that can serve FTS queries.

    HU-2309 W2. Kept separate from :class:`MemoryBackend` so every existing
    ``MemoryBackend`` implementation (and every test fake) stays valid — the
    hybrid seed search probes this protocol and simply skips the lexical lane
    when a backend does not provide it.
    """

    async def search_lexical(
        self,
        persona_id: UUID,
        query: str,
        top_k: int = 20,
        disclosure_scope: DisclosureScope | None = None,
    ) -> list[SearchResult]: ...

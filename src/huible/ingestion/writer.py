from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from datetime import date
from uuid import UUID, uuid4

from huible.ingestion.embedder import MultiVectorEmbeddings
from huible.ingestion.extractor import MemoryCandidate
from huible.memory.protocol import (
    ContentType,
    DisclosureScope,
    EdgeType,
    MemoryBackend,
    MemoryEdge,
    MemoryNode,
    MemoryTier,
    SourceType,
)

logger = logging.getLogger(__name__)


def _parse_memory_date(raw: str | None) -> date | None:
    if not raw:
        return None
    with contextlib.suppress(ValueError, TypeError):
        return date.fromisoformat(raw)
    return None


@dataclass(slots=True)
class WriteResult:
    node: MemoryNode
    edges_created: int = 0
    superseded_id: UUID | None = None


class MemoryWriter:
    def __init__(
        self,
        backend: MemoryBackend,
        default_tier: MemoryTier = MemoryTier.ACCRUED,
        default_source_type: SourceType = SourceType.EXTRACTION,
        default_disclosure: DisclosureScope = DisclosureScope.FAMILY,
    ) -> None:
        self._backend = backend
        self._default_tier = default_tier
        self._default_source_type = default_source_type
        self._default_disclosure = default_disclosure

    async def write_memory(
        self,
        candidate: MemoryCandidate,
        embeddings: MultiVectorEmbeddings,
        persona_id: UUID,
        existing_nodes: list[MemoryNode] | None = None,
    ) -> WriteResult:
        stored_id = await self._backend.store_memory(
            self._build_node(candidate, embeddings, persona_id),
        )

        node = MemoryNode(
            id=stored_id,
            persona_id=persona_id,
            tier=MemoryTier(candidate.tier),
            content=candidate.content,
            content_type=ContentType(candidate.content_type),
            embedding_content=embeddings.content or None,
            embedding_sensory=embeddings.sensory or None,
            embedding_affect=embeddings.affect or None,
            memory_date=_parse_memory_date(candidate.memory_date),
            source_type=SourceType(candidate.source_type),
            source_ref=candidate.source_ref,
            disclosure_scope=DisclosureScope(candidate.disclosure_scope),
            metadata={
                **candidate.extraction_metadata,
                "confidence": candidate.confidence,
            },
        )
        stored_id = await self._backend.store_memory(node)

        edges_created = 0
        if existing_nodes:
            edges_created = await self._create_edges(node, existing_nodes, candidate)

        return WriteResult(node=node, edges_created=edges_created)

    async def write_memories(
        self,
        candidates: list[MemoryCandidate],
        embeddings_list: list[MultiVectorEmbeddings],
        persona_id: UUID,
    ) -> list[WriteResult]:
        if len(candidates) != len(embeddings_list):
            raise ValueError(
                f"Candidate count ({len(candidates)}) "
                f"!= embedding count ({len(embeddings_list)})"
            )

        results: list[WriteResult] = []
        stored_nodes: list[MemoryNode] = []

        for candidate, embeddings in zip(candidates, embeddings_list, strict=True):
            stored_id = await self._backend.store_memory(
                self._build_node(candidate, embeddings, persona_id),
            )

            node = MemoryNode(
                id=stored_id,
                persona_id=persona_id,
                tier=MemoryTier(candidate.tier),
                content=candidate.content,
                content_type=ContentType(candidate.content_type),
                embedding_content=embeddings.content or None,
                embedding_sensory=embeddings.sensory or None,
                embedding_affect=embeddings.affect or None,
                memory_date=_parse_memory_date(candidate.memory_date),
                source_type=SourceType(candidate.source_type),
                source_ref=candidate.source_ref,
                disclosure_scope=DisclosureScope(candidate.disclosure_scope),
                metadata={
                    **candidate.extraction_metadata,
                    "confidence": candidate.confidence,
                },
            )
            stored_nodes.append(node)
            results.append(WriteResult(node=node))

        for i, node in enumerate(stored_nodes):
            edges_created = await self._create_edges(
                node, stored_nodes[:i] + stored_nodes[i + 1:], candidates[i]
            )
            results[i].edges_created = edges_created

        return results

    def _build_node(
        self,
        candidate: MemoryCandidate,
        embeddings: MultiVectorEmbeddings,
        persona_id: UUID,
    ) -> MemoryNode:
        return MemoryNode(
            id=uuid4(),
            persona_id=persona_id,
            tier=MemoryTier(candidate.tier),
            content=candidate.content,
            content_type=ContentType(candidate.content_type),
            embedding_content=embeddings.content or None,
            embedding_sensory=embeddings.sensory or None,
            embedding_affect=embeddings.affect or None,
            memory_date=_parse_memory_date(candidate.memory_date),
            source_type=SourceType(candidate.source_type),
            source_ref=candidate.source_ref,
            disclosure_scope=DisclosureScope(candidate.disclosure_scope),
            metadata={
                **candidate.extraction_metadata,
                "confidence": candidate.confidence,
            },
        )

    async def _create_edges(
        self,
        new_node: MemoryNode,
        existing_nodes: list[MemoryNode],
        candidate: MemoryCandidate,
    ) -> int:
        edges_created = 0
        for other in existing_nodes:
            if other.persona_id != new_node.persona_id:
                continue

            edge = self._find_edge(new_node, other, candidate)
            if edge is None:
                continue

            edge_id = await self._backend.add_edge(edge)
            if edge_id:
                edges_created += 1
                logger.debug(
                    "Edge created: %s -> %s (%s)",
                    new_node.id,
                    other.id,
                    edge.edge_type.value,
                )

        return edges_created

    def _find_edge(
        self,
        new_node: MemoryNode,
        other: MemoryNode,
        candidate: MemoryCandidate,
    ) -> MemoryEdge | None:
        new_participants = set(candidate.participants)
        other_participants = set(other.source_ref.get("participants", []))

        if new_participants & other_participants:
            return MemoryEdge(
                id=uuid4(),
                source_id=new_node.id,
                target_id=other.id,
                edge_type=EdgeType.SHARED_PARTICIPANT,
                weight=0.8,
                metadata={"shared": list(new_participants & other_participants)},
            )

        if new_node.memory_date and other.memory_date:
            days_apart = abs((new_node.memory_date - other.memory_date).days)
            if days_apart <= 7:
                return MemoryEdge(
                    id=uuid4(),
                    source_id=new_node.id,
                    target_id=other.id,
                    edge_type=EdgeType.TEMPORAL_PROXIMITY,
                    weight=max(0.3, 1.0 - days_apart / 7.0),
                    metadata={"days_apart": days_apart},
                )

        if new_node.content_type == other.content_type:
            return MemoryEdge(
                id=uuid4(),
                source_id=new_node.id,
                target_id=other.id,
                edge_type=EdgeType.THEMATIC,
                weight=0.5,
                metadata={"shared_type": new_node.content_type.value},
            )

        return MemoryEdge(
            id=uuid4(),
            source_id=new_node.id,
            target_id=other.id,
            edge_type=EdgeType.THEMATIC,
            weight=0.3,
        )

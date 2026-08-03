from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from huible.ingestion.embedder import Embedder
from huible.ingestion.extractor import ConversationTurn, Extractor, MemoryCandidate
from huible.ingestion.pipeline import IngestionPipeline
from huible.ingestion.writer import MemoryWriter, WriteResult
from huible.memory.protocol import MemoryBackend

logger = logging.getLogger(__name__)

Tier2Model = Callable[..., Coroutine[Any, Any, dict]]


@dataclass(slots=True)
class BatchResult:
    total_turns: int = 0
    candidates_extracted: int = 0
    accepted: int = 0
    rejected: int = 0
    quarantined: int = 0
    edges_created: int = 0
    errors: list[str] = field(default_factory=list)


class IngestionWorker:
    def __init__(
        self,
        backend: MemoryBackend,
        tier2_model: Tier2Model | None = None,
        embedding_fn: Callable | None = None,
        extractor: Extractor | None = None,
        embedder: Embedder | None = None,
        writer: MemoryWriter | None = None,
        pipeline: IngestionPipeline | None = None,
    ) -> None:
        self._backend = backend
        self._tier2_model = tier2_model

        self._extractor = extractor or Extractor(tier2_model=tier2_model)
        self._embedder = embedder or Embedder(embedding_fn=embedding_fn)
        self._writer = writer or MemoryWriter(backend=backend)
        self._pipeline = pipeline or IngestionPipeline()

    async def process_turn(
        self,
        turn: ConversationTurn,
        persona_id: UUID,
    ) -> list[WriteResult]:
        candidates = await self._extractor.extract(turn, persona_id)
        if not candidates:
            return []

        return await self._process_candidates(candidates, persona_id)

    async def process_batch(
        self,
        turns: list[ConversationTurn],
        persona_id: UUID,
    ) -> BatchResult:
        result = BatchResult(total_turns=len(turns))

        all_candidates = await self._extractor.extract_batch(turns, persona_id)
        result.candidates_extracted = len(all_candidates)

        for candidate in all_candidates:
            try:
                write_results = await self._process_single_candidate(
                    candidate, persona_id
                )
                for wr in write_results:
                    result.edges_created += wr.edges_created
                    result.accepted += 1
            except Exception as exc:
                msg = f"Failed to process candidate: {exc}"
                logger.warning(msg, exc_info=True)
                result.errors.append(msg)

        return result

    async def ingest_history(
        self,
        turns: list[ConversationTurn],
        persona_id: UUID,
    ) -> BatchResult:
        logger.info(
            "Starting history ingestion: %d turns for persona %s",
            len(turns),
            persona_id,
        )
        return await self.process_batch(turns, persona_id)

    async def ingest_memories(
        self,
        candidates: list[MemoryCandidate],
        persona_id: UUID,
    ) -> BatchResult:
        result = BatchResult(candidates_extracted=len(candidates))

        for candidate in candidates:
            try:
                write_results = await self._process_single_candidate(
                    candidate, persona_id
                )
                for wr in write_results:
                    result.edges_created += wr.edges_created
                    result.accepted += 1
            except Exception as exc:
                msg = f"Failed to process candidate: {exc}"
                logger.warning(msg, exc_info=True)
                result.errors.append(msg)

        return result

    async def _process_candidates(
        self,
        candidates: list[MemoryCandidate],
        persona_id: UUID,
    ) -> list[WriteResult]:
        all_write_results: list[WriteResult] = []
        for candidate in candidates:
            write_results = await self._process_single_candidate(
                candidate, persona_id
            )
            all_write_results.extend(write_results)
        return all_write_results

    async def _process_single_candidate(
        self,
        candidate: MemoryCandidate,
        persona_id: UUID,
    ) -> list[WriteResult]:
        embeddings = await self._embedder.embed(
            candidate.content,
            sensory_cues=candidate.sensory_cues or None,
            affect_signals=candidate.affect_signals or None,
        )

        candidate_data = {
            "content": candidate.content,
            "content_type": candidate.content_type,
            "tier": candidate.tier,
            "source_type": candidate.source_type,
            "source_ref": candidate.source_ref,
            "disclosure_scope": candidate.disclosure_scope,
            "embedding_content": embeddings.content,
            "embedding_sensory": embeddings.sensory,
            "embedding_affect": embeddings.affect,
        }
        if candidate.memory_date:
            candidate_data["memory_date"] = candidate.memory_date

        pipeline_result = await self._pipeline.process(
            candidate_data,
            persona_id=persona_id,
            backend=self._backend,
            tier2_model=self._tier2_model,
        )

        if pipeline_result.rejected:
            logger.info(
                "Candidate rejected by gate '%s': %s",
                pipeline_result.gate, pipeline_result.reason,
            )
            return []

        if pipeline_result.quarantined:
            logger.info("Candidate quarantined: %s", pipeline_result.reason)
            return []

        if pipeline_result.memory is None:
            return []

        write_result = await self._writer.write_memory(
            candidate, embeddings, persona_id
        )
        logger.info(
            "Memory stored: %s (edges=%d)",
            write_result.node.id,
            write_result.edges_created,
        )
        return [write_result]

    @property
    def extractor(self) -> Extractor:
        return self._extractor

    @property
    def embedder(self) -> Embedder:
        return self._embedder

    @property
    def writer(self) -> MemoryWriter:
        return self._writer

    @property
    def pipeline(self) -> IngestionPipeline:
        return self._pipeline

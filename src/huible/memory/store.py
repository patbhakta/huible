from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import func, literal_column, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from huible.memory.models import (
    MemoryEdgeRow,
    MemoryRow,
    QuarantineRow,
)
from huible.memory.protocol import (
    ContentType,
    DisclosureScope,
    MemoryBackend,
    MemoryEdge,
    MemoryNode,
    MemoryTier,
    QuarantineEntry,
    SearchResult,
    SourceType,
)

logger = logging.getLogger(__name__)


class PostgresMemoryBackend(MemoryBackend):
    def __init__(
        self,
        database_url: str,
        pool_size: int = 10,
        max_overflow: int = 20,
    ) -> None:
        self._engine = create_async_engine(
            database_url,
            pool_size=pool_size,
            max_overflow=max_overflow,
        )
        self._session_factory = async_sessionmaker(
            self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def close(self) -> None:
        await self._engine.dispose()

    def _session(self):
        return self._session_factory()

    def _row_to_node(self, row: MemoryRow) -> MemoryNode:
        """Hydrate a DB row into a :class:`MemoryNode` with enum fields coerced.

        SQLAlchemy returns raw column strings for the enum-backed varchar
        columns; the :class:`MemoryNode` contract (and every consumer — e.g.
        retrieval's motif clustering reading ``content_type.value``) expects
        the protocol enums. The dim-skip guard (HU-1435) previously left the
        Postgres read path unretrievable, so this coercion gap only surfaced
        once 1536-dim vector search actually ran (HU-1909).
        """

        def _coerce(value, enum_cls):
            if isinstance(value, enum_cls):
                return value
            try:
                return enum_cls(str(value))
            except ValueError:
                return value

        return MemoryNode(
            id=row.id,
            persona_id=row.persona_id,
            tier=_coerce(row.tier, MemoryTier),
            content=row.content,
            content_type=_coerce(row.content_type, ContentType),
            embedding_content=row.embedding_content,
            embedding_sensory=row.embedding_sensory,
            embedding_affect=row.embedding_affect,
            valid_from=row.valid_from,
            valid_to=row.valid_to,
            memory_date=row.memory_date,
            source_date=row.source_date,
            source_type=_coerce(row.source_type, SourceType),
            source_ref=row.source_ref or {},
            disclosure_scope=_coerce(row.disclosure_scope, DisclosureScope),
            supersedes=row.supersedes,
            superseded_by=row.superseded_by,
            version=row.version,
            is_active=row.is_active,
            approved_by=row.approved_by,
            approved_at=row.approved_at,
            created_at=row.created_at,
            metadata=row.metadata_ or {},
        )

    def _row_to_edge(self, row: MemoryEdgeRow) -> MemoryEdge:
        return MemoryEdge(
            id=row.id,
            source_id=row.source_id,
            target_id=row.target_id,
            edge_type=row.edge_type,
            weight=row.weight,
            metadata=row.metadata_ or {},
            created_at=row.created_at,
        )

    async def store_memory(self, node: MemoryNode) -> UUID:
        async with self._session() as session:
            row = MemoryRow(
                id=node.id,
                persona_id=node.persona_id,
                tier=node.tier.value if hasattr(node.tier, "value") else str(node.tier),
                content=node.content,
                content_type=node.content_type.value
                if hasattr(node.content_type, "value")
                else str(node.content_type),
                embedding_content=node.embedding_content,
                embedding_sensory=node.embedding_sensory,
                embedding_affect=node.embedding_affect,
                valid_from=node.valid_from,
                valid_to=node.valid_to,
                memory_date=node.memory_date,
                source_type=node.source_type.value
                if hasattr(node.source_type, "value")
                else str(node.source_type),
                source_ref=node.source_ref,
                disclosure_scope=node.disclosure_scope.value
                if hasattr(node.disclosure_scope, "value")
                else str(node.disclosure_scope),
                supersedes=node.supersedes,
                superseded_by=node.superseded_by,
                version=node.version,
                is_active=node.is_active,
                approved_by=node.approved_by,
                approved_at=node.approved_at,
                metadata_=node.metadata,
            )
            session.add(row)
            await session.commit()
            return row.id

    async def get_memory(self, memory_id: UUID) -> MemoryNode | None:
        async with self._session() as session:
            result = await session.execute(
                select(MemoryRow).where(MemoryRow.id == memory_id),
            )
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return self._row_to_node(row)

    async def search_by_content(
        self,
        persona_id: UUID,
        query_embedding: list[float],
        top_k: int = 20,
        disclosure_scope: DisclosureScope | None = None,
    ) -> list[SearchResult]:
        return await self._vector_search(
            persona_id,
            "embedding_content",
            query_embedding,
            top_k,
            disclosure_scope,
        )

    async def search_by_sensory(
        self,
        persona_id: UUID,
        query_embedding: list[float],
        top_k: int = 20,
        disclosure_scope: DisclosureScope | None = None,
    ) -> list[SearchResult]:
        return await self._vector_search(
            persona_id,
            "embedding_sensory",
            query_embedding,
            top_k,
            disclosure_scope,
        )

    async def search_by_affect(
        self,
        persona_id: UUID,
        query_embedding: list[float],
        top_k: int = 20,
        disclosure_scope: DisclosureScope | None = None,
    ) -> list[SearchResult]:
        return await self._vector_search(
            persona_id,
            "embedding_affect",
            query_embedding,
            top_k,
            disclosure_scope,
        )

    async def _vector_search(
        self,
        persona_id: UUID,
        column_name: str,
        query_embedding: list[float],
        top_k: int,
        disclosure_scope: DisclosureScope | None,
    ) -> list[SearchResult]:
        col = getattr(MemoryRow, column_name)
        # Dimension contract (HU-1435): the API's token-hash query embedder
        # (64-dim, fake Stage-1 posture) may not match the stored vector
        # dimension (e.g. 1536-dim seeded memories). Postgres raises DataError
        # on a mismatched cosine_distance, which 500-ed the chat turn during
        # flip verification. Degrade gracefully: skip that column's search
        # (persona voice still serves) and leave a loud breadcrumb instead.
        col_dim = getattr(col.type, "dim", None)
        if col_dim is not None and len(query_embedding) != col_dim:
            logger.warning(
                "vector search skipped: query dim %d != %s dim %d "
                "(query embedder / stored-vector mismatch)",
                len(query_embedding),
                column_name,
                col_dim,
            )
            return []
        # NOTE(HU-1435): ``col.cosine_distance(...)`` (pgvector's SQLAlchemy
        # operator) is not reachable through the ``_PortableVector``
        # TypeDecorator — ``InstrumentedAttribute`` exposes only the impl
        # (LargeBinary) comparator set, so the operator raised AttributeError
        # the first time this search ran against real Postgres during the
        # real-user flip verification. Build the distance expression with an
        # explicit ``literal_column`` instead: pgvector (>=0.7) ships the
        # ``cosine_distance(vector, vector)`` function used here. The query
        # vector is rendered as a float literal (numeric-only, not user
        # input), so there is no injection surface.
        qvec = "[" + ",".join(f"{float(x):.8g}" for x in query_embedding) + "]"
        table_name = MemoryRow.__tablename__
        similarity = literal_column(
            f"(1 - cosine_distance({table_name}.{column_name}, '{qvec}'::vector))"
        ).label("similarity")
        stmt = (
            select(MemoryRow, similarity)
            .where(MemoryRow.persona_id == persona_id)
            .where(MemoryRow.is_active.is_(True))
            .where(col.isnot(None))
            .order_by(text("similarity DESC"))
            .limit(top_k)
        )
        if disclosure_scope is not None:
            scope_value = (
                disclosure_scope.value
                if hasattr(disclosure_scope, "value")
                else str(disclosure_scope)
            )
            stmt = stmt.where(MemoryRow.disclosure_scope == scope_value)

        async with self._session() as session:
            result = await session.execute(stmt)
            rows_with_scores = result.all()
            return [
                SearchResult(node=self._row_to_node(row), score=score)
                for row, score in rows_with_scores
            ]

    async def get_edges(self, memory_id: UUID) -> list[MemoryEdge]:
        async with self._session() as session:
            result = await session.execute(
                select(MemoryEdgeRow).where(MemoryEdgeRow.source_id == memory_id),
            )
            rows = result.scalars().all()
            return [self._row_to_edge(r) for r in rows]

    async def add_edge(self, edge: MemoryEdge) -> UUID:
        async with self._session() as session:
            existing = await session.execute(
                select(MemoryEdgeRow).where(
                    MemoryEdgeRow.source_id == edge.source_id,
                    MemoryEdgeRow.target_id == edge.target_id,
                    MemoryEdgeRow.edge_type
                    == (
                        edge.edge_type.value
                        if hasattr(edge.edge_type, "value")
                        else str(edge.edge_type)
                    ),
                ),
            )
            if existing.scalar_one_or_none() is not None:
                return edge.id

            row = MemoryEdgeRow(
                id=edge.id,
                source_id=edge.source_id,
                target_id=edge.target_id,
                edge_type=edge.edge_type.value
                if hasattr(edge.edge_type, "value")
                else str(edge.edge_type),
                weight=edge.weight,
                metadata_=edge.metadata,
            )
            session.add(row)
            await session.commit()
            return edge.id

    async def supersede_memory(
        self,
        old_id: UUID,
        new_node: MemoryNode,
    ) -> UUID:
        async with self._session() as session:
            await session.execute(
                update(MemoryRow)
                .where(MemoryRow.id == old_id)
                .values(
                    is_active=False,
                    superseded_by=new_node.id,
                ),
            )
            new_row = MemoryRow(
                id=new_node.id,
                persona_id=new_node.persona_id,
                tier=new_node.tier.value if hasattr(new_node.tier, "value") else str(new_node.tier),
                content=new_node.content,
                content_type=new_node.content_type.value
                if hasattr(new_node.content_type, "value")
                else str(new_node.content_type),
                embedding_content=new_node.embedding_content,
                embedding_sensory=new_node.embedding_sensory,
                embedding_affect=new_node.embedding_affect,
                valid_from=new_node.valid_from,
                valid_to=new_node.valid_to,
                memory_date=new_node.memory_date,
                source_type=new_node.source_type.value
                if hasattr(new_node.source_type, "value")
                else str(new_node.source_type),
                source_ref=new_node.source_ref,
                disclosure_scope=new_node.disclosure_scope.value
                if hasattr(new_node.disclosure_scope, "value")
                else str(new_node.disclosure_scope),
                supersedes=old_id,
                version=new_node.version,
                is_active=True,
                approved_by=new_node.approved_by,
                approved_at=new_node.approved_at,
                metadata_=new_node.metadata,
            )
            session.add(new_row)
            await session.commit()
            return new_row.id

    async def get_active_memories(
        self,
        persona_id: UUID,
        limit: int = 50,
    ) -> list[MemoryNode]:
        async with self._session() as session:
            result = await session.execute(
                select(MemoryRow)
                .where(MemoryRow.persona_id == persona_id)
                .where(MemoryRow.is_active.is_(True))
                .order_by(MemoryRow.created_at.desc())
                .limit(limit),
            )
            rows = result.scalars().all()
            return [self._row_to_node(r) for r in rows]

    async def get_active_memory_facts(
        self,
        persona_id: UUID,
        limit: int = 50,
    ) -> list[MemoryNode]:
        """Lightweight active-memory scan for the §7.4.2 grounding corpus (HU-2070).

        Backs :meth:`huible.persona.context.ContextBuilder.persona_scoped_grounding_refs`
        on corpora far larger than one turn's retrieval window (the Chandler
        raw-dialogue persona carries 14k+ active memories). Selects only the
        G4-gate-relevant columns — embedding vectors are deliberately not
        materialized, so a full-corpus scan stays a cheap text read instead of
        a ~175MB vector payload. The returned nodes carry ``embedding_*=None``
        and default audit fields; they are grounding-corpus inputs only, never
        retrieval results.
        """
        def _coerce(value, enum_cls):
            if isinstance(value, enum_cls):
                return value
            try:
                return enum_cls(str(value))
            except ValueError:
                return value

        async with self._session() as session:
            result = await session.execute(
                select(
                    MemoryRow.id,
                    MemoryRow.persona_id,
                    MemoryRow.tier,
                    MemoryRow.content,
                    MemoryRow.content_type,
                    MemoryRow.memory_date,
                    MemoryRow.source_type,
                    MemoryRow.disclosure_scope,
                    MemoryRow.version,
                    MemoryRow.is_active,
                    MemoryRow.metadata_,
                    MemoryRow.created_at,
                )
                .where(MemoryRow.persona_id == persona_id)
                .where(MemoryRow.is_active.is_(True))
                .order_by(MemoryRow.created_at.desc())
                .limit(limit),
            )
            return [
                MemoryNode(
                    id=r.id,
                    persona_id=r.persona_id,
                    tier=_coerce(r.tier, MemoryTier),
                    content=r.content,
                    content_type=_coerce(r.content_type, ContentType),
                    memory_date=r.memory_date,
                    source_type=_coerce(r.source_type, SourceType),
                    disclosure_scope=_coerce(r.disclosure_scope, DisclosureScope),
                    version=r.version,
                    is_active=r.is_active,
                    created_at=r.created_at,
                    metadata=r.metadata_ or {},
                )
                for r in result.all()
            ]

    async def quarantine_candidate(
        self,
        entry: QuarantineEntry,
    ) -> UUID:
        async with self._session() as session:
            row = QuarantineRow(
                id=entry.id,
                candidate_data=entry.candidate_data,
                persona_id=entry.persona_id,
                failed_gates=entry.failed_gates,
                priority=entry.priority.value
                if hasattr(entry.priority, "value")
                else str(entry.priority),
                status=entry.status.value if hasattr(entry.status, "value") else str(entry.status),
                adjudicated_by=entry.adjudicated_by,
                adjudicated_at=entry.adjudicated_at,
            )
            session.add(row)
            await session.commit()
            return row.id

    async def get_all_versions(self, memory_id: UUID) -> list[MemoryNode]:
        root = await self.get_memory(memory_id)
        if root is None:
            return []
        versions = [root]
        current = root
        while current.superseded_by is not None:
            next_mem = await self.get_memory(current.superseded_by)
            if next_mem is None:
                break
            versions.append(next_mem)
            current = next_mem
        return versions

    async def get_persona_memories_count(self, persona_id: UUID) -> int:
        async with self._session() as session:
            result = await session.execute(
                select(func.count())
                .select_from(MemoryRow)
                .where(MemoryRow.persona_id == persona_id)
                .where(MemoryRow.is_active.is_(True)),
            )
            return result.scalar_one() or 0

    async def health_check(self) -> dict[str, str]:
        """Probe DB connectivity and pgvector availability (HU-1403 ``/health``).

        Returns a small status dict for the health checks map:
        ``database`` is ``ok`` when ``SELECT 1`` succeeds else ``unhealthy``;
        ``pgvector`` is ``ok`` when the ``vector`` extension is installed,
        ``missing`` when the DB is reachable but the extension is absent, and
        ``unknown`` when connectivity itself failed.
        """
        try:
            async with self._engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
                result = await conn.execute(
                    text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
                )
                row = result.first()
        except Exception:
            logger.exception("memory backend health check failed")
            return {"database": "unhealthy", "pgvector": "unknown"}
        return {"database": "ok", "pgvector": "ok" if row is not None else "missing"}

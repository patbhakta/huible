from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    TypeDecorator,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import (
    Mapped,
    mapped_column,
    relationship,
)
from sqlalchemy.types import JSON

if TYPE_CHECKING:
    pass

from sqlalchemy.orm import DeclarativeBase


class _PortableJSON(TypeDecorator):
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())


class _PortableVector(TypeDecorator):
    impl = LargeBinary
    cache_ok = True

    def __init__(self, dim: int):
        super().__init__()
        self.dim = dim

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(Vector(self.dim))
        return dialect.type_descriptor(LargeBinary())

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value
        import struct
        return struct.pack(f"<{len(value)}f", *value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return list(value) if value is not None else None
        if isinstance(value, (bytes, bytearray)):
            import struct
            n = len(value) // 4
            return list(struct.unpack(f"<{n}f", value))
        return list(value) if value is not None else None


class Base(DeclarativeBase):
    pass


_TS = func.now()
_UUID = uuid4


class PersonaRow(Base):
    __tablename__ = "personas"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_UUID)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(128))
    age_at_death: Mapped[int | None] = mapped_column(Integer)
    death_date: Mapped[date | None] = mapped_column(Date)
    birth_date: Mapped[date | None] = mapped_column(Date)
    voice_instructions: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    era_knowledge_boundary: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="2020-01-01",
    )
    metadata_: Mapped[dict] = mapped_column(
        "metadata", _PortableJSON, nullable=False, default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_TS,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_TS,
    )

    memories: Mapped[list[MemoryRow]] = relationship(back_populates="persona")

    __table_args__ = ()


class MemoryRow(Base):
    __tablename__ = "memories"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_UUID)
    persona_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("personas.id"), nullable=False,
    )
    tier: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="narrative",
    )
    embedding_content: Mapped[list[float] | None] = mapped_column(_PortableVector(1536))
    embedding_sensory: Mapped[list[float] | None] = mapped_column(_PortableVector(1536))
    embedding_affect: Mapped[list[float] | None] = mapped_column(_PortableVector(512))
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    memory_date: Mapped[date | None] = mapped_column(Date)
    source_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_TS,
    )
    source_type: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="extraction",
    )
    source_ref: Mapped[dict | None] = mapped_column(_PortableJSON)
    disclosure_scope: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="family",
    )
    supersedes: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("memories.id"),
    )
    superseded_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("memories.id"),
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="1",
    )
    approved_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_TS,
    )
    metadata_: Mapped[dict] = mapped_column(
        "metadata", _PortableJSON, nullable=False, default=dict,
    )

    persona: Mapped[PersonaRow] = relationship(back_populates="memories")

    __table_args__ = (
        Index("idx_memories_persona_tier", "persona_id", "tier"),
        Index("idx_memories_disclosure", "persona_id", "disclosure_scope"),
    )


class MemoryEdgeRow(Base):
    __tablename__ = "memory_edges"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_UUID)
    source_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("memories.id"), nullable=False,
    )
    target_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("memories.id"), nullable=False,
    )
    edge_type: Mapped[str] = mapped_column(String(32), nullable=False)
    weight: Mapped[float] = mapped_column(
        Float, nullable=False, server_default="1.0",
    )
    metadata_: Mapped[dict] = mapped_column(
        "metadata", _PortableJSON, nullable=False, default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_TS,
    )

    __table_args__ = (
        Index("idx_memory_edges_source", "source_id"),
        Index("idx_memory_edges_target", "target_id"),
    )


class QuarantineRow(Base):
    __tablename__ = "quarantine"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_UUID)
    candidate_data: Mapped[dict] = mapped_column(_PortableJSON, nullable=False)
    persona_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("personas.id"), nullable=False,
    )
    failed_gates: Mapped[list[str]] = mapped_column(
        _PortableJSON, nullable=False,
    )
    priority: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="low",
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="pending",
    )
    adjudicated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    adjudicated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_TS,
    )

    __table_args__ = (
        Index("idx_quarantine_status", "status", "priority"),
    )

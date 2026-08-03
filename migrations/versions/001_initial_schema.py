"""Initial schema: personas, memories, memory_edges, quarantine + pgvector indexes

Revision ID: 001_initial_schema
Revises: None
Create Date: 2026-07-30
"""

from typing import Sequence, Union

from alembic import op

revision: str = "001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.execute("""
        CREATE TABLE personas (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name            VARCHAR(128) NOT NULL,
            display_name    VARCHAR(128),
            age_at_death    INT,
            death_date      DATE,
            birth_date      DATE,
            voice_instructions TEXT NOT NULL DEFAULT '',
            era_knowledge_boundary VARCHAR(64) NOT NULL DEFAULT '2020-01-01',
            metadata        JSONB NOT NULL DEFAULT '{}',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)

    op.execute("""
        CREATE TABLE memories (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            persona_id    UUID NOT NULL REFERENCES personas(id),
            tier          VARCHAR(16) NOT NULL CHECK (tier IN ('canonical', 'derived', 'accrued', 'world')),
            content       TEXT NOT NULL,
            content_type  VARCHAR(32) NOT NULL DEFAULT 'narrative',
            embedding_content  vector(1536),
            embedding_sensory  vector(1536),
            embedding_affect   vector(512),
            valid_from    TIMESTAMPTZ,
            valid_to      TIMESTAMPTZ,
            memory_date   DATE,
            source_date   TIMESTAMPTZ NOT NULL DEFAULT now(),
            source_type   VARCHAR(32) NOT NULL DEFAULT 'extraction',
            source_ref    JSONB,
            disclosure_scope VARCHAR(32) NOT NULL DEFAULT 'family',
            supersedes    UUID REFERENCES memories(id),
            superseded_by UUID REFERENCES memories(id),
            version       INT NOT NULL DEFAULT 1,
            is_active     BOOLEAN NOT NULL DEFAULT TRUE,
            approved_by   UUID,
            approved_at   TIMESTAMPTZ,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            metadata      JSONB NOT NULL DEFAULT '{}'
        );
    """)

    op.execute("""
        CREATE TABLE memory_edges (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_id   UUID NOT NULL REFERENCES memories(id),
            target_id   UUID NOT NULL REFERENCES memories(id),
            edge_type   VARCHAR(32) NOT NULL,
            weight      FLOAT NOT NULL DEFAULT 1.0,
            metadata    JSONB NOT NULL DEFAULT '{}',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (source_id, target_id, edge_type)
        );
    """)

    op.execute("""
        CREATE TABLE quarantine (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            candidate_data  JSONB NOT NULL,
            persona_id      UUID NOT NULL REFERENCES personas(id),
            failed_gates    VARCHAR(32)[] NOT NULL,
            priority        VARCHAR(16) NOT NULL DEFAULT 'low',
            status          VARCHAR(16) NOT NULL DEFAULT 'pending',
            adjudicated_by  UUID,
            adjudicated_at  TIMESTAMPTZ,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)

    op.execute(
        "CREATE INDEX idx_memories_content_emb ON memories "
        "USING hnsw (embedding_content vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )
    op.execute(
        "CREATE INDEX idx_memories_sensory_emb ON memories "
        "USING hnsw (embedding_sensory vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )
    op.execute(
        "CREATE INDEX idx_memories_affect_emb ON memories "
        "USING hnsw (embedding_affect vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )
    op.execute(
        "CREATE INDEX idx_memories_active ON memories (persona_id, is_active) WHERE is_active = TRUE"
    )
    op.create_index("idx_memories_persona_tier", "memories", ["persona_id", "tier"])
    op.create_index("idx_memories_disclosure", "memories", ["persona_id", "disclosure_scope"])
    op.create_index("idx_memory_edges_source", "memory_edges", ["source_id"])
    op.create_index("idx_memory_edges_target", "memory_edges", ["target_id"])
    op.create_index("idx_quarantine_status", "quarantine", ["status", "priority"])


def downgrade() -> None:
    op.drop_table("quarantine")
    op.drop_table("memory_edges")
    op.drop_table("memories")
    op.drop_table("personas")
    op.execute("DROP EXTENSION IF EXISTS vector")

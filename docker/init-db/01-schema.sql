-- Huible Database Initialization
-- Runs automatically on first PostgreSQL container start via docker-entrypoint-initdb.d
-- The pgvector image already includes the vector extension.

\c huible

-- Enable pgvector (idempotent)
CREATE EXTENSION IF NOT EXISTS vector;

-- Schema: mirrors migrations/schema.sql exactly
CREATE TABLE IF NOT EXISTS personas (
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

CREATE TABLE IF NOT EXISTS memories (
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

CREATE INDEX IF NOT EXISTS idx_memories_content_emb ON memories
    USING hnsw (embedding_content vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX IF NOT EXISTS idx_memories_sensory_emb ON memories
    USING hnsw (embedding_sensory vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX IF NOT EXISTS idx_memories_affect_emb ON memories
    USING hnsw (embedding_affect vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX IF NOT EXISTS idx_memories_active ON memories (persona_id, is_active)
    WHERE is_active = TRUE;

CREATE TABLE IF NOT EXISTS memory_edges (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id   UUID NOT NULL REFERENCES memories(id),
    target_id   UUID NOT NULL REFERENCES memories(id),
    edge_type   VARCHAR(32) NOT NULL,
    weight      FLOAT NOT NULL DEFAULT 1.0,
    metadata    JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_id, target_id, edge_type)
);

CREATE TABLE IF NOT EXISTS quarantine (
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

-- API keys table for Bearer auth
CREATE TABLE IF NOT EXISTS api_keys (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key_hash    VARCHAR(64) NOT NULL UNIQUE,
    persona_id  UUID NOT NULL REFERENCES personas(id),
    label       VARCHAR(128),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys (key_hash);

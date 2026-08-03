-- 02-schema.sql
-- Full Huible schema. Applied on first Postgres container start.
-- Source of truth: migrations/schema.sql

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
CREATE INDEX IF NOT EXISTS idx_memories_persona_tier ON memories (persona_id, tier);
CREATE INDEX IF NOT EXISTS idx_memories_disclosure ON memories (persona_id, disclosure_scope);

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

CREATE INDEX IF NOT EXISTS idx_memory_edges_source ON memory_edges (source_id);
CREATE INDEX IF NOT EXISTS idx_memory_edges_target ON memory_edges (target_id);

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

CREATE INDEX IF NOT EXISTS idx_quarantine_status ON quarantine (persona_id, status);

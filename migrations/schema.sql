-- Huible Memory Engine — Full Schema Reference
-- Matches ENGINE_SPEC.md sections 3.2 and 4.3 exactly.
-- This file is the canonical SQL definition; Alembic migrations derive from it.

CREATE EXTENSION IF NOT EXISTS vector;

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

CREATE TABLE memories (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    persona_id    UUID NOT NULL REFERENCES personas(id),
    tier          VARCHAR(16) NOT NULL CHECK (tier IN ('canonical', 'derived', 'accrued', 'world')),
    content       TEXT NOT NULL,
    content_type  VARCHAR(32) NOT NULL DEFAULT 'narrative',
                 -- 'narrative', 'fact', 'sensory', 'relationship', 'preference'

    -- Multi-vector embeddings
    embedding_content  vector(1536),   -- semantic content embedding
    embedding_sensory  vector(1536),   -- sensory/situational embedding
    embedding_affect   vector(512),    -- emotional valence embedding

    -- Temporal scoping
    valid_from    TIMESTAMPTZ,
    valid_to      TIMESTAMPTZ,
    memory_date   DATE,              -- when the event approximately occurred
    source_date   TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Provenance
    source_type   VARCHAR(32) NOT NULL DEFAULT 'extraction',
                 -- 'extraction', 'family_upload', 'canonical_seed', 'inference'
    source_ref    JSONB,              -- reference to source conversation/message

    -- Disclosure
    disclosure_scope VARCHAR(32) NOT NULL DEFAULT 'family',
                 -- 'private', 'family', 'close_friends', 'all_contacts'

    -- Version chain (append-only)
    supersedes    UUID REFERENCES memories(id),
    superseded_by UUID REFERENCES memories(id),
    version       INT NOT NULL DEFAULT 1,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,

    -- Audit
    approved_by   UUID,              -- user who approved (for canonical promotions)
    approved_at   TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Metadata
    metadata      JSONB NOT NULL DEFAULT '{}'
);

-- Multi-index for different embedding search modes
CREATE INDEX idx_memories_content_emb ON memories
    USING hnsw (embedding_content vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX idx_memories_sensory_emb ON memories
    USING hnsw (embedding_sensory vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX idx_memories_affect_emb ON memories
    USING hnsw (embedding_affect vector_cosine_ops) WITH (m = 16, ef_construction = 64);

-- Active-memories index (query efficiency)
CREATE INDEX idx_memories_active ON memories (persona_id, is_active)
    WHERE is_active = TRUE;

CREATE TABLE memory_edges (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id   UUID NOT NULL REFERENCES memories(id),
    target_id   UUID NOT NULL REFERENCES memories(id),
    edge_type   VARCHAR(32) NOT NULL,
               -- 'shared_participant', 'temporal_proximity', 'thematic',
               --  'causal', 'contradiction', 'elaboration'
    weight      FLOAT NOT NULL DEFAULT 1.0,
    metadata    JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_id, target_id, edge_type)
);

CREATE TABLE quarantine (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    candidate_data  JSONB NOT NULL,
    persona_id      UUID NOT NULL REFERENCES personas(id),
    failed_gates    VARCHAR(32)[] NOT NULL,
    priority        VARCHAR(16) NOT NULL DEFAULT 'low'
                     -- 'critical', 'high', 'medium', 'low'
    status          VARCHAR(16) NOT NULL DEFAULT 'pending'
                     -- 'pending', 'adjudicated', 'promoted', 'rejected'
    adjudicated_by  UUID,
    adjudicated_at  TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

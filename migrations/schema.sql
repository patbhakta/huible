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
    priority        VARCHAR(16) NOT NULL DEFAULT 'low',
                     -- 'critical', 'high', 'medium', 'low'
    status          VARCHAR(16) NOT NULL DEFAULT 'pending',
                     -- 'pending', 'adjudicated', 'promoted', 'rejected'
    adjudicated_by  UUID,
    adjudicated_at  TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================
-- §7.4 durable safety-state tables (HU-1440 / HU-1445 / HU-1459)
-- Drop-in Postgres backends for the handoff queue, consent gate,
-- per-session conversation / crisis state, and G8 risk profile.
-- These persist across container restarts so §10.1 invariant 5
-- (audit every escalation), the §7.4.3 consent gate, the §7.4.4
-- dosage-cap + crisis-history enforcement, and G8 risk-flag
-- enforcement stay correct after `docker compose restart app`.
-- Alembic migrations 002_durable_safety_state + 003_risk_profiles
-- derive from this section.
-- ============================================================

CREATE TABLE handoff_tickets (
    id                     TEXT PRIMARY KEY,
    persona_id             TEXT NOT NULL,
    conversation_id        TEXT,
    trigger_signal         TEXT NOT NULL,
    affect                 TEXT NOT NULL,
    matched_patterns       JSONB NOT NULL DEFAULT '[]',
    risk_flags             JSONB NOT NULL DEFAULT '[]',
    sla_target_seconds     INT NOT NULL DEFAULT 300,
    created_at             TIMESTAMPTZ NOT NULL,
    outcome                TEXT NOT NULL DEFAULT 'enqueued',
    responder_id           TEXT,
    clinical_review_note   TEXT,
    degrade_reason         TEXT,
    resolved_at            TIMESTAMPTZ
);

CREATE INDEX idx_handoff_outcome ON handoff_tickets (outcome);
CREATE INDEX idx_handoff_created ON handoff_tickets (created_at);

CREATE TABLE consent_records (
    acknowledgment_id  TEXT PRIMARY KEY,
    session_id         TEXT NOT NULL,
    persona_id         TEXT NOT NULL,
    card_version       INT NOT NULL,
    acknowledged_at    TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_consent_session_persona ON consent_records (session_id, persona_id);
CREATE INDEX idx_consent_acknowledged_at ON consent_records (acknowledged_at);

CREATE TABLE conversation_turns (
    id              BIGSERIAL PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    speaker         TEXT NOT NULL,
    content         TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_conversation_turns_conv ON conversation_turns (conversation_id, id);

CREATE TABLE crisis_sessions (
    conversation_id TEXT PRIMARY KEY,
    marked_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE risk_profiles (
    id           BIGSERIAL PRIMARY KEY,
    scope        TEXT NOT NULL,
    persona_id   TEXT NOT NULL,
    session_id   TEXT,
    flags        JSONB NOT NULL DEFAULT '[]',
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_risk_profile_persona ON risk_profiles (scope, persona_id);
CREATE INDEX idx_risk_profile_session ON risk_profiles (scope, persona_id, session_id);

CREATE TABLE llm_usage (
    id               BIGSERIAL PRIMARY KEY,
    org_id           UUID,
    api_key_id       VARCHAR(64) NOT NULL,
    persona_id       VARCHAR(64) NOT NULL,
    conversation_id  TEXT,
    provider         VARCHAR(32) NOT NULL,
    model            VARCHAR(128),
    requests         INT NOT NULL DEFAULT 1,
    tokens_in        INT NOT NULL DEFAULT 0,
    tokens_out       INT NOT NULL DEFAULT 0,
    latency_ms       INT NOT NULL DEFAULT 0,
    modeled_cost_usd NUMERIC(14, 8) NOT NULL DEFAULT 0,
    cost_basis       VARCHAR(16) NOT NULL DEFAULT 'modeled',
    key_source       VARCHAR(16) NOT NULL DEFAULT 'shared',
    day              DATE NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_llm_usage_day ON llm_usage (day);
CREATE INDEX idx_llm_usage_key_day ON llm_usage (api_key_id, day);
CREATE INDEX idx_llm_usage_persona_day ON llm_usage (persona_id, day);
CREATE INDEX idx_llm_usage_conversation ON llm_usage (conversation_id);
CREATE INDEX idx_llm_usage_org_day ON llm_usage (org_id, day);
CREATE INDEX idx_llm_usage_key_source_day ON llm_usage (key_source, day);

-- HU-2243 Sprint 3: encrypted per-tenant BYOK provider-key registry.
-- key_ciphertext is AES-256-GCM sealed under BYOK_VAULT_MASTER_KEY
-- (scrypt per-row salt, tenant+provider as AAD); raw keys never stored.
CREATE TABLE byok_keys (
    id              BIGSERIAL PRIMARY KEY,
    api_key_id      VARCHAR(64) NOT NULL,
    provider        VARCHAR(32) NOT NULL,
    key_ciphertext  TEXT NOT NULL,
    key_fingerprint VARCHAR(64) NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_byok_keys_tenant_provider UNIQUE (api_key_id, provider)
);

CREATE INDEX idx_byok_keys_tenant ON byok_keys (api_key_id);

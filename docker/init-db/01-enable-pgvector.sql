-- 01-enable-pgvector.sql
-- Enable pgvector extension (runs before schema init)

CREATE EXTENSION IF NOT EXISTS vector;

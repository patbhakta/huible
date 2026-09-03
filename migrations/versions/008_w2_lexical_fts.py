"""W2: Postgres FTS (GIN) index over persona memories content + provenance

Revision ID: 008_w2_lexical_fts
Revises: 007_w1_vector_384
Create Date: 2026-09-03

HU-2309 v1.8 §1.7.2 W2 (M-0R-A lexical lane). Creates the expression GIN
index backing the hybrid retrieval lane: ``websearch_to_tsquery`` user-text
queries matched against ``to_tsvector('english', content + source_ref)``.

The 2-arg ``to_tsvector`` (explicit regconfig) is IMMUTABLE, so the exact
expression can be indexed. The expression must stay byte-identical to
``huible.memory.store._FTS_TSV_EXPRESSION`` — that is what the lexical
query renders — or the planner will not use this index (kept in sync by
comment cross-reference; schema-only, no data changes, safe to run online).

Downgrade drops the index only: the lexical lane degrades to vector-only
(the pre-W2 behavior) via ``LexicalSearchUnsupported``-free fallback — the
query simply finds no index and retrieval keeps working.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "008_w2_lexical_fts"
down_revision: str | None = "007_w1_vector_384"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "memories"
_INDEX = "idx_memories_fts_content"

# Byte-identical to huible.memory.store._FTS_TSV_EXPRESSION (see above).
_TSV = (
    "to_tsvector('english', coalesce(memories.content, '') || ' ' "
    "|| coalesce(memories.source_ref::text, ''))"
)


def upgrade() -> None:
    op.execute(
        f"CREATE INDEX {_INDEX} ON {_TABLE} USING gin (({_TSV}))"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_INDEX}")

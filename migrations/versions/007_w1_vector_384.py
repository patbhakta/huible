"""W1: vector(1536) → vector(384) for memories.embedding_content / embedding_sensory

Revision ID: 007_w1_vector_384
Revises: 006_byok_key_vault
Create Date: 2026-09-03

HU-2309 v1.8 M-0R-A (W1 real embeddings end-to-end). Cuts both Stage-1
1536-dim token-hash vector columns to the 384-dim local ONNX
bge-small-en-v1.5 schema (TL pick, R&D option (a)).

DATA NOTE (by design): the ``USING NULL::vector(384)`` cast drops every stored
vector. The old vectors are token-hash artifacts that were never semantically
read (RC-2 / evidence E4) — derived data, not source content. The companion
script ``scripts/migrate_embeddings_384.py`` re-embeds all memories from
their content in the same deploy window (no mixed-dim state: the app must
start with ``EMBEDDING_PROVIDER=local_onnx`` only after this migration plus
the re-embed complete; retrieval degrades to persona-voice-only until then,
which is a valid Class-B state per CA condition C3).

Downgrade restores the 1536 columns but cannot resurrect the dropped vectors
(they must be re-embedded with the legacy embedder if ever needed).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "007_w1_vector_384"
down_revision: str | None = "006_byok_key_vault"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "memories"
_COLUMNS = ("embedding_content", "embedding_sensory")


def upgrade() -> None:
    for column in _COLUMNS:
        op.execute(
            f"ALTER TABLE {_TABLE} ALTER COLUMN {column} "
            f"TYPE vector(384) USING NULL::vector(384)"
        )


def downgrade() -> None:
    for column in _COLUMNS:
        op.execute(
            f"ALTER TABLE {_TABLE} ALTER COLUMN {column} "
            f"TYPE vector(1536) USING NULL::vector(1536)"
        )

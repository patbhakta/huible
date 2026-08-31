"""Usage metering key_source attribution (HU-2243 Sprint 2)

Revision ID: 005_usage_key_source
Revises: 004_usage_metering
Create Date: 2026-08-31

Founder directive (Pat, 2026-08-30) parts (1) and (3): product traffic moves
to a dedicated provider API key (separation from internals), and clients may
supply their own provider key (BYOK) with per-tenant usage attribution and
graceful fallback to the house key. Sprint 2 adds ``key_source`` to the
metering rows so every usage row records WHICH key served the turn:

* ``shared``  — the shared internals key (pre-separation posture; the
  backfill default — every row written before this migration ran on it).
* ``product`` — the dedicated product key (``PERSONA_LLM_PROVIDER`` /
  ``PERSONA_LLM_API_KEY`` overlay on the persona voice).
* ``byok``    — a client-supplied provider key (``X-Provider-Key`` on the
  chat turn, gated by ``BYOK_ENABLED``); attribution stays the caller's own
  bearer-key digest (``api_key_id``), so per-tenant rollups are unchanged.

The daily aggregate (``GET /api/v1/usage/daily``) now groups by
``key_source`` as well — one row per (day, key, persona, key_source) — which
is the split the founder asked for: product vs internals COGS, and house vs
BYOK spend per tenant, from the same tables.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "005_usage_key_source"
down_revision: str | None = "004_usage_metering"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE llm_usage
            ADD COLUMN key_source VARCHAR(16) NOT NULL DEFAULT 'shared';
        """
    )
    op.execute("CREATE INDEX idx_llm_usage_key_source_day ON llm_usage (key_source, day)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_llm_usage_key_source_day")
    op.execute("ALTER TABLE llm_usage DROP COLUMN key_source")

"""Persistent G8 risk profiles (HU-1445)

Revision ID: 003_risk_profiles
Revises: 002_durable_safety_state
Create Date: 2026-08-11

Durable storage for the intake-derived ``risk_flags`` that §7.4.4 G8
enforcement acts on. Stage 0.1 (HU-1440) covered the handoff queue, consent
gate, and conversation / crisis state; the risk profile backend was left
in-memory, which means a container restart wipes the per-session +
per-persona flags and G8 enforcement silently goes inert (a populated
``loss_of_child`` / ``minor_decedent`` / ``recent_loss`` / ``non_acceptance``
/ ``proxy_user`` profile reverts to empty). This migration adds the
``risk_profiles`` table the drop-in :class:`huible.safety.store.PostgresRiskProfile`
writes to (HU-1445).

Table (see huible.safety.store.RiskProfileRow for the ORM mapping):

* risk_profiles — one row per (scope, persona_id[, session_id]) flag set.
  ``scope = 'persona'`` rows apply to every session for the persona;
  ``scope = 'session'`` rows are session-scoped. The durable
  ``get_flags(session_id, persona_id)`` returns the union of both.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "003_risk_profiles"
down_revision: Union[str, None] = "002_durable_safety_state"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE risk_profiles (
            id           BIGSERIAL PRIMARY KEY,
            scope        TEXT NOT NULL,
            persona_id   TEXT NOT NULL,
            session_id   TEXT,
            flags        JSONB NOT NULL DEFAULT '[]',
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)
    op.execute(
        "CREATE INDEX idx_risk_profile_persona "
        "ON risk_profiles (scope, persona_id)"
    )
    op.execute(
        "CREATE INDEX idx_risk_profile_session "
        "ON risk_profiles (scope, persona_id, session_id)"
    )


def downgrade() -> None:
    op.drop_table("risk_profiles")

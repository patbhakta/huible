"""Durable §7.4 safety state: handoff_tickets, consent_records, conversation_turns, crisis_sessions

Revision ID: 002_durable_safety_state
Revises: 001_initial_schema
Create Date: 2026-08-11

Pre-real-launch persistence for the §7.4 operational surfaces (HU-1440).
The in-memory defaults (InMemoryHandoffQueue, InMemoryConsentGate, and the
app.state.conversations / crisis_sessions dicts) are wiped on every container
restart, which violates §10.1 invariant 5 ("audit every escalation") and
silently breaks the "a person will join you right now" promise an ENQUEUED
handoff ticket makes. This migration adds the durable Postgres tables the
drop-in backends in huible.safety.store write to.

Tables (see huible.safety.store for the ORM mappings):

* handoff_tickets     — one row per §7.4.1 escalation (the audit log itself).
* consent_records     — one row per §7.4.3 acknowledgment (full history).
* conversation_turns  — per-session chat history (turn counts, distress trend).
* crisis_sessions     — per-session prior-G1-crisis marker (matrix §3 signal).
"""

from typing import Sequence, Union

from alembic import op

revision: str = "002_durable_safety_state"
down_revision: Union[str, None] = "001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
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
    """)
    op.execute(
        "CREATE INDEX idx_handoff_outcome ON handoff_tickets (outcome)"
    )
    op.execute(
        "CREATE INDEX idx_handoff_created ON handoff_tickets (created_at)"
    )

    op.execute("""
        CREATE TABLE consent_records (
            acknowledgment_id  TEXT PRIMARY KEY,
            session_id         TEXT NOT NULL,
            persona_id         TEXT NOT NULL,
            card_version       INT NOT NULL,
            acknowledged_at    TIMESTAMPTZ NOT NULL
        );
    """)
    op.execute(
        "CREATE INDEX idx_consent_session_persona "
        "ON consent_records (session_id, persona_id)"
    )
    op.execute(
        "CREATE INDEX idx_consent_acknowledged_at "
        "ON consent_records (acknowledged_at)"
    )

    op.execute("""
        CREATE TABLE conversation_turns (
            id              BIGSERIAL PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            speaker         TEXT NOT NULL,
            content         TEXT NOT NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)
    op.execute(
        "CREATE INDEX idx_conversation_turns_conv "
        "ON conversation_turns (conversation_id, id)"
    )

    op.execute("""
        CREATE TABLE crisis_sessions (
            conversation_id TEXT PRIMARY KEY,
            marked_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)


def downgrade() -> None:
    op.drop_table("crisis_sessions")
    op.drop_table("conversation_turns")
    op.drop_table("consent_records")
    op.drop_table("handoff_tickets")

"""LLM usage metering skeleton (HU-2243 Sprint 1)

Revision ID: 004_usage_metering
Revises: 003_risk_profiles
Create Date: 2026-08-31

Founder directive (Pat, 2026-08-30): per-persona / per-conversation token +
cost accounting keyed on the product provider key, surfaced as dashboard /
report data — the minimum that makes valuation data, plan pricing, and B2B
API billing possible. Sprint 1 (CEO scope, 2026-08-31) lands the metering
skeleton: per-org / per-conversation usage rows written by the chat path on
every LLM turn, plus a read endpoint returning per-key / per-persona daily
aggregates. Polar wiring, BYOK vault, and the dashboard UI are explicitly
out of scope for this sprint.

Table (see huible.api.metering.UsageRow for the ORM mapping):

* llm_usage — one row per metered chat-turn LLM call. Attribution columns:
  ``org_id`` (tenant, NULL until orgs exist — column ready so per-org
  rollups work the moment keys gain org bindings), ``api_key_id`` (SHA-256
  digest of the caller's bearer key — per-key attribution without storing
  raw keys), ``persona_id``, ``conversation_id``. Resource columns:
  ``provider`` / ``model``, ``requests``, ``tokens_in`` / ``tokens_out``,
  ``latency_ms``, ``modeled_cost_usd`` (reference-rate model — the z.ai
  coding subscription is quota-not-metered, so cost is *modeled* at public
  API reference rates for valuation/pricing data), ``cost_basis`` (
  ``modeled`` | ``reported`` | ``free``). ``day`` is the UTC calendar date
  of the turn (consistent with the zai daily-token ledger) so daily
  aggregates are a plain GROUP BY.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "004_usage_metering"
down_revision: str | None = "003_risk_profiles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
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
            day              DATE NOT NULL,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)
    op.execute(
        "CREATE INDEX idx_llm_usage_day ON llm_usage (day)"
    )
    op.execute(
        "CREATE INDEX idx_llm_usage_key_day ON llm_usage (api_key_id, day)"
    )
    op.execute(
        "CREATE INDEX idx_llm_usage_persona_day ON llm_usage (persona_id, day)"
    )
    op.execute(
        "CREATE INDEX idx_llm_usage_conversation ON llm_usage (conversation_id)"
    )
    op.execute(
        "CREATE INDEX idx_llm_usage_org_day ON llm_usage (org_id, day)"
    )


def downgrade() -> None:
    op.drop_table("llm_usage")

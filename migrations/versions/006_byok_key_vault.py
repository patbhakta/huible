"""Encrypted per-tenant BYOK key vault (HU-2243 Sprint 3)

Revision ID: 006_byok_key_vault
Revises: 005_usage_key_source
Create Date: 2026-08-31

Founder directive (Pat, 2026-08-30) part (3): "BYOK — design hook for
clients to supply their own provider key; per-tenant key vault, usage
attribution, graceful fallback to house key." Sprint 2 landed the
per-request ``X-Provider-Key`` hook; this table is the durable registry —
a tenant registers its provider key once (``PUT /api/v1/byok/keys/{provider}``)
and every subsequent chat turn runs on it, with usage attributed to the
tenant's own bearer-key digest (``llm_usage.api_key_id`` /
``key_source='byok'``).

Storage posture (see huible.api.byok_vault):

* ``key_ciphertext`` — AES-256-GCM sealed blob
  (``v1.<salt>.<nonce>.<ct+tag>``); the row-level key derives from
  ``BYOK_VAULT_MASTER_KEY`` via scrypt with a per-row random salt, and the
  (``api_key_id``, ``provider``) pair is the AES-GCM AAD so rows cannot be
  copied between tenants or providers without failing decryption. The raw
  provider key is never persisted or logged.
* ``api_key_id`` — SHA-256 digest of the caller's bearer key (same
  attribution id as ``llm_usage``), never the raw bearer key.
* ``key_fingerprint`` — non-secret SHA-256/16 of the raw provider key, the
  only value any endpoint ever returns.
* UNIQUE (``api_key_id``, ``provider``) — one sealed key per tenant per
  provider; re-registering overwrites (upsert).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "006_byok_key_vault"
down_revision: str | None = "005_usage_key_source"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE byok_keys (
            id              BIGSERIAL PRIMARY KEY,
            api_key_id      VARCHAR(64) NOT NULL,
            provider        VARCHAR(32) NOT NULL,
            key_ciphertext  TEXT NOT NULL,
            key_fingerprint VARCHAR(64) NOT NULL,
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_byok_keys_tenant_provider UNIQUE (api_key_id, provider)
        );
    """)
    op.execute("CREATE INDEX idx_byok_keys_tenant ON byok_keys (api_key_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_byok_keys_tenant")
    op.execute("DROP TABLE byok_keys")

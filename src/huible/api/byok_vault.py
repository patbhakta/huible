"""Encrypted per-tenant BYOK key vault (HU-2243 Sprint 3, directive part 3).

Founder directive (Pat, 2026-08-30): "BYOK — design hook for clients to
supply their own provider key; **per-tenant key vault**, usage attribution,
graceful fallback to house key." Sprint 2 landed the per-request hook
(``X-Provider-Key``); this module is the durable half — clients register
their provider key once and every subsequent chat turn runs on it.

Design (mirrors :mod:`huible.api.metering`, the §7.4 durability pattern):

* **Envelope encryption, one master secret** — ``BYOK_VAULT_MASTER_KEY``
  derives a per-row AES-256-GCM key via scrypt (random 16-byte salt per
  row) and seals the raw provider key with the caller's attribution id +
  provider as AES-GCM AAD: tampering with a row or moving it between
  tenants/providers fails the decrypt. The stored ``key_ciphertext`` is
  ``v1.<b64 salt>.<b64 nonce>.<b64 ciphertext+tag>`` — raw provider keys
  are never persisted, logged, or returned by any endpoint (only a
  SHA-256 ``key_fingerprint`` for confirmation).
* **Sync SQLAlchemy Postgres backend** (``byok_keys`` table, migration
  006) on the same sync-safety-DB posture as the metering recorder;
  in-memory default for key-free dev/test.
* **Vault disabled by default** — no master key configured → no vault
  (endpoints 403, resolver skips the vault leg). Same default-off posture
  as ``BYOK_ENABLED`` and the kill switch.

Turn resolution order (see ``huible.api.app._resolve_turn_llm``):
request header key → vault key for the caller's digest → house key
(dedicated product key when configured, else shared). Attribution is
always the caller's own bearer-key digest with ``key_source='byok'``.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import (
    BigInteger,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    delete,
    select,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    sessionmaker,
)

logger = logging.getLogger(__name__)

__all__ = [
    "BYOK_PROVIDERS",
    "ByokCipher",
    "ByokVault",
    "ByokVaultError",
    "InMemoryByokVault",
    "PostgresByokVault",
    "provider_key_fingerprint",
]


#: Providers a tenant may register a key for (the real hosted voices).
BYOK_PROVIDERS = frozenset({"zai", "openrouter", "gemini"})

_SALT_BYTES = 16
_NONCE_BYTES = 12
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1


class ByokVaultError(RuntimeError):
    """A vault operation failed (cipher misconfiguration or tamper)."""


def provider_key_fingerprint(raw_provider_key: str) -> str:
    """Stable non-secret fingerprint of a raw provider key (SHA-256/16)."""
    return hashlib.sha256(raw_provider_key.encode("utf-8")).hexdigest()[:16]


class ByokCipher:
    """AES-256-GCM seal/open for vault rows under one master secret.

    Key derivation is scrypt (random per-row salt) so two identical provider
    keys stored for different tenants produce unrelated ciphertexts, and a
    leaked database without the master secret stays cryptographically
    closed. The AAD binds each row to its (tenant digest, provider) so a
    copied/moved row fails authentication instead of decrypting.
    """

    def __init__(self, master_secret: str) -> None:
        if not master_secret or not master_secret.strip():
            raise ByokVaultError(
                "BYOK vault requires BYOK_VAULT_MASTER_KEY to be set "
                "(non-empty secret; generate e.g. `openssl rand -hex 32`)."
            )
        self._secret = master_secret.strip().encode("utf-8")

    def _derive(self, salt: bytes) -> bytes:
        return hashlib.scrypt(
            self._secret, salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
        )

    def encrypt(self, plaintext: str, *, aad: str) -> str:
        salt = os.urandom(_SALT_BYTES)
        nonce = os.urandom(_NONCE_BYTES)
        key = self._derive(salt)
        sealed = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), aad.encode())
        b64 = base64.urlsafe_b64encode
        return f"v1.{b64(salt).decode()}.{b64(nonce).decode()}.{b64(sealed).decode()}"

    def decrypt(self, blob: str, *, aad: str) -> str:
        try:
            version, salt_s, nonce_s, sealed_s = blob.split(".", 3)
            if version != "v1":
                raise ByokVaultError(f"unknown vault blob version {version!r}")
            b64d = base64.urlsafe_b64decode
            key = self._derive(b64d(salt_s))
            plaintext = AESGCM(key).decrypt(
                b64d(nonce_s), b64d(sealed_s), aad.encode()
            )
        except InvalidTag as exc:
            raise ByokVaultError(
                "vault ciphertext failed authentication (tampered row or "
                "wrong master key)"
            ) from exc
        except (ValueError, TypeError) as exc:
            raise ByokVaultError(f"malformed vault ciphertext: {exc}") from exc
        return plaintext.decode("utf-8")


@dataclass(slots=True)
class ByokKeyRecord:
    """One vault row (never carries the raw key)."""

    api_key_id: str
    provider: str
    key_fingerprint: str
    updated_at: datetime


@runtime_checkable
class ByokVault(Protocol):
    """Pluggable vault backend (store / fetch / delete by tenant+provider)."""

    def store(self, api_key_id: str, provider: str, raw_provider_key: str) -> str:
        """Seal and upsert the key; returns the new fingerprint."""
        ...

    def fetch(self, api_key_id: str, provider: str) -> str | None:
        """Open and return the raw key, or ``None`` when absent."""
        ...

    def delete(self, api_key_id: str, provider: str) -> bool:
        """Remove the row; returns whether one existed."""
        ...

    def list_keys(self, api_key_id: str) -> list[ByokKeyRecord]:
        """The tenant's registered providers + fingerprints (no raw keys)."""
        ...


# --- in-memory backend ---------------------------------------------------------


class InMemoryByokVault:
    """Deterministic in-memory vault (key-free dev/test, still encrypted)."""

    def __init__(self, cipher: ByokCipher) -> None:
        self._cipher = cipher
        self._rows: dict[tuple[str, str], tuple[str, str, datetime]] = {}

    def _aad(self, api_key_id: str, provider: str) -> str:
        return f"{api_key_id}:{provider}"

    def store(self, api_key_id: str, provider: str, raw_provider_key: str) -> str:
        fingerprint = provider_key_fingerprint(raw_provider_key)
        blob = self._cipher.encrypt(raw_provider_key, aad=self._aad(api_key_id, provider))
        self._rows[(api_key_id, provider)] = (blob, fingerprint, datetime.now(UTC))
        return fingerprint

    def fetch(self, api_key_id: str, provider: str) -> str | None:
        row = self._rows.get((api_key_id, provider))
        if row is None:
            return None
        blob, _fingerprint, _updated = row
        return self._cipher.decrypt(blob, aad=self._aad(api_key_id, provider))

    def delete(self, api_key_id: str, provider: str) -> bool:
        return self._rows.pop((api_key_id, provider), None) is not None

    def list_keys(self, api_key_id: str) -> list[ByokKeyRecord]:
        return [
            ByokKeyRecord(
                api_key_id=key_id,
                provider=provider,
                key_fingerprint=fingerprint,
                updated_at=updated,
            )
            for (key_id, provider), (_blob, fingerprint, updated) in sorted(
                self._rows.items()
            )
            if key_id == api_key_id
        ]


# --- durable Postgres backend --------------------------------------------------


class VaultBase(DeclarativeBase):
    """ORM base for the vault table (``byok_keys``, migration 006)."""


class ByokRow(VaultBase):
    """Durable sealed provider-key row.

    Portable types (String/Integer) so the test suite runs against SQLite;
    the Alembic migration declares the production Postgres types. Same
    convention as :mod:`huible.api.metering`.
    """

    __tablename__ = "byok_keys"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    api_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    key_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    key_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        UniqueConstraint("api_key_id", "provider", name="uq_byok_keys_tenant_provider"),
        Index("idx_byok_keys_tenant", "api_key_id"),
    )


class PostgresByokVault:
    """Durable :class:`ByokVault` backend (sync SQLAlchemy + Postgres)."""

    def __init__(
        self,
        master_secret: str,
        database_url: str,
        *,
        engine: Any | None = None,
        pool_size: int = 5,
        max_overflow: int = 10,
    ) -> None:
        self._cipher = ByokCipher(master_secret)
        # ``engine`` injectable so tests point the vault at a pre-created
        # (sqlite) engine — production passes only the URL.
        self._engine = engine or create_engine(
            database_url,
            pool_size=pool_size,
            max_overflow=max_overflow,
            future=True,
        )
        self._session_factory = sessionmaker(
            self._engine,
            class_=Session,
            expire_on_commit=False,
        )

    def close(self) -> None:
        self._engine.dispose()

    def _aad(self, api_key_id: str, provider: str) -> str:
        return f"{api_key_id}:{provider}"

    def store(self, api_key_id: str, provider: str, raw_provider_key: str) -> str:
        fingerprint = provider_key_fingerprint(raw_provider_key)
        blob = self._cipher.encrypt(raw_provider_key, aad=self._aad(api_key_id, provider))
        with self._session_factory() as session:
            existing = session.execute(
                select(ByokRow).where(
                    ByokRow.api_key_id == api_key_id, ByokRow.provider == provider
                )
            ).scalar_one_or_none()
            if existing is not None:
                existing.key_ciphertext = blob
                existing.key_fingerprint = fingerprint
                existing.updated_at = datetime.now(UTC)
            else:
                session.add(
                    ByokRow(
                        api_key_id=api_key_id,
                        provider=provider,
                        key_ciphertext=blob,
                        key_fingerprint=fingerprint,
                    )
                )
            session.commit()
        return fingerprint

    def fetch(self, api_key_id: str, provider: str) -> str | None:
        with self._session_factory() as session:
            row = session.execute(
                select(ByokRow).where(
                    ByokRow.api_key_id == api_key_id, ByokRow.provider == provider
                )
            ).scalar_one_or_none()
            if row is None:
                return None
        # Decrypt outside the session; a tampered row raises
        # ByokVaultError which the resolver treats as vault-miss.
        return self._cipher.decrypt(
            row.key_ciphertext, aad=self._aad(api_key_id, provider)
        )

    def delete(self, api_key_id: str, provider: str) -> bool:
        with self._session_factory() as session:
            result = session.execute(
                delete(ByokRow).where(
                    ByokRow.api_key_id == api_key_id, ByokRow.provider == provider
                )
            )
            session.commit()
            return bool(result.rowcount)

    def list_keys(self, api_key_id: str) -> list[ByokKeyRecord]:
        with self._session_factory() as session:
            rows = (
                session.execute(
                    select(ByokRow)
                    .where(ByokRow.api_key_id == api_key_id)
                    .order_by(ByokRow.provider)
                )
                .scalars()
                .all()
            )
            return [
                ByokKeyRecord(
                    api_key_id=row.api_key_id,
                    provider=row.provider,
                    key_fingerprint=row.key_fingerprint,
                    updated_at=row.updated_at,
                )
                for row in rows
            ]

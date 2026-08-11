"""Persona-scoped bearer auth for the Huible REST API.

Implements section 2 of ``docs/07-api-specification.md``:

* API keys are **persona-scoped** — a single key grants access to exactly one
  persona's memory graph.
* Missing / invalid ``Authorization`` header -> ``401 AUTH_REQUIRED``.
* Valid key but wrong persona scope (the request targets a different
  ``persona_id``) -> ``403 FORBIDDEN``.

The :class:`ApiKeyStore` is an injectable interface so the production path can
swap in a DB-backed ``api_keys`` table without touching the route layer. The
default :class:`InMemoryApiKeyStore` is seedable for tests and reads the
comma-separated ``API_KEYS`` env var (``"key1:persona-uuid,key2:persona-uuid"``)
at construction time, matching the ``.env.example`` contract.

A :class:`PersonaRegistry` resolves a ``persona_id`` to the
``(PersonaConfig, MemoryBackend)`` pair the chat path needs, plus the
per-request requester :class:`RelationshipTier`. It too is an injectable
interface; the default in-memory registry is seedable for tests.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, status

from huible.memory.protocol import MemoryBackend
from huible.persona.context import PersonaConfig, RelationshipTier

logger = logging.getLogger(__name__)

__all__ = [
    "ApiKeyPrincipal",
    "ApiKeyStore",
    "InMemoryApiKeyStore",
    "InMemoryPersonaRegistry",
    "PersonaBinding",
    "PersonaRegistry",
    "authenticate",
    "get_api_key_store",
    "get_persona_registry",
    "raise_auth_required",
    "raise_forbidden",
]

#: Scheme expected in the ``Authorization`` header.
BEARER_SCHEME = "Bearer"


@dataclass(slots=True, frozen=True)
class ApiKeyPrincipal:
    """The authenticated caller resolved from a bearer API key.

    ``persona_id`` is the persona this key is scoped to. A request targeting a
    different ``persona_id`` is a scope mismatch (403).
    """

    api_key: str
    persona_id: UUID


def raise_auth_required() -> None:
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "error": {
                "code": "AUTH_REQUIRED",
                "status": 401,
                "message": "Missing or invalid Authorization header",
            }
        },
    )


def raise_forbidden() -> None:
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "error": {
                "code": "FORBIDDEN",
                "status": 403,
                "message": "API key does not grant access to this persona",
            }
        },
    )


@runtime_checkable
class ApiKeyStore(Protocol):
    """Resolve a raw API key string to a scoped principal."""

    def resolve(self, api_key: str) -> ApiKeyPrincipal | None: ...


class InMemoryApiKeyStore:
    """Default key store: a dict of ``api_key -> persona_id``.

    Seedable at construction (``keys``) and mutable at runtime (``register``)
    so tests and the future admin CLI share one shape. Reads ``API_KEYS`` from
    the environment when ``read_env`` is true and no explicit ``keys`` collide.
    """

    def __init__(
        self,
        keys: dict[str, UUID] | None = None,
        *,
        read_env: bool = True,
        env: Any | None = None,
    ) -> None:
        self._keys: dict[str, UUID] = {}
        if read_env:
            env = env if env is not None else os.environ
            self._merge_env(env.get("API_KEYS", ""))
        if keys:
            self._keys.update(keys)

    def _merge_env(self, raw: str) -> None:
        """Parse ``key:persona-uuid,key2:persona-uuid`` from the env value."""
        if not raw:
            return
        for entry in raw.split(","):
            entry = entry.strip()
            if not entry:
                continue
            if ":" not in entry:
                logger.warning("Ignoring malformed API_KEYS entry (no ':'): %r", entry)
                continue
            key, _, persona_raw = entry.partition(":")
            key = key.strip()
            persona_raw = persona_raw.strip()
            if not key or not persona_raw:
                continue
            try:
                persona_id = UUID(persona_raw)
            except ValueError:
                logger.warning("Ignoring API_KEYS entry with non-UUID persona: %r", persona_raw)
                continue
            self._keys[key] = persona_id

    def register(self, api_key: str, persona_id: UUID) -> None:
        self._keys[api_key] = persona_id

    def resolve(self, api_key: str) -> ApiKeyPrincipal | None:
        persona_id = self._keys.get(api_key)
        if persona_id is None:
            return None
        return ApiKeyPrincipal(api_key=api_key, persona_id=persona_id)


@dataclass(slots=True, frozen=True)
class PersonaBinding:
    """Everything the chat path needs to speak as a persona.

    ``requester_tier`` is the relationship tier the caller is asserting for the
    turn (resolved from the request's ``disclosure_tier`` field, defaulting to
    ``FAMILY`` per the spec).
    """

    persona: PersonaConfig
    backend: MemoryBackend
    requester_tier: RelationshipTier


@runtime_checkable
class PersonaRegistry(Protocol):
    """Resolve a persona id + requester tier to the chat wiring."""

    def get(self, persona_id: UUID, requester_tier: RelationshipTier) -> PersonaBinding | None: ...


class InMemoryPersonaRegistry:
    """Default registry mapping ``persona_id -> (PersonaConfig, MemoryBackend)``.

    The requester tier is supplied per-request by the route layer, so a single
    persona can be addressed at different disclosure levels. Seedable at
    construction (``personas``) and mutable at runtime (``register``).
    """

    def __init__(
        self,
        personas: dict[UUID, tuple[PersonaConfig, MemoryBackend]] | None = None,
    ) -> None:
        self._personas: dict[UUID, tuple[PersonaConfig, MemoryBackend]] = {}
        if personas:
            self._personas.update(personas)

    def register(self, persona: PersonaConfig, backend: MemoryBackend) -> None:
        self._personas[persona.id] = (persona, backend)

    def get(
        self, persona_id: UUID, requester_tier: RelationshipTier
    ) -> PersonaBinding | None:
        entry = self._personas.get(persona_id)
        if entry is None:
            return None
        persona, backend = entry
        return PersonaBinding(
            persona=persona, backend=backend, requester_tier=requester_tier
        )


def get_api_key_store(request: Request) -> ApiKeyStore:
    """FastAPI dependency: the app-wide API key store from ``app.state``."""
    return request.app.state.api_key_store


def get_persona_registry(request: Request) -> PersonaRegistry:
    """FastAPI dependency: the app-wide persona registry from ``app.state``."""
    return request.app.state.persona_registry


def _parse_bearer(authorization: str | None) -> str:
    """Extract the bearer token from an ``Authorization`` header.

    Raises ``401`` when the header is missing, not the Bearer scheme, or empty.
    """
    if not authorization:
        raise_auth_required()
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0] != BEARER_SCHEME or not parts[1].strip():
        raise_auth_required()
    return parts[1].strip()


async def authenticate(
    authorization: str | None = Header(default=None),
    store: ApiKeyStore = Depends(get_api_key_store),
) -> ApiKeyPrincipal:
    """FastAPI dependency: bearer auth -> scoped principal.

    Raises ``401 AUTH_REQUIRED`` for a missing header or unknown key.
    """
    token = _parse_bearer(authorization)
    principal = store.resolve(token)
    if principal is None:
        raise_auth_required()
    return principal

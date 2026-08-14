"""Smoke tests for the Huible FastAPI server skeleton (HU-1403).

Covers the HU-1403 acceptance criteria:

- ``GET /health`` returns 200 JSON with service + version + DB/pgvector
  connectivity status, via ``httpx.AsyncClient`` + ASGI transport (no network).
- ``GET /api/v1/health`` returns the same payload (alias).
- The bare module-level app boots and ``/health`` returns 200 without seeds.
- DB/pgvector checks report ``skipped`` when no backend is wired and surface
  live status when one is; overall status goes ``degraded`` when the DB is down.
- Settings defaults are key-free (mock generator / fake embeddings) and the
  effective DB URL ignores a foreign (non-asyncpg) ``DATABASE_URL``.
- CORS preflight is answered.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from huible.api.app import app, create_app
from huible.api.settings import Settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeBackend:
    """Minimal backend exposing only ``health_check`` + ``close``."""

    def __init__(self, status: dict[str, str]) -> None:
        self._status = status

    async def health_check(self) -> dict[str, str]:
        return self._status

    async def close(self) -> None:  # pragma: no cover - trivial
        return None


@asynccontextmanager
async def _client(application: FastAPI) -> AsyncIterator[AsyncClient]:
    """An ``httpx.AsyncClient`` bound to the app via ASGI (no real network)."""
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.fixture
def application():
    return create_app(start_time=0.0)


# ---------------------------------------------------------------------------
# /health (top-level)
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    async def test_health_returns_200_with_service_version_and_checks(self, application):
        async with _client(application) as client:
            r = await client.get("/health")
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["status"] == "ok"
        assert data["version"]
        # Key-free default: no DB wired -> both DB checks skipped.
        assert data["checks"]["database"] == "skipped"
        assert data["checks"]["pgvector"] == "skipped"
        assert data["checks"]["generator"] == "ready (mock)"
        assert data["uptime_seconds"] >= 0.0

    async def test_versioned_health_is_alias_of_top_level(self, application):
        async with _client(application) as client:
            top = await client.get("/health")
            v1 = await client.get("/api/v1/health")
        # Same keys/values (uptime may differ by a tick -> compare the rest).
        top_data, v1_data = top.json()["data"], v1.json()["data"]
        assert top_data["status"] == v1_data["status"]
        assert top_data["version"] == v1_data["version"]
        assert top_data["checks"] == v1_data["checks"]

    async def test_bare_module_app_health_boots_without_seeds(self):
        async with _client(app) as client:
            r = await client.get("/health")
        assert r.status_code == 200
        assert r.json()["data"]["status"] == "ok"


# ---------------------------------------------------------------------------
# DB / pgvector probe wiring
# ---------------------------------------------------------------------------


class TestHealthProbe:
    async def test_live_backend_surfaces_db_and_pgvector_status(self, application):
        application.state.memory_backend = _FakeBackend({"database": "ok", "pgvector": "ok"})
        async with _client(application) as client:
            r = await client.get("/health")
        data = r.json()["data"]
        assert data["checks"]["database"] == "ok"
        assert data["checks"]["pgvector"] == "ok"
        assert data["status"] == "ok"

    async def test_unhealthy_backend_marks_status_degraded(self, application):
        application.state.memory_backend = _FakeBackend(
            {"database": "unhealthy", "pgvector": "unknown"}
        )
        async with _client(application) as client:
            r = await client.get("/health")
        data = r.json()["data"]
        assert data["checks"]["database"] == "unhealthy"
        assert data["status"] == "degraded"

    async def test_missing_pgvector_extension_is_reported(self, application):
        application.state.memory_backend = _FakeBackend({"database": "ok", "pgvector": "missing"})
        async with _client(application) as client:
            r = await client.get("/health")
        data = r.json()["data"]
        assert data["checks"]["pgvector"] == "missing"
        # DB reachable -> service still ok.
        assert data["status"] == "ok"


# ---------------------------------------------------------------------------
# Settings (key-free defaults, foreign-DB guard)
# ---------------------------------------------------------------------------


class TestSettings:
    def test_defaults_are_key_free(self):
        s = Settings()
        assert s.generator_provider == "mock"
        assert s.embedding_provider == "fake"

    def test_blank_embedding_provider_falls_back_to_fake(self):
        s = Settings(embedding_provider="")
        assert s.embedding_provider == "fake"

    def test_env_example_parses_without_comment_pollution(self):
        # python-dotenv keeps an inline ``# comment`` as the literal value when
        # the assignment is empty (``VAR=   # comment``), which silently
        # overrides defaults (HU-1644 pre-flight false-reds). The template must
        # keep comments on their own lines.
        from pathlib import Path

        from dotenv import dotenv_values

        example = Path(__file__).resolve().parents[2] / ".env.example"
        values = dotenv_values(example)
        polluted = {k: v for k, v in values.items() if v and v.strip().startswith("#")}
        assert polluted == {}

    def test_effective_database_url_ignores_foreign_postgres_scheme(self):
        # A plain postgres:// control-plane URL must not be treated as Huible's.
        s = Settings(database_url="postgres://paperclip:pw@127.0.0.1:5432/paperclip")
        assert s.effective_database_url == ""

    def test_effective_database_url_accepts_asyncpg_scheme(self):
        url = "postgresql+asyncpg://huible:pw@localhost:5432/huible"
        s = Settings(database_url=url)
        assert s.effective_database_url == url

    def test_to_generator_config_defaults_to_mock(self):
        cfg = Settings().to_generator_config()
        from huible.persona.generator import GeneratorProvider

        assert cfg.provider is GeneratorProvider.MOCK

    def test_cors_origins_list_parses_comma_separated(self):
        s = Settings(cors_origins="https://a.example.com, https://b.example.com")
        assert s.cors_origins_list == [
            "https://a.example.com",
            "https://b.example.com",
        ]


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------


class TestCors:
    async def test_preflight_returns_allow_origin(self):
        application = create_app(
            settings=Settings(cors_origins="https://app.example.com"),
            start_time=0.0,
        )
        async with _client(application) as client:
            r = await client.options(
                "/health",
                headers={
                    "Origin": "https://app.example.com",
                    "Access-Control-Request-Method": "GET",
                },
            )
        assert r.status_code == 200
        assert r.headers["access-control-allow-origin"] == "https://app.example.com"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

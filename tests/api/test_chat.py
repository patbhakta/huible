"""Tests for ``huible.api.app`` — FastAPI server + POST /api/v1/chat.

Covers the HU-1401 acceptance criteria:

- ``GET /api/v1/health`` returns 200 with a ``data`` envelope.
- ``POST /api/v1/chat`` returns a grounded persona reply.
- Unauthenticated request -> ``401 AUTH_REQUIRED``; bad key -> ``401``.
- Valid key but wrong persona scope (``persona_id`` mismatch) -> ``403 FORBIDDEN``.
- Contamination guard: LOW / QUARANTINE memories never appear in the reply's
  ``activated_memories`` source set, **and** never enter the prompt sent to the
  generator (provenance-safe bridge, HU-1399).
- Disclosure scoping (INV-DS): an acquaintance never receives a private memory.
- ``conversation_id`` is echoed / minted.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from huible.api.app import _embed, create_app
from huible.api.auth import InMemoryApiKeyStore, InMemoryPersonaRegistry
from huible.memory.protocol import (
    ContentType,
    DisclosureScope,
    MemoryEdge,
    MemoryNode,
    MemoryTier,
    SearchResult,
    SourceType,
)
from huible.persona.context import CONFIDENCE_LEVEL_METADATA_KEY, PersonaConfig
from huible.persona.generator import MockPersonaGeneratorClient

PERSONA_ID = uuid4()
OTHER_PERSONA_ID = uuid4()
API_KEY = "key-chandler-family"
OTHER_API_KEY = "key-other-persona"


# ---------------------------------------------------------------------------
# Minimal memory backend (real cosine similarity on content vectors)
# ---------------------------------------------------------------------------


class _FakeBackend:
    """In-memory backend; ``search_by_content`` ranks by dot product."""

    def __init__(self) -> None:
        self._memories: dict[UUID, MemoryNode] = {}
        self._vectors: list[tuple[list[float], UUID]] = []

    def seed(self, node: MemoryNode) -> None:
        """Synchronous seed helper for test setup (bypasses async store)."""
        self._memories[node.id] = node
        if node.embedding_content:
            self._vectors.append((node.embedding_content, node.id))

    async def store_memory(self, node: MemoryNode) -> UUID:
        self.seed(node)
        return node.id

    async def get_memory(self, memory_id: UUID) -> MemoryNode | None:
        return self._memories.get(memory_id)

    async def search_by_content(
        self,
        persona_id: UUID,
        query_embedding: list[float],
        top_k: int = 20,
        disclosure_scope: DisclosureScope | None = None,
    ) -> list[SearchResult]:
        results: list[SearchResult] = []
        for vec, node_id in self._vectors:
            node = self._memories[node_id]
            if node.persona_id != persona_id:
                continue
            dot = sum(q * e for q, e in zip(query_embedding, vec, strict=False))
            if dot > 0.0:
                results.append(SearchResult(node=node, score=dot))
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]

    async def search_by_sensory(self, *a: Any, **k: Any) -> list[SearchResult]:
        return []

    async def search_by_affect(self, *a: Any, **k: Any) -> list[SearchResult]:
        return []

    async def get_edges(self, memory_id: UUID) -> list[MemoryEdge]:
        return []


def _node(
    *,
    content: str,
    confidence_level: str,
    disclosure_scope: DisclosureScope = DisclosureScope.FAMILY,
    memory_date: date | None = date(2015, 7, 15),
    embedding: list[float] | None = None,
) -> MemoryNode:
    metadata: dict[str, Any] = {CONFIDENCE_LEVEL_METADATA_KEY: confidence_level}
    return MemoryNode(
        id=uuid4(),
        persona_id=PERSONA_ID,
        tier=MemoryTier.ACCRUED,
        content=content,
        content_type=ContentType.NARRATIVE,
        embedding_content=list(embedding) if embedding is not None else None,
        memory_date=memory_date,
        source_type=SourceType.EXTRACTION,
        disclosure_scope=disclosure_scope,
        metadata=metadata,
    )


def _persona(
    persona_id: UUID = PERSONA_ID,
    name: str = "Chandler",
    boundary: str = "2024-12-01",
) -> PersonaConfig:
    return PersonaConfig(
        id=persona_id,
        name=name,
        voice_instructions="Warm Texas storyteller.",
        era_knowledge_boundary=boundary,
        age_at_death=72,
        death_date="2024-12-01",
    )


def _seeded_backend() -> _FakeBackend:
    """Backend with HIGH/MEDIUM/LOW/QUARANTINE memories all matching 'fishing'."""
    backend = _FakeBackend()
    vec = _embed("fishing lake")
    for content, confidence in [
        ("Chandler loved fishing on Lake Travis.", "high"),
        ("He kept his rods in the garage.", "medium"),
        ("Maybe he once fished the Gulf.", "low"),
        ("A disputed claim he fished daily.", "quarantine"),
    ]:
        backend.seed(_node(content=content, confidence_level=confidence, embedding=vec))
    return backend


def _make_app(
    *,
    persona_id: UUID = PERSONA_ID,
    generator: MockPersonaGeneratorClient | None = None,
) -> tuple[TestClient, MockPersonaGeneratorClient]:
    backend = _seeded_backend()
    gen = generator or MockPersonaGeneratorClient(persona_name="Chandler")
    persona = _persona(persona_id)
    registry = InMemoryPersonaRegistry({persona.id: (persona, backend)})
    keys = InMemoryApiKeyStore(
        {API_KEY: persona_id, OTHER_API_KEY: OTHER_PERSONA_ID}, read_env=False
    )
    application = create_app(
        api_key_store=keys,
        persona_registry=registry,
        generator=gen,
        start_time=0.0,
    )
    return TestClient(application), gen


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class TestHealth:
    def test_health_returns_200_with_data_envelope(self):
        client, _ = _make_app()
        r = client.get("/api/v1/health")
        assert r.status_code == 200
        body = r.json()
        assert body["data"]["status"] == "ok"
        # No DB wired by _make_app() -> DB/pgvector checks report "skipped"
        # while the service stays overall "ok" (key-free default, HU-1403).
        assert body["data"]["checks"]["database"] == "skipped"
        assert body["data"]["checks"]["pgvector"] == "skipped"
        assert "generator" in body["data"]["checks"]
        assert body["data"]["version"]

    def test_bare_app_health_boots_without_seeds(self):
        from huible.api.app import app

        r = TestClient(app).get("/api/v1/health")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Auth: 401 / 403
# ---------------------------------------------------------------------------


class TestAuth:
    def test_no_authorization_header_returns_401(self):
        client, _ = _make_app()
        r = client.post("/api/v1/chat", json={"message": "tell me about fishing"})
        assert r.status_code == 401
        assert r.json()["detail"]["error"]["code"] == "AUTH_REQUIRED"

    def test_unknown_key_returns_401(self):
        client, _ = _make_app()
        r = client.post(
            "/api/v1/chat",
            json={"message": "hi"},
            headers={"Authorization": "Bearer bogus"},
        )
        assert r.status_code == 401
        assert r.json()["detail"]["error"]["code"] == "AUTH_REQUIRED"

    def test_non_bearer_scheme_returns_401(self):
        client, _ = _make_app()
        r = client.post(
            "/api/v1/chat",
            json={"message": "hi"},
            headers={"Authorization": "Basic abc"},
        )
        assert r.status_code == 401

    def test_wrong_persona_scope_returns_403(self):
        client, _ = _make_app()
        r = client.post(
            "/api/v1/chat",
            json={"message": "hi", "persona_id": str(OTHER_PERSONA_ID)},
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        assert r.status_code == 403
        assert r.json()["detail"]["error"]["code"] == "FORBIDDEN"

    def test_key_scoped_to_other_persona_cannot_reach_default(self):
        """A key for persona B explicitly requesting persona A is 403."""
        client, _ = _make_app()
        r = client.post(
            "/api/v1/chat",
            json={"message": "hi", "persona_id": str(PERSONA_ID)},
            headers={"Authorization": f"Bearer {OTHER_API_KEY}"},
        )
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# Happy path + contamination guard
# ---------------------------------------------------------------------------


class TestChatHappyPath:
    def test_chat_returns_reply_and_activated_memories(self):
        client, gen = _make_app()
        r = client.post(
            "/api/v1/chat",
            json={"message": "tell me about fishing on the lake"},
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["reply"]
        # Generator was called exactly once with a rendered prompt.
        assert len(gen.calls) == 1
        # Only HIGH/MEDIUM memories surfaced.
        confidences = {m["confidence_level"] for m in data["activated_memories"]}
        assert confidences <= {"high", "medium"}
        assert len(data["activated_memories"]) == 2  # one HIGH + one MEDIUM
        # LOW/QUARANTINE were excluded (contamination guard fired).
        assert data["exclusion_counts"].get("confidence_low") == 1
        assert data["exclusion_counts"].get("confidence_quarantine") == 1

    def test_chat_passes_only_provenance_safe_memory_to_generator(self):
        """The prompt sent to the generator must not contain LOW/QUARANTINE text."""
        client, gen = _make_app()
        client.post(
            "/api/v1/chat",
            json={"message": "tell me about fishing on the lake"},
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        prompt = gen.calls[0]
        assert "Lake Travis" in prompt  # HIGH admitted
        assert "kept his rods" in prompt  # MEDIUM admitted
        assert "fished the Gulf" not in prompt  # LOW excluded
        assert "fished daily" not in prompt  # QUARANTINE excluded

    def test_conversation_id_echoed_when_provided(self):
        client, _ = _make_app()
        r = client.post(
            "/api/v1/chat",
            json={"message": "fishing", "conversation_id": "conv-42"},
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        assert r.status_code == 200
        assert r.json()["data"]["conversation_id"] == "conv-42"

    def test_conversation_id_minted_when_absent(self):
        client, _ = _make_app()
        r = client.post(
            "/api/v1/chat",
            json={"message": "fishing"},
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        assert r.status_code == 200
        assert r.json()["data"]["conversation_id"]


# ---------------------------------------------------------------------------
# Disclosure scoping (INV-DS) via disclosure_tier request field
# ---------------------------------------------------------------------------


class TestDisclosureScoping:
    def test_acquaintance_never_sees_private_memory(self):
        """An acquaintance requester must not receive private memories.

        We seed a private HIGH memory and assert that with
        ``disclosure_tier=all_contacts`` it does not surface in the reply source
        set nor in the generator prompt.
        """
        from datetime import date as _date

        backend = _FakeBackend()
        vec = _embed("fishing lake")

        private = _node(
            content="Chandler's secret fishing spot was private.",
            confidence_level="high",
            disclosure_scope=DisclosureScope.PRIVATE,
            embedding=vec,
            memory_date=_date(2015, 7, 1),
        )
        public = _node(
            content="Chandler fished Lake Travis often.",
            confidence_level="high",
            disclosure_scope=DisclosureScope.ALL_CONTACTS,
            embedding=vec,
            memory_date=_date(2015, 7, 2),
        )
        backend.seed(private)
        backend.seed(public)

        gen = MockPersonaGeneratorClient(persona_name="Chandler")
        persona = _persona()
        registry = InMemoryPersonaRegistry({persona.id: (persona, backend)})
        keys = InMemoryApiKeyStore({API_KEY: PERSONA_ID}, read_env=False)
        application = create_app(
            api_key_store=keys, persona_registry=registry, generator=gen, start_time=0.0
        )
        client = TestClient(application)

        r = client.post(
            "/api/v1/chat",
            json={
                "message": "fishing on the lake",
                "disclosure_tier": "all_contacts",
            },
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        contents = [m["content"] for m in data["activated_memories"]]
        assert "Chandler fished Lake Travis often." in contents
        assert all("secret" not in c for c in contents)
        # Defense in depth: the private memory is excluded — either by
        # retrieval's disclosure filter (layer 1) or the context builder's
        # second-pass disclosure gate (layer 2). Either way it must not reach
        # the generator prompt or the reply source set.
        assert "secret fishing spot" not in gen.calls[0]
        assert all(m["disclosure_scope"] != "private" for m in data["activated_memories"])


# ---------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_empty_message_rejected(self):
        client, _ = _make_app()
        r = client.post(
            "/api/v1/chat",
            json={"message": ""},
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        assert r.status_code == 422  # pydantic min_length

    def test_invalid_disclosure_tier_rejected(self):
        client, _ = _make_app()
        r = client.post(
            "/api/v1/chat",
            json={"message": "hi", "disclosure_tier": "world"},
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"]["code"] == "VALIDATION_ERROR"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

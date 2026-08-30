"""Tests for ``huible.api.app`` — FastAPI server + POST /api/v1/chat/{persona_id}.

Covers the HU-1401 acceptance criteria against the **single** persona chat
surface (HU-1926 consolidation: the generic ``POST /api/v1/chat`` is a 308
redirect shim onto this route — its behavior is covered in
``test_chat_surface_consolidation.py``):

- ``GET /api/v1/health`` returns 200 with a ``data`` envelope.
- ``POST /api/v1/chat/{persona_id}`` returns a grounded persona reply.
- Unauthenticated request -> ``401 AUTH_REQUIRED``; bad key -> ``401``.
- Valid key but wrong persona scope (path ``persona_id`` mismatch) -> ``403 FORBIDDEN``.
- Contamination guard: LOW / QUARANTINE memories never appear in the trace's
  ``activated_memories`` source set, **and** never enter the prompt sent to
  the LLM (provenance-safe bridge, HU-1399).
- Disclosure scoping (INV-DS): an acquaintance never receives a private memory.
- ``conversation_id`` is echoed / minted on the trace.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from huible.api.app import _embed, create_app
from huible.api.auth import InMemoryApiKeyStore, InMemoryPersonaRegistry
from huible.llm.client import FakeLLMClient
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
) -> tuple[TestClient, FakeLLMClient]:
    backend = _seeded_backend()
    llm = FakeLLMClient(persona_name="Chandler")
    persona = _persona(persona_id)
    registry = InMemoryPersonaRegistry({persona.id: (persona, backend)})
    keys = InMemoryApiKeyStore(
        {API_KEY: persona_id, OTHER_API_KEY: OTHER_PERSONA_ID}, read_env=False
    )
    application = create_app(
        api_key_store=keys,
        persona_registry=registry,
        llm_client=llm,
        start_time=0.0,
    )
    return TestClient(application), llm


def _consent(client: TestClient, conv: str) -> str:
    """Pre-consent a session so the persona path under test runs (G6).

    The consent gate itself is exercised in test_chat_consent.py; this suite
    covers the post-consent retrieval/generation path.
    """
    r = client.post(
        f"/api/v1/chat/{PERSONA_ID}/consent",
        json={"conversation_id": conv},
        headers={"Authorization": f"Bearer {API_KEY}"},
    )
    assert r.status_code == 200, r.text
    return conv


def _chat(
    client: TestClient,
    *,
    message: str,
    conv: str,
    payload: dict[str, Any] | None = None,
):
    body = {"message": message, "conversation_id": conv}
    if payload:
        body.update(payload)
    return client.post(
        f"/api/v1/chat/{PERSONA_ID}",
        json=body,
        headers={"Authorization": f"Bearer {API_KEY}"},
    )


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
        r = client.post(f"/api/v1/chat/{PERSONA_ID}", json={"message": "tell me about fishing"})
        assert r.status_code == 401
        assert r.json()["detail"]["error"]["code"] == "AUTH_REQUIRED"

    def test_unknown_key_returns_401(self):
        client, _ = _make_app()
        r = client.post(
            f"/api/v1/chat/{PERSONA_ID}",
            json={"message": "hi"},
            headers={"Authorization": "Bearer bogus"},
        )
        assert r.status_code == 401
        assert r.json()["detail"]["error"]["code"] == "AUTH_REQUIRED"

    def test_non_bearer_scheme_returns_401(self):
        client, _ = _make_app()
        r = client.post(
            f"/api/v1/chat/{PERSONA_ID}",
            json={"message": "hi"},
            headers={"Authorization": "Basic abc"},
        )
        assert r.status_code == 401

    def test_wrong_persona_scope_returns_403(self):
        client, _ = _make_app()
        r = client.post(
            f"/api/v1/chat/{OTHER_PERSONA_ID}",
            json={"message": "hi"},
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        assert r.status_code == 403
        assert r.json()["detail"]["error"]["code"] == "FORBIDDEN"

    def test_key_scoped_to_other_persona_cannot_reach_default(self):
        """A key for persona B hitting persona A's route is 403."""
        client, _ = _make_app()
        r = client.post(
            f"/api/v1/chat/{PERSONA_ID}",
            json={"message": "hi"},
            headers={"Authorization": f"Bearer {OTHER_API_KEY}"},
        )
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# Happy path + contamination guard
# ---------------------------------------------------------------------------


class TestChatHappyPath:
    def test_chat_returns_reply_and_activated_memories(self):
        client, llm = _make_app()
        conv = _consent(client, "conv-happy")
        r = _chat(client, message="tell me about fishing on the lake", conv=conv)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["response"]
        # LLM was called exactly once with a rendered prompt.
        assert len(llm.calls) == 1
        trace = body["trace"]
        # Only HIGH/MEDIUM memories surfaced.
        confidences = {m["confidence_level"] for m in trace["activated_memories"]}
        assert confidences <= {"high", "medium"}
        assert len(trace["activated_memories"]) == 2  # one HIGH + one MEDIUM
        # LOW/QUARANTINE were excluded (contamination guard fired).
        assert trace["exclusion_counts"].get("confidence_low") == 1
        assert trace["exclusion_counts"].get("confidence_quarantine") == 1

    def test_chat_passes_only_provenance_safe_memory_to_generator(self):
        """The prompt sent to the LLM must not contain LOW/QUARANTINE text."""
        client, llm = _make_app()
        conv = _consent(client, "conv-prompt")
        _chat(client, message="tell me about fishing on the lake", conv=conv)
        prompt = llm.calls[0][0]
        assert "Lake Travis" in prompt  # HIGH admitted
        assert "kept his rods" in prompt  # MEDIUM admitted
        assert "fished the Gulf" not in prompt  # LOW excluded
        assert "fished daily" not in prompt  # QUARANTINE excluded

    def test_conversation_id_echoed_when_provided(self):
        client, _ = _make_app()
        conv = _consent(client, "conv-42")
        r = _chat(client, message="fishing", conv=conv)
        assert r.status_code == 200
        assert r.json()["trace"]["conversation_id"] == "conv-42"

    def test_conversation_id_minted_when_absent(self):
        client, _ = _make_app()
        # No conversation_id on the first turn: the consent 409 carries the
        # minted session id, and the acknowledged retry reuses it.
        r = client.post(
            f"/api/v1/chat/{PERSONA_ID}",
            json={"message": "fishing"},
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        assert r.status_code == 409
        minted = r.json()["detail"]["error"]["conversation_id"]
        assert minted
        _consent(client, minted)
        r2 = client.post(
            f"/api/v1/chat/{PERSONA_ID}",
            json={"message": "fishing", "conversation_id": minted},
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        assert r2.status_code == 200
        assert r2.json()["trace"]["conversation_id"] == minted


# ---------------------------------------------------------------------------
# Disclosure scoping (INV-DS) via the relationship request field
# ---------------------------------------------------------------------------


class TestDisclosureScoping:
    def test_acquaintance_never_sees_private_memory(self):
        """An acquaintance requester must not receive private memories.

        We seed a private HIGH memory and assert that with
        ``relationship=acquaintance`` it does not surface in the reply source
        set nor in the LLM prompt.
        """
        backend = _FakeBackend()
        vec = _embed("fishing lake")

        private = _node(
            content="Chandler's secret fishing spot was private.",
            confidence_level="high",
            disclosure_scope=DisclosureScope.PRIVATE,
            embedding=vec,
            memory_date=date(2015, 7, 1),
        )
        public = _node(
            content="Chandler fished Lake Travis often.",
            confidence_level="high",
            disclosure_scope=DisclosureScope.ALL_CONTACTS,
            embedding=vec,
            memory_date=date(2015, 7, 2),
        )
        backend.seed(private)
        backend.seed(public)

        llm = FakeLLMClient(persona_name="Chandler")
        persona = _persona()
        registry = InMemoryPersonaRegistry({persona.id: (persona, backend)})
        keys = InMemoryApiKeyStore({API_KEY: PERSONA_ID}, read_env=False)
        application = create_app(
            api_key_store=keys, persona_registry=registry, llm_client=llm, start_time=0.0
        )
        client = TestClient(application)
        conv = _consent(client, "conv-ds")

        r = _chat(
            client,
            message="fishing on the lake",
            conv=conv,
            payload={"relationship": "acquaintance"},
        )
        assert r.status_code == 200, r.text
        trace = r.json()["trace"]
        contents = [m["content"] for m in trace["activated_memories"]]
        assert "Chandler fished Lake Travis often." in contents
        assert all("secret" not in c for c in contents)
        # Defense in depth: the private memory is excluded — either by
        # retrieval's disclosure filter (layer 1) or the context builder's
        # second-pass disclosure gate (layer 2). Either way it must not reach
        # the LLM prompt or the reply source set.
        assert "secret fishing spot" not in llm.calls[0][0]
        assert all(m["disclosure_scope"] != "private" for m in trace["activated_memories"])


# ---------------------------------------------------------------------------
# Per-persona reply budgets (HU-2231)
# ---------------------------------------------------------------------------


class TestReplyBudget:
    """The chat call site must hand the generator the persona's own
    corpus-derived cap, not the global ceiling (regression: the global
    64-token cap clipped long-winded personas before HU-2231)."""

    def _budget(self, persona: PersonaConfig) -> dict[str, Any]:
        llm = FakeLLMClient(persona_name=persona.name)
        registry = InMemoryPersonaRegistry({persona.id: (persona, _seeded_backend())})
        keys = InMemoryApiKeyStore({API_KEY: persona.id}, read_env=False)
        application = create_app(
            api_key_store=keys, persona_registry=registry, llm_client=llm, start_time=0.0
        )
        client = TestClient(application)
        conv = _consent(client, "conv-budget")
        r = _chat(client, message="tell me about fishing", conv=conv)
        assert r.status_code == 200, r.text
        assert llm.kwargs_calls, "generator was never called"
        return llm.kwargs_calls[0]

    def test_persona_without_stats_gets_global_default_cap(self):
        from huible.api.settings import Settings

        budget = self._budget(_persona())
        assert budget["max_tokens"] == Settings().persona_chat_max_tokens

    def test_chandler_register_keeps_verified_64_token_cap(self):
        from huible.persona.length import CHANDLER_GROUND_TRUTH

        persona = _persona()
        persona = PersonaConfig(
            id=persona.id,
            name=persona.name,
            voice_instructions=persona.voice_instructions,
            era_knowledge_boundary=persona.era_knowledge_boundary,
            age_at_death=persona.age_at_death,
            death_date=persona.death_date,
            length_stats=CHANDLER_GROUND_TRUTH,
        )
        assert self._budget(persona)["max_tokens"] == 64

    def test_long_winded_persona_cap_is_not_clipped_to_global(self):
        from huible.persona.length import CorpusLengthStats

        talkative = PersonaConfig(
            id=PERSONA_ID,
            name="Storyteller",
            voice_instructions="Warm Texas storyteller.",
            era_knowledge_boundary="2024-12-01",
            length_stats=CorpusLengthStats(
                median_chars=420, p75_chars=640, p90_chars=900, sample_lines=400
            ),
        )
        assert self._budget(talkative)["max_tokens"] == 441


# ---------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_empty_message_rejected(self):
        client, _ = _make_app()
        r = client.post(
            f"/api/v1/chat/{PERSONA_ID}",
            json={"message": ""},
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        assert r.status_code == 422  # pydantic min_length

    def test_invalid_relationship_rejected(self):
        client, _ = _make_app()
        r = client.post(
            f"/api/v1/chat/{PERSONA_ID}",
            json={"message": "hi", "relationship": "world"},
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"]["code"] == "VALIDATION_ERROR"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

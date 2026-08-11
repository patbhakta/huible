"""End-to-end test for ``POST /api/v1/chat/{persona_id}`` (HU-1406).

This is the Phase-1 "Make It Speak" integration milestone: the first real
text-in -> memory-retrieval -> LLM -> text-out path. It exercises the full
wiring through the FastAPI app:

    inbound message
      -> ContextBuilder (provenance-safe memory -> prompt bridge, HU-1399)
      -> FakeLLMClient (HU-1405 fake provider; key-free, deterministic)
      -> response + structured trace

The committed test uses the fake provider so CI is deterministic and key-free
per the issue's acceptance criteria. It proves:

* The end-to-end path returns a non-empty persona response.
* The trace only ever reports ``canonical`` / ``derived`` provenance tiers —
  the provenance firewall (HU-1399 / HU-1404) drops LOW and QUARANTINE
  confidence memories before the LLM sees them, so they never reach the trace.
* Defense in depth: the LOW / QUARANTINE memory text never enters the prompt
  handed to the LLM either.
* Disclosure scoping (INV-DS): an acquaintance relationship never receives a
  private memory.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID, uuid4

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
API_KEY = "key-chandler-family"


# ---------------------------------------------------------------------------
# Minimal in-memory backend (real dot-product ranking on content vectors)
# ---------------------------------------------------------------------------


class _FakeBackend:
    """In-memory backend; ``search_by_content`` ranks by dot product.

    Mirrors the test backend in ``tests/api/test_chat.py`` so retrieval produces
    real cosine-style activation over a seeded memory set.
    """

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
    tier: MemoryTier,
    confidence_level: str,
    disclosure_scope: DisclosureScope = DisclosureScope.FAMILY,
    memory_date: date | None = date(2015, 7, 15),
    embedding: list[float] | None = None,
) -> MemoryNode:
    metadata: dict[str, Any] = {CONFIDENCE_LEVEL_METADATA_KEY: confidence_level}
    return MemoryNode(
        id=uuid4(),
        persona_id=PERSONA_ID,
        tier=tier,
        content=content,
        content_type=ContentType.NARRATIVE,
        embedding_content=list(embedding) if embedding is not None else None,
        memory_date=memory_date,
        source_type=SourceType.EXTRACTION,
        disclosure_scope=disclosure_scope,
        metadata=metadata,
    )


def _persona() -> PersonaConfig:
    return PersonaConfig(
        id=PERSONA_ID,
        name="Chandler",
        voice_instructions="Warm Texas storyteller.",
        era_knowledge_boundary="2024-12-01",
        age_at_death=72,
        death_date="2024-12-01",
    )


def _seeded_backend() -> tuple[_FakeBackend, dict[str, MemoryNode]]:
    """Backend seeded with canonical/derived + low/quarantine memories.

    All four memories match the ``"fishing lake"`` query vector so retrieval
    surfaces every one of them; the provenance firewall then must drop the LOW
    and QUARANTINE entries before the LLM sees them.
    """
    backend = _FakeBackend()
    vec = _embed("fishing lake")
    memories: dict[str, MemoryNode] = {}

    # Admissible: HIGH confidence, canonical provenance.
    memories["canonical_high"] = _node(
        content="Chandler loved fishing on Lake Travis.",
        tier=MemoryTier.CANONICAL,
        confidence_level="high",
        embedding=vec,
    )
    # Admissible: MEDIUM confidence, derived provenance.
    memories["derived_medium"] = _node(
        content="He kept his rods in the garage.",
        tier=MemoryTier.DERIVED,
        confidence_level="medium",
        embedding=vec,
    )
    # Excluded by the provenance firewall: LOW confidence.
    memories["low_excluded"] = _node(
        content="Maybe he once fished the Gulf.",
        tier=MemoryTier.DERIVED,
        confidence_level="low",
        embedding=vec,
    )
    # Excluded by the provenance firewall: QUARANTINE confidence.
    memories["quarantine_excluded"] = _node(
        content="A disputed claim he fished daily.",
        tier=MemoryTier.CANONICAL,
        confidence_level="quarantine",
        embedding=vec,
    )

    for node in memories.values():
        backend.seed(node)
    return backend, memories


def _make_app(
    backend: _FakeBackend | None = None,
    *,
    llm: FakeLLMClient | None = None,
) -> tuple[TestClient, FakeLLMClient, dict[str, MemoryNode]]:
    if backend is not None:
        seeded_backend, memories = backend, {}
    else:
        seeded_backend, memories = _seeded_backend()
    fake_llm = llm or FakeLLMClient(persona_name="Chandler")
    persona = _persona()
    registry = InMemoryPersonaRegistry({persona.id: (persona, seeded_backend)})
    keys = InMemoryApiKeyStore({API_KEY: PERSONA_ID}, read_env=False)
    application = create_app(
        api_key_store=keys,
        persona_registry=registry,
        llm_client=fake_llm,
        start_time=0.0,
    )
    return TestClient(application), fake_llm, memories


def _consent(client: TestClient, conv: str = "sess-e2e") -> str:
    """Pre-consent a session so the persona path under test runs.

    The G6 reality-framing / consent gate is exercised in test_chat_consent.py;
    this suite covers the post-consent retrieval/generation path (HU-1406).
    """
    client.post(
        f"/api/v1/chat/{PERSONA_ID}/consent",
        json={"conversation_id": conv},
        headers={"Authorization": f"Bearer {API_KEY}"},
    )
    return conv


# ---------------------------------------------------------------------------
# Headline: text -> retrieval -> LLM -> text
# ---------------------------------------------------------------------------


class TestPersonaChatEndToEnd:
    def test_full_path_returns_response_and_trace(self):
        """Text-in -> retrieval -> LLM -> text-out works end to end."""
        client, llm, _memories = _make_app()
        conv = _consent(client)

        r = client.post(
            f"/api/v1/chat/{PERSONA_ID}",
            json={"message": "tell me about fishing on the lake", "conversation_id": conv},
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # Top-level contract (HU-1406): response + trace, no data envelope.
        assert body["response"]
        assert body["response"].startswith("[fake-llm:")  # FakeLLMClient stamp
        trace = body["trace"]
        assert trace["provider"] == "fake"
        # The LLM was invoked exactly once with a rendered prompt.
        assert len(llm.calls) == 1

    def test_trace_contains_only_canonical_and_derived_tiers(self):
        """The trace must report only canonical/derived provenance tiers.

        LOW and QUARANTINE confidence memories are dropped by the provenance
        firewall (HU-1399) before generation, so their tiers never surface.
        """
        client, _llm, memories = _make_app()
        conv = _consent(client)

        r = client.post(
            f"/api/v1/chat/{PERSONA_ID}",
            json={"message": "tell me about fishing on the lake", "conversation_id": conv},
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        assert r.status_code == 200, r.text
        trace = r.json()["trace"]

        assert set(trace["provenance_tiers"]) <= {"canonical", "derived"}
        assert set(trace["provenance_tiers"]) == {"canonical", "derived"}

        # memory_refs are exactly the two admissible memories.
        admitted_ids = {
            str(memories["canonical_high"].id),
            str(memories["derived_medium"].id),
        }
        excluded_ids = {
            str(memories["low_excluded"].id),
            str(memories["quarantine_excluded"].id),
        }
        assert set(trace["memory_refs"]) == admitted_ids
        assert set(trace["memory_refs"]).isdisjoint(excluded_ids)

    def test_provenance_firewall_blocks_low_and_quarantine_from_llm_prompt(self):
        """Defense in depth: LOW/QUARANTINE text never reaches the LLM prompt."""
        client, llm, _memories = _make_app()
        conv = _consent(client)

        client.post(
            f"/api/v1/chat/{PERSONA_ID}",
            json={"message": "tell me about fishing on the lake", "conversation_id": conv},
            headers={"Authorization": f"Bearer {API_KEY}"},
        )

        prompt, _system = llm.calls[0]
        # Admissible memories are grounded into the prompt.
        assert "Lake Travis" in prompt  # canonical HIGH
        assert "kept his rods" in prompt  # derived MEDIUM
        # LOW / QUARANTINE are hard-excluded by the context builder.
        assert "fished the Gulf" not in prompt  # LOW
        assert "fished daily" not in prompt  # QUARANTINE

    def test_acquaintance_relationship_does_not_leak_private_memory(self):
        """An acquaintance requester must not receive a private memory (INV-DS)."""
        backend = _FakeBackend()
        vec = _embed("fishing lake")
        public = _node(
            content="Chandler fished Lake Travis often.",
            tier=MemoryTier.CANONICAL,
            confidence_level="high",
            disclosure_scope=DisclosureScope.ALL_CONTACTS,
            embedding=vec,
            memory_date=date(2015, 7, 2),
        )
        private = _node(
            content="Chandler's secret fishing spot was private.",
            tier=MemoryTier.DERIVED,
            confidence_level="high",
            disclosure_scope=DisclosureScope.PRIVATE,
            embedding=vec,
            memory_date=date(2015, 7, 1),
        )
        backend.seed(public)
        backend.seed(private)

        client, llm, _memories = _make_app(backend=backend)
        conv = _consent(client)

        r = client.post(
            f"/api/v1/chat/{PERSONA_ID}",
            json={
                "message": "fishing on the lake",
                "relationship": "acquaintance",
                "conversation_id": conv,
            },
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        assert r.status_code == 200, r.text
        trace = r.json()["trace"]

        # The private memory must not be in the trace refs...
        assert str(private.id) not in set(trace["memory_refs"])
        assert str(public.id) in set(trace["memory_refs"])
        # ...nor in the prompt handed to the LLM (defense in depth).
        prompt, _system = llm.calls[0]
        assert "secret fishing spot" not in prompt
        assert "fished Lake Travis often" in prompt


# ---------------------------------------------------------------------------
# Auth + validation guards on the integration path
# ---------------------------------------------------------------------------


class TestPersonaChatGuards:
    def test_missing_auth_returns_401(self):
        client, _llm, _memories = _make_app()
        r = client.post(
            f"/api/v1/chat/{PERSONA_ID}",
            json={"message": "hi"},
        )
        assert r.status_code == 401
        assert r.json()["detail"]["error"]["code"] == "AUTH_REQUIRED"

    def test_path_persona_mismatch_returns_403(self):
        """A valid key scoped to persona A hitting persona B's path is 403."""
        client, _llm, _memories = _make_app()
        other = uuid4()
        r = client.post(
            f"/api/v1/chat/{other}",
            json={"message": "hi"},
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        assert r.status_code == 403
        assert r.json()["detail"]["error"]["code"] == "FORBIDDEN"

    def test_invalid_relationship_returns_400(self):
        client, _llm, _memories = _make_app()
        r = client.post(
            f"/api/v1/chat/{PERSONA_ID}",
            json={"message": "hi", "relationship": "stranger"},
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"]["code"] == "VALIDATION_ERROR"

    def test_empty_message_rejected(self):
        client, _llm, _memories = _make_app()
        r = client.post(
            f"/api/v1/chat/{PERSONA_ID}",
            json={"message": ""},
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        assert r.status_code == 422  # pydantic min_length


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])

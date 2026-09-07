"""M1.4 scoped-lane chat-path tests (HU-2732).

Covers ``POST /api/v1/chat/{persona_id}`` integration for the M1.4 context +
tools acceptance:

- current-events / emotion / career shaped turns ground the reply in the
  persona's own era-admissible vault lines (scoped content types) and record
  the scoped read on the trace (``trace.scoped_reads``) — live tool calls with
  observable results, never fabricated ones;
- the knowledge boundary is enforceable: a post-boundary-dated vault atom can
  never render through a scoped lane even when the lane fires (era gate), and
  a PRIVATE atom stays inaccessible to a FAMILY requester (disclosure gate);
- disabled flags keep the pre-M1.4 prompt shape and an empty
  ``trace.scoped_reads``.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from huible.api.app import _embed, create_app
from huible.api.auth import InMemoryApiKeyStore, InMemoryPersonaRegistry
from huible.api.settings import Settings
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
from huible.safety import InMemoryRiskProfile

PERSONA_ID = uuid4()
API_KEY = "key-chandler-scoped-reads"
CONV = "sess-scoped-reads"


# ---------------------------------------------------------------------------
# Fixtures (mirrors tests/api/test_chat_caretaker.py)
# ---------------------------------------------------------------------------


class _FakeBackend:
    """In-memory backend; ``search_by_content`` ranks by dot product."""

    def __init__(self) -> None:
        self._memories: dict[Any, MemoryNode] = {}
        self._vectors: list[tuple[list[float], Any]] = []

    def seed(self, node: MemoryNode) -> None:
        self._memories[node.id] = node
        if node.embedding_content:
            self._vectors.append((node.embedding_content, node.id))

    async def store_memory(self, node: MemoryNode) -> Any:
        self.seed(node)
        return node.id

    async def get_memory(self, memory_id: Any) -> MemoryNode | None:
        return self._memories.get(memory_id)

    async def search_by_content(
        self,
        persona_id: Any,
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

    async def get_edges(self, memory_id: Any) -> list[MemoryEdge]:
        return []

    async def get_active_memories(
        self, persona_id: Any, limit: int = 200
    ) -> list[MemoryNode]:
        """Grounding-corpus scan path (HU-2070); ranking is irrelevant here."""
        return [n for n in self._memories.values() if n.persona_id == persona_id][
            :limit
        ]


def _node(
    *,
    content: str,
    content_type: ContentType = ContentType.NARRATIVE,
    disclosure_scope: DisclosureScope = DisclosureScope.FAMILY,
    memory_date: Any = None,
    embedding: list[float] | None = None,
) -> MemoryNode:
    return MemoryNode(
        id=uuid4(),
        persona_id=PERSONA_ID,
        tier=MemoryTier.CANONICAL,
        content=content,
        content_type=content_type,
        embedding_content=embedding if embedding is not None else _embed(content),
        memory_date=memory_date,
        source_type=SourceType.EXTRACTION,
        disclosure_scope=disclosure_scope,
        metadata={CONFIDENCE_LEVEL_METADATA_KEY: "high"},
    )


def _persona() -> PersonaConfig:
    return PersonaConfig(
        id=PERSONA_ID,
        name="Chandler",
        era_knowledge_boundary="2024-12-01",
    )


class _StubWorkingMemory:
    def __init__(self) -> None:
        self.recalls: list[tuple[str, str]] = []
        self.captures: list[tuple[str, str, str]] = []

    async def recall(self, session_key: str, query: str):
        self.recalls.append((session_key, query))

        class _R:
            context = ""
            strategy = ""
            chars = 0

            @classmethod
            def empty(cls):  # pragma: no cover - shape parity
                return cls()

        return _R()

    async def capture(self, session_key: str, user_content: str, assistant_content: str) -> bool:
        self.captures.append((session_key, user_content, assistant_content))
        return True


def _make_app(
    *,
    backend: _FakeBackend | None = None,
    settings: Settings | None = None,
) -> tuple[TestClient, FakeLLMClient]:
    seeded_backend = backend or _FakeBackend()
    fake_llm = FakeLLMClient(persona_name="Chandler")
    persona = _persona()
    registry = InMemoryPersonaRegistry({persona.id: (persona, seeded_backend)})
    keys = InMemoryApiKeyStore({API_KEY: PERSONA_ID}, read_env=False)
    application = create_app(
        api_key_store=keys,
        persona_registry=registry,
        llm_client=fake_llm,
        risk_profile=InMemoryRiskProfile(),
        settings=settings,
        start_time=0.0,
    )
    return TestClient(application), fake_llm


def _consent(client: TestClient) -> None:
    r = client.post(
        f"/api/v1/chat/{PERSONA_ID}/consent",
        json={"conversation_id": CONV},
        headers={"Authorization": f"Bearer {API_KEY}"},
    )
    assert r.status_code == 200, r.text


def _post(client: TestClient, message: str) -> dict[str, Any]:
    r = client.post(
        f"/api/v1/chat/{PERSONA_ID}",
        json={"message": message, "conversation_id": CONV},
        headers={"Authorization": f"Bearer {API_KEY}"},
    )
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
# Scoped lanes: grounded + traced
# ---------------------------------------------------------------------------


class TestScopedLanesGroundAndTrace:
    def test_current_events_turn_grounded_and_traced(self):
        backend = _FakeBackend()
        backend.seed(
            _node(
                content="event — is: the office won the league trivia night.",
                content_type=ContentType.NARRATIVE,
                embedding=_embed("what's going on in the world?"),
            )
        )
        client, llm = _make_app(backend=backend)
        _consent(client)
        body = _post(client, "so what's going on in the world?")

        scoped = {r["section"]: r["lines"] for r in body["trace"]["scoped_reads"]}
        assert scoped.get("current_events", 0) >= 1
        prompt, _system = llm.calls[-1]
        assert "IN YOUR WORLD" in prompt
        assert "[WORLD] the office won the league trivia night." in prompt

    def test_emotion_turn_grounded_and_traced(self):
        backend = _FakeBackend()
        backend.seed(
            _node(
                content="relationship — is: he calls Janice when he panics.",
                content_type=ContentType.RELATIONSHIP,
                embedding=_embed("do you ever feel lonely?"),
            )
        )
        client, llm = _make_app(backend=backend)
        _consent(client)
        body = _post(client, "be honest — do you ever feel lonely?")

        scoped = {r["section"]: r["lines"] for r in body["trace"]["scoped_reads"]}
        assert scoped.get("emotion", 0) >= 1
        prompt, _system = llm.calls[-1]
        assert "HOW YOU FEEL" in prompt
        assert "[FEELING] he calls Janice when he panics." in prompt

    def test_career_turn_grounded_and_traced(self):
        backend = _FakeBackend()
        backend.seed(
            _node(
                content="fact — is: nobody knows what he does at his job.",
                content_type=ContentType.FACT,
                embedding=_embed("how's work?"),
            )
        )
        client, llm = _make_app(backend=backend)
        _consent(client)
        body = _post(client, "how's work?")

        scoped = {r["section"]: r["lines"] for r in body["trace"]["scoped_reads"]}
        assert scoped.get("career", 0) >= 1
        prompt, _system = llm.calls[-1]
        assert "YOUR WORK" in prompt
        assert "[WORK] nobody knows what he does at his job." in prompt

    def test_non_matching_turn_has_no_scoped_reads(self):
        client, _llm = _make_app()
        _consent(client)
        body = _post(client, "hey you guys!")
        assert body["trace"]["scoped_reads"] == []


# ---------------------------------------------------------------------------
# Enforceable knowledge boundary
# ---------------------------------------------------------------------------


class TestKnowledgeBoundary:
    def test_post_boundary_atom_never_renders_through_lane(self):
        """The era gate is the enforceable boundary: even when the lane fires
        (shape matches, similarity clears the floor), a post-boundary-dated
        atom can never reach the prompt — the lane stays empty rather than
        fabricating (B2)."""
        backend = _FakeBackend()
        backend.seed(
            _node(
                content="narrative — is: he watched the 2025 championship live.",
                content_type=ContentType.NARRATIVE,
                memory_date=date(2025, 6, 1),  # past the 2024-12-01 boundary
                embedding=_embed("what's going on in the world?"),
            )
        )
        client, llm = _make_app(backend=backend)
        _consent(client)
        body = _post(client, "so what's going on in the world?")

        assert body["trace"]["scoped_reads"] == []
        prompt, _system = llm.calls[-1]
        assert "IN YOUR WORLD" not in prompt
        assert "2025 championship" not in prompt

    def test_private_atom_inaccessible_to_family_requester(self):
        """Disclosure gate: a PRIVATE atom never reaches a FAMILY-tier
        requester through a scoped lane (unrelated/private sources remain
        inaccessible)."""
        backend = _FakeBackend()
        backend.seed(
            _node(
                content="fact — is: a private work shame he never tells anyone.",
                content_type=ContentType.FACT,
                disclosure_scope=DisclosureScope.PRIVATE,
                embedding=_embed("how's work?"),
            )
        )
        client, llm = _make_app(backend=backend)
        _consent(client)
        body = _post(client, "how's work?")

        assert body["trace"]["scoped_reads"] == []
        prompt, _system = llm.calls[-1]
        assert "private work shame" not in prompt


# ---------------------------------------------------------------------------
# Disabled flags keep the pre-M1.4 shape
# ---------------------------------------------------------------------------


class TestDisabledFlags:
    def test_disabled_current_events_flag_keeps_prompt_shape(self):
        settings = Settings(current_events_tool_enabled=False)
        backend = _FakeBackend()
        backend.seed(
            _node(
                content="narrative — is: the office won the league trivia night.",
                content_type=ContentType.NARRATIVE,
                embedding=_embed("what's going on in the world?"),
            )
        )
        client, llm = _make_app(backend=backend, settings=settings)
        _consent(client)
        body = _post(client, "so what's going on in the world?")
        assert body["trace"]["scoped_reads"] == []
        prompt, _system = llm.calls[-1]
        assert "IN YOUR WORLD" not in prompt

    def test_disabled_scoped_reads_flag_keeps_prompt_shape(self):
        settings = Settings(scoped_vault_reads_enabled=False)
        backend = _FakeBackend()
        backend.seed(
            _node(
                content="fact — is: nobody knows what he does at his job.",
                content_type=ContentType.FACT,
                embedding=_embed("how's work?"),
            )
        )
        client, llm = _make_app(backend=backend, settings=settings)
        _consent(client)
        body = _post(client, "how's work?")
        assert body["trace"]["scoped_reads"] == []
        prompt, _system = llm.calls[-1]
        assert "YOUR WORK" not in prompt

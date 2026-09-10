"""HU-2774 conversation write-back chat-path tests.

Covers the cross-session recall lane on ``POST /api/v1/chat/{persona_id}``:

- with the lane enabled, each completed persona turn is persisted into the
  persona-scoped retrieval backend as an ACCRUED ``conversation`` memory
  (user words verbatim + reply, medium confidence, FAMILY scope);
- the disabled default (and budget-fallback replies) store nothing;
- a failing backend never breaks the chat turn (returns 200);
- the telemetry line carries the write-back state.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from huible.api.app import (
    WORKING_MEMORY_SERVICE_ID_METADATA_KEY,
    _writeback_conversation_memory,
    create_app,
)
from huible.api.auth import InMemoryApiKeyStore, InMemoryPersonaRegistry
from huible.api.settings import Settings
from huible.conversation import InMemoryMemoryBackend
from huible.embeddings import reset_embedder_cache
from huible.llm.client import FakeLLMClient
from huible.memory.protocol import MemoryNode, MemoryTier, SourceType
from huible.persona.context import PersonaConfig
from huible.persona.working_memory import TencentWorkingMemory

PERSONA_ID = uuid4()
API_KEY = "key-writeback"
CONV = "writeback-test"


@pytest.fixture(autouse=True)
def _legacy_embedder(monkeypatch):
    """Hermetic embedder: the host .env arms local_onnx (fastembed, absent in
    the test venv); the write-back lane only needs the deterministic legacy
    token-hash vectors. Reset the process caches so the flip takes effect."""
    monkeypatch.setenv("EMBEDDING_PROVIDER", "legacy")
    from huible.api.settings import get_settings

    get_settings.cache_clear()
    reset_embedder_cache()
    yield
    get_settings.cache_clear()
    reset_embedder_cache()


def _make_app(
    *, writeback_enabled: bool, backend: InMemoryMemoryBackend | None = None
):
    persona = PersonaConfig(
        id=PERSONA_ID,
        name="Chandler",
        era_knowledge_boundary="2024-12-01",
    )
    registry = InMemoryPersonaRegistry(
        {persona.id: (persona, backend or InMemoryMemoryBackend())}
    )
    keys = InMemoryApiKeyStore({API_KEY: PERSONA_ID}, read_env=False)
    application = create_app(
        api_key_store=keys,
        persona_registry=registry,
        llm_client=FakeLLMClient(persona_name="Chandler"),
        settings=Settings(
            working_memory_enabled=False,
            conversation_writeback_enabled=writeback_enabled,
        ),
        start_time=0.0,
    )
    return TestClient(application), registry


def _consent(client: TestClient) -> None:
    r = client.post(
        f"/api/v1/chat/{PERSONA_ID}/consent",
        json={"conversation_id": CONV},
        headers={"Authorization": f"Bearer {API_KEY}"},
    )
    assert r.status_code == 200, r.text


def _persona_memories(registry: InMemoryPersonaRegistry) -> list[MemoryNode]:
    backend = registry._personas[PERSONA_ID][1]
    return [
        m for m in backend.memories.values()
        if m.source_type == SourceType.CONVERSATION
    ]


def test_enabled_lane_persists_turn_as_conversation_memory() -> None:
    client, registry = _make_app(writeback_enabled=True)
    _consent(client)
    r = client.post(
        f"/api/v1/chat/{PERSONA_ID}",
        json={"message": "hey, you free tonight?", "conversation_id": CONV},
        headers={"Authorization": f"Bearer {API_KEY}"},
    )
    assert r.status_code == 200, r.text

    memories = _persona_memories(registry)
    # First turn stores TWO: the exchange memory + the episodic ordinal index
    # ("the first thing <user> said to <persona> was: ...") that makes
    # cross-session recall probes semantically findable.
    assert len(memories) == 2
    index = [m for m in memories if m.metadata.get("kind") == "conversation_index"]
    exchange = [m for m in memories if "kind" not in m.metadata]
    assert len(index) == 1 and len(exchange) == 1
    assert "the first thing" in index[0].content
    assert "hey, you free tonight?" in index[0].content
    node = exchange[0]
    assert node.tier == MemoryTier.ACCRUED
    assert node.source_type == SourceType.CONVERSATION
    # The user's words ride verbatim (cross-session "first thing I said"
    # recall must semantically match the turn that holds them).
    assert "hey, you free tonight?" in node.content
    assert node.embedding_content, "write-back memory must be retrievable"
    assert node.metadata["confidence_level"] == "medium"
    assert node.metadata["conversation_id"] == CONV
    assert node.memory_date is None  # never era-gated
    assert node.disclosure_scope.value == "close_friends"


def test_disabled_default_stores_nothing() -> None:
    client, registry = _make_app(writeback_enabled=False)
    _consent(client)
    r = client.post(
        f"/api/v1/chat/{PERSONA_ID}",
        json={"message": "hey", "conversation_id": CONV},
        headers={"Authorization": f"Bearer {API_KEY}"},
    )
    assert r.status_code == 200, r.text
    assert _persona_memories(registry) == []


def test_failing_backend_never_breaks_the_turn() -> None:
    class _ExplodingBackend(InMemoryMemoryBackend):
        async def store_memory(self, node: MemoryNode):
            raise RuntimeError("disk on fire")

    client, registry = _make_app(
        writeback_enabled=True, backend=_ExplodingBackend()
    )
    _consent(client)
    r = client.post(
        f"/api/v1/chat/{PERSONA_ID}",
        json={"message": "hey", "conversation_id": CONV},
        headers={"Authorization": f"Bearer {API_KEY}"},
    )
    # The lane degrades: the turn survives, nothing is persisted.
    assert r.status_code == 200, r.text
    assert _persona_memories(registry) == []


def test_helper_unit_skip_and_missing_backend() -> None:
    app = FastAPI()
    persona = PersonaConfig(id=PERSONA_ID, name="Monica")
    kwargs = dict(
        persona=persona,
        persona_id=PERSONA_ID,
        backend=None,
        user_message="hi",
        persona_reply="hello",
        conversation_id=CONV,
    )
    loop = asyncio.new_event_loop()
    try:
        # Disabled setting -> False (not an error).
        app.state.settings = Settings(conversation_writeback_enabled=False)
        assert loop.run_until_complete(
            _writeback_conversation_memory(app, **kwargs)
        ) is False
        # Enabled but no backend -> None (failure code).
        app.state.settings = Settings(conversation_writeback_enabled=True)
        assert loop.run_until_complete(
            _writeback_conversation_memory(app, **kwargs)
        ) is None
        # skip (budget fallback) -> False even when the lane is enabled.
        assert loop.run_until_complete(
            _writeback_conversation_memory(app, skip=True, **kwargs)
        ) is False
    finally:
        loop.close()


def test_wm_service_id_metadata_key_wired() -> None:
    """The isolation key exists and the Tencent client clones per service."""
    client = TencentWorkingMemory(
        "http://127.0.0.1:1", service_id="default", timeout_s=1
    )
    scoped = client.with_service_id("huible-chandler")
    assert scoped is not client
    assert client._service_id == "default"
    assert scoped._service_id == "huible-chandler"
    assert WORKING_MEMORY_SERVICE_ID_METADATA_KEY == "working_memory_service_id"

"""W4 working-memory chat-path wiring tests (HU-2309 v1.8 §1.7.2 / M-0R-B).

Covers the ``POST /api/v1/chat/{persona_id}`` integration of the TencentDB
Arm A lane:

- recall lands in the generator prompt (WORKING MEMORY section) on an
  enabled lane, and the trace carries the observability view;
- the completed turn (inbound message + post-guard reply) is captured back
  to the store, keyed by the (persona, conversation)-scoped session key;
- the disabled lane (default) leaves the prompt and trace pre-W4;
- a degraded recall (empty payload) never breaks the turn.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from huible.api.app import create_app
from huible.api.auth import InMemoryApiKeyStore, InMemoryPersonaRegistry
from huible.conversation import InMemoryMemoryBackend
from huible.llm.client import FakeLLMClient
from huible.persona.context import PersonaConfig
from huible.persona.working_memory import (
    ARM_A_STRATEGY,
    NullWorkingMemory,
    WorkingMemoryRecall,
    working_memory_session_key,
)

PERSONA_ID = uuid4()
API_KEY = "key-working-memory"
CONV = "demo-wm-test"


class _StubWorkingMemory:
    """Deterministic in-test lane: fixed recall payload, recorded captures."""

    def __init__(self, *, recall: WorkingMemoryRecall | None = None) -> None:
        self._recall = recall or WorkingMemoryRecall.empty()
        self.recalls: list[tuple[str, str]] = []
        self.captures: list[tuple[str, str, str]] = []

    async def recall(self, session_key: str, query: str) -> WorkingMemoryRecall:
        self.recalls.append((session_key, query))
        return self._recall

    async def capture(self, session_key: str, user_content: str, assistant_content: str) -> bool:
        self.captures.append((session_key, user_content, assistant_content))
        return True


def _make_app(
    lane: Any = None,
) -> tuple[TestClient, FakeLLMClient]:
    persona = PersonaConfig(
        id=PERSONA_ID,
        name="Chandler",
        era_knowledge_boundary="2024-12-01",
    )
    registry = InMemoryPersonaRegistry({persona.id: (persona, InMemoryMemoryBackend())})
    keys = InMemoryApiKeyStore({API_KEY: PERSONA_ID}, read_env=False)
    application = create_app(
        api_key_store=keys,
        persona_registry=registry,
        llm_client=FakeLLMClient(persona_name="Chandler"),
        start_time=0.0,
    )
    if lane is not None:
        application.state.working_memory = lane
    return TestClient(application), application.state.llm_client


def _consent(client: TestClient) -> None:
    r = client.post(
        f"/api/v1/chat/{PERSONA_ID}/consent",
        json={"conversation_id": CONV},
        headers={"Authorization": f"Bearer {API_KEY}"},
    )
    assert r.status_code == 200, r.text


def _chat(client: TestClient, message: str):
    return client.post(
        f"/api/v1/chat/{PERSONA_ID}",
        json={"message": message, "conversation_id": CONV},
        headers={"Authorization": f"Bearer {API_KEY}"},
    )


def test_recall_reaches_prompt_and_trace() -> None:
    lane = _StubWorkingMemory(
        recall=WorkingMemoryRecall(
            context="WM-DIGEST-MARKER turn 1: hey who r u?",
            strategy=ARM_A_STRATEGY,
            chars=33,
        )
    )
    client, llm = _make_app(lane)
    _consent(client)
    r = _chat(client, "what was the first thing I said to you?")
    assert r.status_code == 200, r.text
    prompt, _system = llm.calls[-1]
    assert "WORKING MEMORY" in prompt
    assert "WM-DIGEST-MARKER" in prompt
    trace = r.json()["trace"]
    assert trace["working_memory"] is not None
    assert trace["working_memory"]["strategy"] == ARM_A_STRATEGY
    assert trace["working_memory"]["chars"] > 0
    assert trace["working_memory"]["synced"] is True
    # Recall was queried with the inbound message under the scoped key.
    session_key, query = lane.recalls[-1]
    assert query == "what was the first thing I said to you?"
    assert session_key == working_memory_session_key(PERSONA_ID, CONV)


def test_completed_turn_is_captured() -> None:
    lane = _StubWorkingMemory()
    client, _llm = _make_app(lane)
    _consent(client)
    r = _chat(client, "do you remember the foosball tournament?")
    assert r.status_code == 200, r.text
    assert len(lane.captures) == 1
    session_key, user_content, assistant_content = lane.captures[0]
    assert session_key == working_memory_session_key(PERSONA_ID, CONV)
    assert user_content == "do you remember the foosball tournament?"
    reply = r.json()["response"]
    assert assistant_content == reply


def test_disabled_lane_is_pre_w4() -> None:
    client, llm = _make_app()  # no lane injected -> default null lane
    assert isinstance(client.app.state.working_memory, NullWorkingMemory)  # type: ignore[union-attr]
    _consent(client)
    r = _chat(client, "hello there")
    assert r.status_code == 200, r.text
    prompt, _system = llm.calls[-1]
    assert "WORKING MEMORY" not in prompt
    assert r.json()["trace"]["working_memory"] is None


def test_degraded_recall_keeps_turn_alive() -> None:
    lane = _StubWorkingMemory(recall=WorkingMemoryRecall.empty())
    client, llm = _make_app(lane)
    _consent(client)
    r = _chat(client, "what was the first thing I said to you?")
    assert r.status_code == 200, r.text
    prompt, _system = llm.calls[-1]
    assert "WORKING MEMORY" not in prompt
    trace = r.json()["trace"]
    assert trace["working_memory"]["chars"] == 0
    assert trace["working_memory"]["synced"] is True

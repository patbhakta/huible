"""M1.3 eviction boundary test (HU-2732): retrieval after leaving prompt context.

The W4 design bounds the rendered prompt: ``HISTORY_WINDOW=10`` verbatim tail
plus a ``WORKING_MEMORY_HEAD_CAP=30`` verbatim head — 40 ConversationTurn
rows total (an exchange records a user + a persona row, so 20 exchanges).
Rows older than that are GONE from the prompt the generator sees; the
TencentDB Arm A digest + excerpts are the only carrier that can bring them
back (the RC-3 failure class from M-0: "what was the first thing I said?"
forgotten by ~turn 22).

This module pins that boundary at the full-route level, hermetically
(FakeLLM + stub working-memory lane, zero hosted spend). The §7.4.4 dosage
cap is disabled for the session (``risk_dosage_cap_turns=0`` — the sanctioned
settings contract) so the driver can walk past the pause threshold that
otherwise ends a production session at its 40-row block boundary:

1. A 23-exchange conversation whose exchange 1 plants a marker (rows 1-2).
   At the probe (exchange 23) the marker is outside window+head: with an
   EMPTY recall the rendered prompt must NOT contain it (it truly left
   prompt context).
2. The same probe with a recall payload carrying the marker: the prompt
   MUST contain it — working memory retrieved what the window evicted.
3. Append-only capture: every completed turn appends exactly one capture,
   in order, none dropped or overwritten.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from huible.api.app import create_app
from huible.api.auth import InMemoryApiKeyStore, InMemoryPersonaRegistry
from huible.api.settings import Settings
from huible.conversation import InMemoryMemoryBackend
from huible.llm.client import FakeLLMClient
from huible.persona.context import PersonaConfig
from huible.persona.working_memory import (
    WorkingMemoryClient,
    WorkingMemoryRecall,
    working_memory_session_key,
)

PERSONA_ID = uuid4()
API_KEY = "key-eviction-test"
CONV = "demo-eviction-test"
PLANT_MARKER = "YELLOW-UMBRELLA-PLANT-MARKER"

#: Rows leaving the prompt = window(10) + head(30) = 40; an exchange records
#: 2 rows, so the plant (rows 1-2, exchange 1) is outside the rendered prompt
#: once 21+ later exchanges exist. Probe runs at exchange 23.
_FILLER_EXCHANGES = 21


class _RecordingWorkingMemory:
    """Append-capturing lane; recall payload configurable per probe."""

    def __init__(self, recall: WorkingMemoryRecall | None = None) -> None:
        self._recall = recall
        self.captures: list[tuple[str, str, str]] = []

    async def recall(self, session_key: str, query: str) -> WorkingMemoryRecall:
        return self._recall or WorkingMemoryRecall.empty()

    async def capture(self, session_key: str, user_content: str, assistant_content: str) -> bool:
        self.captures.append((session_key, user_content, assistant_content))
        return True


def _make_app(
    lane: WorkingMemoryClient,
) -> tuple[TestClient, Any]:
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
        # Dosage pause disabled: the test walks past the 20-exchange session
        # threshold to reach the 40-row prompt boundary the WM lane backs.
        # Host-independent env (HU-2836, same pattern as HU-2828 32a7fb8):
        # Settings is a pydantic BaseSettings bound to the CWD's .env, so any
        # field not passed here absorbs host drift — GENERATOR_PROVIDER=
        # openrouter falls back to the mock generator, EMBEDDING_PROVIDER=
        # local_onnx fails app construction (or fetches models), the
        # PERSONA_CHAT_* gates and DATABASE_URL leak host posture into the
        # route. Pin the env-sensitive surface the chat path touches.
        settings=Settings(
            working_memory_enabled=False,
            risk_dosage_cap_turns=0,
            generator_provider="mock",
            llm_provider="fake",
            embedding_provider="fake",
            persona_chat_real_user_traffic="off",
            persona_chat_real_user_mode="off",
            persona_chat_coverage_enforcement="off",
            conversation_writeback_enabled=False,
            database_url="",
            postgres_user="",
            postgres_db="",
        ),
        start_time=0.0,
    )
    application.state.working_memory = lane
    return TestClient(application), application.state.llm_client


def _consent(client: TestClient) -> None:
    r = client.post(
        f"/api/v1/chat/{PERSONA_ID}/consent",
        json={"conversation_id": CONV},
        headers={"Authorization": f"Bearer {API_KEY}"},
    )
    assert r.status_code == 200, r.text


def _turn(client: TestClient, message: str):
    # Internal traffic class: headerless posts resolve to TrafficClass.REAL,
    # which puts every filler turn behind the host-env ramp gate — a staged
    # 503 there silently drops recorded rows and breaks the eviction math.
    return client.post(
        f"/api/v1/chat/{PERSONA_ID}",
        json={"message": message, "conversation_id": CONV},
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "X-Huible-Traffic-Class": "internal",
        },
    )


def _run_to_probe(lane: _RecordingWorkingMemory, probe_message: str) -> str:
    """Drive the session; return the prompt the probe turn rendered."""
    client, llm = _make_app(lane)
    _consent(client)
    r = _turn(client, f"first thing ever: my key is {PLANT_MARKER}")
    assert r.status_code == 200, r.text
    for i in range(_FILLER_EXCHANGES):
        r = _turn(client, f"filler exchange {i + 2}: nothing about umbrellas here {i}")
        assert r.status_code == 200, r.text
    r = _turn(client, probe_message)
    assert r.status_code == 200, r.text
    prompt, _system = llm.calls[-1]
    return prompt


def test_planted_turn_leaves_prompt_context() -> None:
    """Empty recall: the exchange-1 marker is absent from the rendered prompt."""
    prompt = _run_to_probe(
        _RecordingWorkingMemory(recall=WorkingMemoryRecall.empty()),
        "what was the first thing I ever told you?",
    )
    assert "CONVERSATION HISTORY" in prompt
    assert PLANT_MARKER not in prompt


def test_working_memory_retrieves_what_the_window_evicted() -> None:
    """Recall payload carrying the marker puts it back into the prompt."""
    prompt = _run_to_probe(
        _RecordingWorkingMemory(
            recall=WorkingMemoryRecall(
                context=f"session digest: exchange 1 said {PLANT_MARKER}",
                strategy="v4-arm-a",
                chars=64,
            )
        ),
        "what was the first thing I ever told you?",
    )
    assert PLANT_MARKER in prompt
    assert "WORKING MEMORY" in prompt


def test_capture_is_append_only_per_completed_turn() -> None:
    """Every completed turn appends exactly one capture, in order."""
    lane = _RecordingWorkingMemory()
    client, _llm = _make_app(lane)
    _consent(client)
    plant = f"my key is {PLANT_MARKER}"
    r = _turn(client, plant)
    assert r.status_code == 200, r.text
    for i in range(_FILLER_EXCHANGES):
        assert _turn(client, f"filler {i}").status_code == 200
    r = _turn(client, "what was the first thing I ever told you?")
    assert r.status_code == 200, r.text
    assert len(lane.captures) == _FILLER_EXCHANGES + 2
    # order preserved: capture 0 is the plant, never overwritten
    assert lane.captures[0][1] == plant
    assert all(
        key == working_memory_session_key(PERSONA_ID, CONV) for key, _u, _a in lane.captures
    )
    assert all(user or assistant for _key, user, assistant in lane.captures)

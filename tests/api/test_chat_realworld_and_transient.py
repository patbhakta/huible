"""HU-2828: chat-path wiring — CURRENT-REALITY lane, clock fix, no raw 500.

Proves the founder's three portal symptoms are fixed end-to-end:

1. time-of-day resolves to the persona's location (Chandler = NYC) at reply
   time, with the HU-2774 "noon" pin scoped to machine flows (human portals
   opt out via the ``X-Huible-Client: portal-human`` header);
2. a real-world probe (rent) fetches SearXNG hits through the persona's
   CURRENT-REALITY lane and grounds the prompt + safety guards;
3. a transient LLM failure NEVER surfaces as a raw 500 — the portal gets a
   structured, retryable 503 (and a forced-empty mock recovers into a normal
   reply).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from huible.api.app import _embed, create_app
from huible.api.auth import InMemoryApiKeyStore, InMemoryPersonaRegistry
from huible.api.settings import Settings
from huible.llm.client import FakeLLMClient, LLMEmptyContentError, LLMError
from huible.memory.protocol import (
    ContentType,
    DisclosureScope,
    MemoryNode,
    MemoryTier,
    SearchResult,
    SourceType,
)
from huible.persona.context import CONFIDENCE_LEVEL_METADATA_KEY, PersonaConfig

PERSONA_ID = uuid4()
API_KEY = "key-chandler-family"
NY_TZ = "America/New_York"

RENT_HIT_PAYLOAD = {
    "results": [
        {
            "title": "Greenwich Village rents",
            "url": "https://example.test/rent",
            "content": "Median rent in Greenwich Village runs about $4,200 a month.",
        },
        {
            "title": "NYC cost of living",
            "url": "https://example.test/col",
            "content": "Manhattan rents keep climbing this year.",
        },
    ]
}


class _FakeBackend:
    async def store_memory(self, node: MemoryNode) -> Any:  # pragma: no cover
        return node.id

    async def get_memory(self, memory_id: Any) -> None:  # pragma: no cover
        return None

    async def search_by_content(self, *a: Any, **k: Any) -> list[SearchResult]:
        return []

    async def search_by_sensory(self, *a: Any, **k: Any) -> list[SearchResult]:  # pragma: no cover
        return []

    async def search_by_affect(self, *a: Any, **k: Any) -> list[SearchResult]:  # pragma: no cover
        return []

    async def get_edges(self, memory_id: Any) -> list:  # pragma: no cover
        return []


class _FlakyThenOKLLM(FakeLLMClient):
    """Forced-empty mock: raises LLMEmptyContentError once, then replies."""

    def __init__(self) -> None:
        super().__init__(persona_name="Chandler")
        self.generate_calls = 0

    async def generate(self, prompt, *, system_prompt=None, **kwargs):
        self.generate_calls += 1
        if self.generate_calls == 1:
            raise LLMEmptyContentError("LLM at test returned empty content")
        return await super().generate(prompt, system_prompt=system_prompt, **kwargs)


class _AlwaysErrorLLM(FakeLLMClient):
    """Hosted LLM that never answers (retries exhausted upstream)."""

    def __init__(self) -> None:
        super().__init__(persona_name="Chandler")

    async def generate(self, prompt, *, system_prompt=None, **kwargs):
        raise LLMError("LLM at test returned empty content")


def _persona(metadata: dict[str, Any] | None = None) -> PersonaConfig:
    return PersonaConfig(
        id=PERSONA_ID,
        name="Chandler",
        voice_instructions="Sarcastic, warm.",
        era_knowledge_boundary="2004-05-06",
        metadata={
            "location_timezone": NY_TZ,
            "location_label": "Greenwich Village, New York",
            "search_lanes": {"current_reality": True, "academic": False},
            **(metadata or {}),
        },
    )


def _make_app(
    *,
    persona_metadata: dict[str, Any] | None = None,
    settings: Settings | None = None,
    llm: Any | None = None,
) -> tuple[TestClient, FakeLLMClient, Any]:
    llm = llm or FakeLLMClient(persona_name="Chandler")
    persona = _persona(persona_metadata)
    registry = InMemoryPersonaRegistry({persona.id: (persona, _FakeBackend())})
    keys = InMemoryApiKeyStore({API_KEY: PERSONA_ID}, read_env=False)
    resolved = settings or Settings()
    application = create_app(
        api_key_store=keys,
        persona_registry=registry,
        llm_client=llm,
        start_time=0.0,
        settings=resolved,
    )
    return TestClient(application), llm, application


def _consent(client: TestClient, conv: str) -> None:
    r = client.post(
        f"/api/v1/chat/{PERSONA_ID}/consent",
        json={"conversation_id": conv},
        headers={"Authorization": f"Bearer {API_KEY}"},
    )
    assert r.status_code == 200, r.text


def _chat(client: TestClient, message: str, conv: str, *, portal: bool = True):
    headers = {"Authorization": f"Bearer {API_KEY}"}
    if portal:
        headers["X-Huible-Client"] = "portal-human"
    return client.post(
        f"/api/v1/chat/{PERSONA_ID}",
        json={"message": message, "conversation_id": conv},
        headers=headers,
    )


def _arm_searxng(application: Any, payload: Any = RENT_HIT_PAYLOAD) -> list[httpx.Request]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=payload if not isinstance(payload, int) else None) if not isinstance(payload, int) else httpx.Response(payload, text="boom")

    application.state.searxng_transport = httpx.MockTransport(handler)
    return requests


SEARCH_SETTINGS = Settings(
    real_world_search_enabled=True,
    searxng_base_url="http://searx.test",
    searxng_timeout_s=2.0,
    searxng_max_results=3,
)


class TestRealworldLaneChatWiring:
    def test_rent_probe_fires_lane_and_renders_block(self):
        client, llm, application = _make_app(settings=SEARCH_SETTINGS)
        _arm_searxng(application)
        conv = "conv-rent"
        _consent(client, conv)

        r = _chat(client, "How much is rent where you live?", conv)

        assert r.status_code == 200, r.text
        prompt, _system = llm.calls[-1]
        assert "YOUR WORLD RIGHT NOW" in prompt
        assert "[CURWORLD]" in prompt
        assert "$4,200 a month" in prompt
        # The lane-fired turn is in-domain: the competence wall stayed silent.
        assert r.json()["trace"]["competence_wall"] is False

    def test_rent_probe_query_uses_persona_location(self):
        client, _llm, application = _make_app(settings=SEARCH_SETTINGS)
        requests = _arm_searxng(application)
        conv = "conv-query"
        _consent(client, conv)

        _chat(client, "How much is rent where you live?", conv)

        assert requests, "SearXNG was never called"
        assert requests[0].url.params["q"] == "average rent in Greenwich Village, New York"
        assert requests[0].url.params["format"] == "json"

    def test_mayans_probe_never_touches_search(self):
        client, llm, application = _make_app(settings=SEARCH_SETTINGS)
        requests = _arm_searxng(application)
        conv = "conv-mayans"
        _consent(client, conv)

        r = _chat(client, "Tell me about the Mayans", conv)

        assert r.status_code == 200
        assert requests == [], "academic probe must not reach SearXNG"
        prompt, _system = llm.calls[-1]
        assert "YOUR WORLD RIGHT NOW" not in prompt
        assert "[CURWORLD]" not in prompt

    def test_lane_not_provisioned_skips_search(self):
        persona_metadata = {"search_lanes": None}
        client, llm, application = _make_app(
            persona_metadata=persona_metadata, settings=SEARCH_SETTINGS
        )
        requests = _arm_searxng(application)
        conv = "conv-off"
        _consent(client, conv)

        r = _chat(client, "How much is rent where you live?", conv)

        assert r.status_code == 200
        assert requests == []
        prompt, _system = llm.calls[-1]
        assert "YOUR WORLD RIGHT NOW" not in prompt
        assert "[CURWORLD]" not in prompt

    def test_searxng_failure_degrades_without_breaking_turn(self):
        client, llm, application = _make_app(settings=SEARCH_SETTINGS)
        _arm_searxng(application, payload=503)
        conv = "conv-degrade"
        _consent(client, conv)

        r = _chat(client, "How much is rent where you live?", conv)

        assert r.status_code == 200, r.text
        prompt, _system = llm.calls[-1]
        assert "YOUR WORLD RIGHT NOW" not in prompt
        assert "[CURWORLD]" not in prompt


class TestPersonaLocalClock:
    def test_portal_human_gets_persona_location_time(self):
        client, llm, application = _make_app()
        application.state.chat_now = lambda: datetime(2026, 9, 11, 20, 33, tzinfo=UTC)
        conv = "conv-clock"
        _consent(client, conv)

        r = _chat(client, "So, busy day?", conv)

        assert r.status_code == 200
        _prompt, system = llm.calls[-1]
        # 20:33 UTC = 16:33 America/New_York — the noon pin is NOT honored
        # for human-portal conversations.
        assert "16:33" in system
        assert "(12:00)" not in system

    def test_machine_flows_keep_noon_pin(self):
        client, llm, application = _make_app(
            persona_metadata={"in_world_clock": "noon"}
        )
        application.state.chat_now = lambda: datetime(2026, 9, 11, 20, 33, tzinfo=UTC)
        conv = "conv-machine"
        _consent(client, conv)

        r = _chat(client, "So, busy day?", conv, portal=False)

        assert r.status_code == 200
        _prompt, system = llm.calls[-1]
        # Battery-flow semantics unchanged (HU-2774).
        assert "(12:00)" in system

    def test_caretaker_answers_in_persona_location_tz(self):
        client, llm, application = _make_app()
        application.state.chat_now = lambda: datetime(2026, 9, 11, 20, 33, tzinfo=UTC)
        conv = "conv-caretaker"
        _consent(client, conv)

        r = _chat(client, "What time is it right now?", conv)

        assert r.status_code == 200
        body = r.json()
        assert body["trace"]["caretaker"]["kind"] == "temporal"
        assert "EDT" in body["response"] or "-04:00" in body["response"]
        assert "16:33" in body["response"]


class TestTransientLLMNeverRaw500:
    def test_forced_empty_response_recovers_without_500(self):
        """The comment-1 DONE-WHEN: forced-empty mock recovers, normal reply."""
        client, llm, _application = _make_app(llm=_FlakyThenOKLLM())
        conv = "conv-recover"
        _consent(client, conv)

        r = _chat(client, "Hey.", conv)

        assert r.status_code == 200, r.text
        assert llm.generate_calls == 2
        assert r.json()["response"]

    def test_exhausted_llm_error_is_structured_503_not_500(self):
        client, _llm, _application = _make_app(llm=_AlwaysErrorLLM())
        conv = "conv-503"
        _consent(client, conv)

        r = _chat(client, "Hey.", conv)

        assert r.status_code == 503
        detail = r.json()["detail"]
        assert detail["error"]["code"] == "LLM_TRANSIENT"
        assert detail["error"]["retryable"] is True
        assert detail["error"]["message"]
        assert detail["error"]["trace_id"]
        # Portal-parseable shape: detail.error.message.
        assert isinstance(detail["error"]["message"], str)

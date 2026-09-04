"""W5 caretaker-channel + era-clock chat-path tests (HU-2309 v1.8 §1.7.2 / M-0R-E).

Covers ``POST /api/v1/chat/{persona_id}`` integration:

- date/time-class questions route to the caretaker channel: a clearly-labeled
  out-of-persona answer from the real clock — no retrieval, no generation,
  no working-memory capture, no persona turn recorded (the persona corpus
  and history are never fed);
- **CA C2** — the caretaker stays inside the G-path: a crisis disclosure
  arriving at the caretaker channel routes to G1 handling (never to a
  date/time non-answer), the G6 consent gate still refuses un-consented
  sessions, and a G8 short-circuit (pause_session) outranks the caretaker;
- the in-world era clock renders an era-gated system-prompt line (pinned to
  the persona's ``era_knowledge_boundary``) when the caller clock is wired;
- the hobby/interest tool grounds interest-shaped turns in the persona's own
  era-admissible vault lines and surfaces on the trace.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

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
from huible.safety import InMemoryRiskProfile, RiskFlag

PERSONA_ID = uuid4()
API_KEY = "key-chandler-caretaker"
CONV = "sess-caretaker"


# ---------------------------------------------------------------------------
# Fixtures (mirrors tests/api/test_chat_guardrails.py)
# ---------------------------------------------------------------------------


class _FakeBackend:
    """In-memory backend; ``search_by_content`` ranks by dot product."""

    def __init__(self) -> None:
        self._memories: dict[UUID, MemoryNode] = {}
        self._vectors: list[tuple[list[float], UUID]] = []

    def seed(self, node: MemoryNode) -> None:
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
    content_type: ContentType = ContentType.NARRATIVE,
    memory_date: date | None = None,
) -> MemoryNode:
    return MemoryNode(
        id=uuid4(),
        persona_id=PERSONA_ID,
        tier=MemoryTier.CANONICAL,
        content=content,
        content_type=content_type,
        embedding_content=_embed(content),
        memory_date=memory_date,
        source_type=SourceType.EXTRACTION,
        disclosure_scope=DisclosureScope.FAMILY,
        metadata={CONFIDENCE_LEVEL_METADATA_KEY: "high"},
    )


def _persona() -> PersonaConfig:
    return PersonaConfig(
        id=PERSONA_ID,
        name="Chandler",
        era_knowledge_boundary="2024-12-01",
    )


class _StubWorkingMemory:
    """Deterministic in-test lane: records recalls + captures."""

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
    risk_profile: InMemoryRiskProfile | None = None,
    settings: Settings | None = None,
    now_fn: Any = None,
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
        risk_profile=risk_profile,
        settings=settings,
        start_time=0.0,
        now_fn=now_fn,
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
# Caretaker routing
# ---------------------------------------------------------------------------


class TestCaretakerRouting:
    def test_temporal_question_routes_to_caretaker(self):
        client, llm = _make_app()
        _consent(client)
        body = _post(client, "what day is it?")

        # Out-of-persona, clearly labeled, answered from the real clock.
        assert body["response"].startswith("[Caretaker — out of character, not Chandler]")
        assert "Today is" in body["response"]
        # No persona generation, no retrieval.
        assert llm.calls == []
        assert body["trace"]["memory_refs"] == []
        assert body["trace"]["provider"] == "caretaker(clock)"
        caretaker = body["trace"]["caretaker"]
        assert caretaker is not None
        assert caretaker["kind"] == "temporal"
        assert caretaker["era_boundary"] == "2024-12-01"
        # CA C2 evidence: the full G-path executed on this caretaker-routed turn.
        assert caretaker["gates_cleared"] == ["g1_crisis", "g6_consent", "g8_risk"]

    def test_caretaker_turn_never_feeds_persona_memory(self):
        client, llm = _make_app()
        lane = _StubWorkingMemory()
        client.app.state.working_memory = lane  # type: ignore[union-attr]
        _consent(client)
        _post(client, "what time is it?")

        # No recall, no capture: the caretaker turn is not a persona turn.
        assert lane.recalls == []
        assert lane.captures == []
        assert llm.calls == []

        # The following persona turn's prompt history carries no caretaker
        # voice — the reply is not recorded into the conversation history.
        _post(client, "anyway — do you remember the foosball tournament?")
        assert len(llm.calls) == 1
        prompt, _system = llm.calls[-1]
        assert "[Caretaker" not in prompt
        assert "Today is" not in prompt

    def test_non_temporal_message_stays_persona(self):
        client, llm = _make_app()
        _consent(client)
        body = _post(client, "hey you guys!")
        assert len(llm.calls) == 1
        assert body["trace"]["caretaker"] is None

    def test_autobiographical_time_reference_stays_persona(self):
        """The E0 turn-class must NOT route out of voice (narrow classifier)."""
        client, llm = _make_app()
        _consent(client)
        body = _post(client, "what was the first thing I said to you?")
        assert len(llm.calls) == 1
        assert body["trace"]["caretaker"] is None

    def test_disabled_flag_serves_persona_path(self):
        settings = Settings(caretaker_channel_enabled=False)
        client, llm = _make_app(settings=settings)
        _consent(client)
        _post(client, "what day is it?")
        assert len(llm.calls) == 1


# ---------------------------------------------------------------------------
# CA C2 — out-of-voice ≠ out-of-safety-stack
# ---------------------------------------------------------------------------


class TestCaretakerStaysInsideGPath:
    def test_crisis_at_caretaker_routes_to_g1(self):
        """A crisis disclosure arriving at the caretaker channel routes to G1
        handling — never to a date/time non-answer."""
        client, llm = _make_app()
        _consent(client)
        body = _post(client, "what day is it? I want to die, I have the pills")

        trace = body["trace"]
        assert trace["caretaker"] is None
        assert trace["safety_event"] is not None
        assert trace["safety_event"]["kind"] == "crisis_escalation"
        assert trace["handoff"] is not None
        assert "Today is" not in body["response"]
        assert llm.calls == []

    def test_consent_gate_precedes_caretaker(self):
        """An un-consented session gets 409 CONSENT_REQUIRED, not the clock."""
        client, llm = _make_app()
        r = client.post(
            f"/api/v1/chat/{PERSONA_ID}",
            json={"message": "what time is it?", "conversation_id": CONV},
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["error"]["code"] == "CONSENT_REQUIRED"
        assert llm.calls == []

    def test_g8_pause_outranks_caretaker(self):
        """A G8 short-circuit (proxy_user → pause_session) suppresses the
        caretaker route — enforcement runs on caretaker-class input."""
        profile = InMemoryRiskProfile()
        profile.set_session_flags(CONV, PERSONA_ID, {RiskFlag.PROXY_USER})
        client, llm = _make_app(risk_profile=profile)
        _consent(client)
        body = _post(client, "what time is it?")

        assert body["trace"]["caretaker"] is None
        assert body["trace"]["risk_enforcement"]["action"] == "pause_session"
        assert llm.calls == []


# ---------------------------------------------------------------------------
# In-world era clock (system-prompt line)
# ---------------------------------------------------------------------------


class TestEraClock:
    def _now(self) -> datetime:
        return datetime(2026, 9, 4, 14, 5, tzinfo=UTC)

    def test_clock_line_pinned_to_boundary(self):
        client, llm = _make_app(now_fn=self._now)
        _consent(client)
        _post(client, "hey you guys!")
        _prompt, system = llm.calls[-1]
        assert "In-world clock:" in system
        # The persona's today pins to the era boundary, never the real date.
        assert "December 1, 2024" in system
        assert "2026" not in system
        assert "never state the real-world current date" in system

    def test_clock_disabled_keeps_pre_w5_prompt(self):
        settings = Settings(era_clock_enabled=False)
        client, llm = _make_app(settings=settings, now_fn=self._now)
        _consent(client)
        _post(client, "hey you guys!")
        _prompt, system = llm.calls[-1]
        assert "In-world clock:" not in system


# ---------------------------------------------------------------------------
# Hobby / interest tool
# ---------------------------------------------------------------------------


class TestInterestTool:
    def test_interest_turn_grounded_and_traced(self):
        backend = _FakeBackend()
        backend.seed(
            _node(
                content="I love foosball, I am basically a professional.",
                content_type=ContentType.PREFERENCE,
            )
        )
        client, llm = _make_app(backend=backend)
        _consent(client)
        body = _post(client, "do you like foosball?")

        interest = body["trace"]["interest_tool"]
        assert interest is not None
        assert interest["lines"] >= 1
        prompt, _system = llm.calls[-1]
        assert "YOUR INTERESTS" in prompt
        assert "[INTEREST] I love foosball" in prompt

    def test_non_interest_turn_has_no_interest_trace(self):
        client, _llm = _make_app()
        _consent(client)
        body = _post(client, "hey you guys!")
        assert body["trace"]["interest_tool"] is None

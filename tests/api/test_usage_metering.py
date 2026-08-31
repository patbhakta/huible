"""Tests for the HU-2243 Sprint 1 usage metering skeleton.

Covers the deliverable bar (CEO scope, 2026-08-31):

* **Migration/ORM parity** — the ``llm_usage`` table exists with the
  attribution + resource columns (exercised via the sqlite-portable ORM,
  the same backend-portable convention as the §7.4 safety store tests).
* **Write path** — the chat endpoint writes one usage row per LLM turn,
  keyed on the caller's API-key digest + persona + conversation, with
  token counts from the client's ``last_usage`` and latency measured
  around the generate call.
* **Read path** — ``GET /api/v1/usage/daily`` returns per-key /
  per-persona daily aggregates (requests, tokens in/out, modeled cost,
  avg latency, conversation count) and enforces the persona scope (403).
* **Cost model** — modeled reference-rate pricing, reported-cost override,
  and the free fake-voice basis.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from huible.api.app import _embed, create_app
from huible.api.auth import InMemoryApiKeyStore, InMemoryPersonaRegistry
from huible.api.metering import (
    InMemoryUsageRecorder,
    PostgresUsageRecorder,
    UsageRecord,
    api_key_attribution_id,
    default_window,
    estimate_tokens,
    modeled_cost_usd,
)
from huible.llm.client import FakeLLMClient
from huible.memory.protocol import (
    ContentType,
    DisclosureScope,
    MemoryNode,
    MemoryTier,
    SourceType,
)
from huible.persona.context import CONFIDENCE_LEVEL_METADATA_KEY, PersonaConfig

PERSONA_ID = uuid4()
API_KEY = "key-metering-family"
OTHER_PERSONA_ID = uuid4()
OTHER_API_KEY = "key-metering-other"


# ---------------------------------------------------------------------------
# Shared fixtures (same shape as tests/api/test_chat.py)
# ---------------------------------------------------------------------------


class _FakeBackend:
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

    async def search_by_content(self, persona_id, query_embedding, top_k=20, disclosure_scope=None):
        results = []
        for vec, node_id in self._vectors:
            node = self._memories[node_id]
            if node.persona_id != persona_id:
                continue
            dot = sum(q * e for q, e in zip(query_embedding, vec, strict=False))
            if dot > 0.0:
                from huible.memory.protocol import SearchResult

                results.append(SearchResult(node=node, score=dot))
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]

    async def search_by_sensory(self, *a, **k):
        return []

    async def search_by_affect(self, *a, **k):
        return []

    async def get_edges(self, memory_id):
        return []


def _seeded_backend() -> _FakeBackend:
    backend = _FakeBackend()
    vec = _embed("fishing lake")
    backend.seed(
        MemoryNode(
            id=uuid4(),
            persona_id=PERSONA_ID,
            tier=MemoryTier.ACCRUED,
            content="Chandler loved fishing on Lake Travis.",
            content_type=ContentType.NARRATIVE,
            embedding_content=vec,
            source_type=SourceType.EXTRACTION,
            disclosure_scope=DisclosureScope.FAMILY,
            metadata={CONFIDENCE_LEVEL_METADATA_KEY: "high"},
        )
    )
    return backend


def _make_app(recorder=None) -> tuple[TestClient, InMemoryUsageRecorder, FakeLLMClient]:
    llm = FakeLLMClient(persona_name="Chandler")
    persona = PersonaConfig(
        id=PERSONA_ID,
        name="Chandler",
        voice_instructions="Warm Texas storyteller.",
        era_knowledge_boundary="2024-12-01",
    )
    registry = InMemoryPersonaRegistry({persona.id: (persona, _seeded_backend())})
    keys = InMemoryApiKeyStore(
        {API_KEY: PERSONA_ID, OTHER_API_KEY: OTHER_PERSONA_ID}, read_env=False
    )
    recorder = recorder or InMemoryUsageRecorder()
    application = create_app(
        api_key_store=keys,
        persona_registry=registry,
        llm_client=llm,
        usage_recorder=recorder,
        start_time=0.0,
    )
    return TestClient(application), recorder, llm


def _consent(client: TestClient, conv: str) -> str:
    r = client.post(
        f"/api/v1/chat/{PERSONA_ID}/consent",
        json={"conversation_id": conv},
        headers={"Authorization": f"Bearer {API_KEY}"},
    )
    assert r.status_code == 200, r.text
    return conv


def _chat(client: TestClient, *, message: str, conv: str):
    return client.post(
        f"/api/v1/chat/{PERSONA_ID}",
        json={"message": message, "conversation_id": conv},
        headers={"Authorization": f"Bearer {API_KEY}"},
    )


# ---------------------------------------------------------------------------
# Cost model + attribution units
# ---------------------------------------------------------------------------


class TestCostModel:
    def test_modeled_reference_rates(self):
        cost, basis = modeled_cost_usd(
            model="glm-5.3", tokens_in=1_000_000, tokens_out=500_000, provider="zai"
        )
        assert basis == "modeled"
        assert cost == pytest.approx(0.60 + 0.5 * 2.20)

    def test_unknown_model_uses_default_rate(self):
        cost, basis = modeled_cost_usd(
            model="mystery-model", tokens_in=1_000_000, tokens_out=0, provider="zai"
        )
        assert basis == "modeled"
        assert cost == 0.60

    def test_reported_cost_wins(self):
        cost, basis = modeled_cost_usd(
            model="glm-5.3",
            tokens_in=100,
            tokens_out=100,
            provider="openrouter",
            reported_cost_usd=0.00123,
        )
        assert basis == "reported"
        assert cost == 0.00123

    def test_fake_voice_is_free(self):
        cost, basis = modeled_cost_usd(
            model="fake", tokens_in=10_000, tokens_out=10_000, provider="fake"
        )
        assert basis == "free"
        assert cost == 0.0

    def test_key_digest_is_stable_and_not_the_key(self):
        a = api_key_attribution_id(API_KEY)
        assert a == api_key_attribution_id(API_KEY)
        assert a != API_KEY and len(a) == 16
        assert a != api_key_attribution_id(OTHER_API_KEY)

    def test_estimate_tokens(self):
        assert estimate_tokens("") == 0
        assert estimate_tokens("abcd" * 10) == 10
        assert estimate_tokens("a") == 1


# ---------------------------------------------------------------------------
# Recorder backends (in-memory + sqlite-portable durable)
# ---------------------------------------------------------------------------


def _record(day: date, *, key: str = "k1", persona: str = "p1", conv: str = "c1", tokens=(100, 50)):
    return UsageRecord(
        api_key_id=key,
        persona_id=persona,
        conversation_id=conv,
        provider="zai",
        model="glm-5.3",
        tokens_in=tokens[0],
        tokens_out=tokens[1],
        latency_ms=200,
        day=day,
    )


class TestRecorders:
    def test_in_memory_daily_aggregates_group_by_day_key_persona(self):
        recorder = InMemoryUsageRecorder()
        day = date(2026, 8, 31)
        recorder.record_turn(_record(day, tokens=(100, 50)))
        recorder.record_turn(_record(day, tokens=(200, 100), conv="c2"))
        recorder.record_turn(_record(day, key="k2", persona="p1"))
        recorder.record_turn(_record(day - timedelta(days=1)))
        rows = recorder.daily_aggregates(from_day=day, to_day=day)
        # (day,k1,p1), (day,k2,p1) — yesterday's row is outside the window.
        assert len(rows) == 2
        main = next(r for r in rows if r.api_key_id == "k1")
        assert main.requests == 2
        assert main.tokens_in == 300
        assert main.tokens_out == 150
        assert main.conversations == 2
        assert main.avg_latency_ms == 200.0
        assert main.modeled_cost_usd == round((300 * 0.60 + 150 * 2.20) / 1_000_000, 8)

    def test_in_memory_filters(self):
        recorder = InMemoryUsageRecorder()
        day = date(2026, 8, 31)
        recorder.record_turn(_record(day))
        recorder.record_turn(_record(day, persona="p2"))
        rows = recorder.daily_aggregates(from_day=day, to_day=day, persona_id="p2")
        assert len(rows) == 1 and rows[0].persona_id == "p2"
        rows = recorder.daily_aggregates(from_day=day, to_day=day, api_key_id="nope")
        assert rows == []

    def test_postgres_recorder_round_trip_on_sqlite(self):
        from sqlalchemy.pool import StaticPool

        from huible.api.metering import MeteringBase

        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        MeteringBase.metadata.create_all(engine)
        recorder = PostgresUsageRecorder("sqlite://", engine=engine)
        day = date(2026, 8, 31)
        recorder.record_turn(_record(day))
        recorder.record_turn(_record(day, tokens=(300, 150), conv="c2"))
        rows = recorder.daily_aggregates(from_day=day, to_day=day)
        assert len(rows) == 1
        row = rows[0]
        assert row.requests == 2
        assert row.tokens_in == 400
        assert row.tokens_out == 200
        assert row.conversations == 2
        assert row.modeled_cost_usd == round((400 * 0.60 + 200 * 2.20) / 1_000_000, 8)
        # Window + persona filter paths.
        assert recorder.daily_aggregates(from_day=day, to_day=day, persona_id="p9") == []
        assert (
            recorder.daily_aggregates(
                from_day=day + timedelta(days=1), to_day=day + timedelta(days=1)
            )
            == []
        )
        recorder.close()


# ---------------------------------------------------------------------------
# Chat-path write + aggregate endpoint (the deliverable test)
# ---------------------------------------------------------------------------


class TestChatMeteringWritePath:
    def test_chat_turn_writes_one_usage_row(self):
        client, recorder, _llm = _make_app()
        conv = _consent(client, "conv-metering")
        r = _chat(client, message="tell me about fishing", conv=conv)
        assert r.status_code == 200, r.text
        assert len(recorder.rows) == 1
        row = recorder.rows[0]
        assert row.api_key_id == api_key_attribution_id(API_KEY)
        assert row.persona_id == str(PERSONA_ID)
        assert row.conversation_id == conv
        assert row.provider == "fake"
        # Token counts came from the client's last_usage (estimates).
        assert row.tokens_in > 0 and row.tokens_out > 0
        assert row.latency_ms >= 0
        assert row.resolved_cost() == (0.0, "free")

    def test_no_llm_no_row(self):
        """Crisis short-circuit never reaches the LLM — no usage row."""
        client, recorder, _ = _make_app()
        conv = _consent(client, "conv-crisis")
        r = _chat(
            client, message="I am going to end my life tonight", conv=conv
        )
        assert r.status_code == 200
        assert recorder.rows == []


class TestUsageDailyEndpoint:
    def test_endpoint_returns_per_key_per_persona_daily_aggregate(self):
        client, _, _ = _make_app()
        conv = _consent(client, "conv-daily")
        _consent(client, "conv-daily-2")
        assert _chat(client, message="tell me about fishing", conv=conv).status_code == 200
        assert (
            _chat(client, message="more fishing stories", conv="conv-daily-2").status_code
            == 200
        )
        r = client.get(
            "/api/v1/usage/daily",
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        rows = data["rows"]
        assert len(rows) == 1
        row = rows[0]
        assert row["api_key_id"] == api_key_attribution_id(API_KEY)
        assert row["persona_id"] == str(PERSONA_ID)
        assert row["requests"] == 2
        assert row["tokens_in"] > 0 and row["tokens_out"] > 0
        assert row["conversations"] == 2
        assert row["modeled_cost_usd"] == 0.0  # fake voice basis
        assert row["day"] == datetime.now(UTC).date().isoformat()
        assert "from" in data["window"] and "to" in data["window"]

    def test_endpoint_requires_auth(self):
        client, _, _ = _make_app()
        assert client.get("/api/v1/usage/daily").status_code == 401

    def test_endpoint_persona_filter_out_of_scope_403(self):
        client, _, _ = _make_app()
        r = client.get(
            f"/api/v1/usage/daily?persona_id={OTHER_PERSONA_ID}",
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        assert r.status_code == 403

    def test_endpoint_persona_filter_in_scope(self):
        client, _, _ = _make_app()
        conv = _consent(client, "conv-filter")
        assert _chat(client, message="hi", conv=conv).status_code == 200
        r = client.get(
            f"/api/v1/usage/daily?persona_id={PERSONA_ID}",
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        assert r.status_code == 200
        assert len(r.json()["data"]["rows"]) == 1

    def test_endpoint_days_bounds(self):
        client, _, _ = _make_app()
        ok = client.get(
            "/api/v1/usage/daily?days=30",
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        assert ok.status_code == 200
        bad = client.get(
            "/api/v1/usage/daily?days=0",
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        assert bad.status_code == 422


class TestDefaultWindow:
    def test_default_window_trailing_days(self):
        today = date(2026, 8, 31)
        now = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
        from_day, to_day = default_window(7, now=now)
        assert to_day == today
        assert from_day == today - timedelta(days=6)
        # Clamped, never negative / exploding.
        from_day, to_day = default_window(10_000, now=now)
        assert (to_day - from_day).days == 365

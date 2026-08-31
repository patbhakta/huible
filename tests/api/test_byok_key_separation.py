"""Tests for HU-2243 Sprint 2 — product-key separation + the BYOK hook.

Founder directive (Pat, 2026-08-30) parts (1) and (3):

* **Key separation** — the persona voice (product traffic) builds from a
  dedicated provider key (``PERSONA_LLM_*`` overlay) while internals keep
  the shared config; empty overlay inherits the shared posture unchanged.
* **BYOK** — a chat turn may carry ``X-Provider-Key`` (gated by
  ``BYOK_ENABLED``, default-off); the turn runs on the client's key against
  the product voice's provider/model, attribution stays the caller's own
  bearer-key digest, and any failure falls back to the house key.
* **Metering split** — usage rows/aggregates carry ``key_source``
  (``byok`` | ``product`` | ``shared``) so the daily endpoint shows the
  product-vs-internals and house-vs-BYOK cost split from the same tables.
"""

from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from huible.api.app import PROVIDER_KEY_HEADER, _embed, _resolve_turn_llm, create_app
from huible.api.auth import InMemoryApiKeyStore, InMemoryPersonaRegistry
from huible.api.metering import (
    InMemoryUsageRecorder,
    MeteringBase,
    PostgresUsageRecorder,
    UsageRecord,
    api_key_attribution_id,
)
from huible.api.settings import Settings
from huible.llm.client import FakeLLMClient, LLMConfigError, LLMProvider, ZaiLLMClient
from huible.memory.protocol import (
    ContentType,
    DisclosureScope,
    MemoryNode,
    MemoryTier,
    SourceType,
)
from huible.persona.context import CONFIDENCE_LEVEL_METADATA_KEY, PersonaConfig

PERSONA_ID = uuid4()
API_KEY = "key-byok-family"


class _StubByokClient:
    """Deterministic stand-in for a BYOK-constructed hosted client."""

    provider = "zai"

    def __init__(self) -> None:
        self.last_usage = {
            "prompt_tokens": 11,
            "completion_tokens": 7,
            "model": "glm-5.3",
        }

    async def generate(self, prompt, *, system_prompt=None, **kwargs):
        return "a grounded byok reply"


class _FakeBackend:
    def __init__(self) -> None:
        self._memories: dict = {}
        self._vectors: list[tuple[list[float], object]] = []

    def seed(self, node: MemoryNode) -> None:
        self._memories[node.id] = node
        if node.embedding_content:
            self._vectors.append((node.embedding_content, node.id))

    async def store_memory(self, node: MemoryNode) -> object:
        self.seed(node)
        return node.id

    async def get_memory(self, memory_id):
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
    backend.seed(
        MemoryNode(
            id=uuid4(),
            persona_id=PERSONA_ID,
            tier=MemoryTier.ACCRUED,
            content="Chandler loved fishing on Lake Travis.",
            content_type=ContentType.NARRATIVE,
            embedding_content=_embed("fishing lake"),
            source_type=SourceType.EXTRACTION,
            disclosure_scope=DisclosureScope.FAMILY,
            metadata={CONFIDENCE_LEVEL_METADATA_KEY: "high"},
        )
    )
    return backend


def _make_app(
    *,
    settings: Settings | None = None,
    recorder: InMemoryUsageRecorder | None = None,
    llm_client=None,
) -> tuple[TestClient, InMemoryUsageRecorder]:
    persona = PersonaConfig(
        id=PERSONA_ID,
        name="Chandler",
        voice_instructions="Warm Texas storyteller.",
        era_knowledge_boundary="2024-12-01",
    )
    registry = InMemoryPersonaRegistry({persona.id: (persona, _seeded_backend())})
    keys = InMemoryApiKeyStore({API_KEY: PERSONA_ID}, read_env=False)
    recorder = recorder or InMemoryUsageRecorder()
    application = create_app(
        api_key_store=keys,
        persona_registry=registry,
        llm_client=llm_client,
        usage_recorder=recorder,
        settings=settings,
        start_time=0.0,
    )
    return TestClient(application), recorder


def _consent(client: TestClient, conv: str) -> str:
    r = client.post(
        f"/api/v1/chat/{PERSONA_ID}/consent",
        json={"conversation_id": conv},
        headers={"Authorization": f"Bearer {API_KEY}"},
    )
    assert r.status_code == 200, r.text
    return conv


def _chat(client: TestClient, *, message: str, conv: str, provider_key: str | None = None):
    headers = {"Authorization": f"Bearer {API_KEY}"}
    if provider_key is not None:
        headers[PROVIDER_KEY_HEADER] = provider_key
    return client.post(
        f"/api/v1/chat/{PERSONA_ID}",
        json={"message": message, "conversation_id": conv},
        headers=headers,
    )


# ---------------------------------------------------------------------------
# Settings overlay — the key-separation config surface
# ---------------------------------------------------------------------------


class TestPersonaLLMConfig:
    def test_empty_overlay_inherits_shared_config(self):
        s = Settings(
            llm_provider="zai",
            zai_api_key="shared-key",
            zai_daily_token_limit=1234,
            persona_llm_provider="",
        )
        assert s.to_persona_llm_config() == s.to_llm_config()

    def test_overlay_swaps_provider_and_key_keeps_guardrails(self):
        s = Settings(
            llm_provider="zai",
            zai_api_key="shared-key",
            zai_daily_token_limit=1234,
            zai_thinking="disabled",
            persona_llm_provider="zai",
            persona_llm_api_key="product-key",
        )
        cfg = s.to_persona_llm_config()
        assert cfg.provider is LLMProvider.ZAI
        assert cfg.zai_api_key == "product-key"
        # Guardrails inherit from the shared config.
        assert cfg.zai_daily_token_limit == 1234
        assert cfg.zai_thinking == "disabled"

    def test_overlay_model_and_base_url(self):
        s = Settings(
            llm_provider="openrouter",
            openrouter_api_key="shared-or",
            persona_llm_provider="openrouter",
            persona_llm_api_key="product-or",
            persona_llm_model="z-ai/glm-5.3",
            persona_llm_base_url="https://openrouter.ai/api/v1",
        )
        cfg = s.to_persona_llm_config()
        assert cfg.provider is LLMProvider.OPENROUTER
        assert cfg.openrouter_api_key == "product-or"
        assert cfg.openrouter_model == "z-ai/glm-5.3"
        assert cfg.openrouter_base_url == "https://openrouter.ai/api/v1"

    def test_overlay_unset_slots_keep_shared_values(self):
        s = Settings(
            llm_provider="zai",
            zai_api_key="shared-key",
            persona_llm_provider="zai",
            # persona_llm_api_key intentionally empty
        )
        cfg = s.to_persona_llm_config()
        assert cfg.provider is LLMProvider.ZAI
        assert cfg.zai_api_key == "shared-key"

    def test_unknown_overlay_provider_falls_back_to_fake(self):
        """Mirrors LLMConfig.from_env: unknown provider never wires hosted."""
        s = Settings(llm_provider="zai", persona_llm_provider="not-a-provider")
        cfg = s.to_persona_llm_config()
        assert cfg.provider is LLMProvider.FAKE


# ---------------------------------------------------------------------------
# create_app wiring — the product voice runs on the dedicated key
# ---------------------------------------------------------------------------


class TestProductKeyWiring:
    def test_product_overlay_builds_dedicated_client(self):
        s = Settings(
            llm_provider="zai",
            zai_api_key="shared-key",
            persona_llm_provider="zai",
            persona_llm_api_key="product-key",
        )
        client, _ = _make_app(settings=s)
        app = client.app
        assert isinstance(app.state.llm_client, ZaiLLMClient)
        assert app.state.llm_client._config.zai_api_key == "product-key"
        assert app.state.llm_key_source == "product"

    def test_no_overlay_keeps_shared_posture(self):
        s = Settings(llm_provider="fake")
        client, _ = _make_app(settings=s)
        assert isinstance(client.app.state.llm_client, FakeLLMClient)
        assert client.app.state.llm_key_source == "shared"

    def test_injected_client_wins_and_counts_as_shared(self):
        s = Settings(
            llm_provider="zai",
            persona_llm_provider="zai",
            persona_llm_api_key="product-key",
        )
        injected = FakeLLMClient(persona_name="Chandler")
        client, _recorder = _make_app(settings=s, llm_client=injected)
        assert client.app.state.llm_client is injected
        assert client.app.state.llm_key_source == "shared"

    def test_chat_meters_injected_house_client_as_shared(self):
        s = Settings(
            llm_provider="fake",
            persona_llm_provider="zai",
            persona_llm_api_key="product-key",
        )
        # Injected fake keeps the turn offline; an explicitly wired house
        # client is 'shared' (it is not the dedicated product key).
        client, recorder = _make_app(settings=s, llm_client=FakeLLMClient(persona_name="Chandler"))
        conv = _consent(client, "conv-product")
        assert _chat(client, message="tell me about fishing", conv=conv).status_code == 200
        assert recorder.rows[0].key_source == "shared"


# ---------------------------------------------------------------------------
# BYOK resolution — gate, keying, cache, fallback
# ---------------------------------------------------------------------------


def _byok_settings() -> Settings:
    return Settings(
        llm_provider="zai",
        zai_api_key="shared-key",
        persona_llm_provider="zai",
        persona_llm_api_key="house-product-key",
        byok_enabled=True,
    )


class TestByokResolution:
    def test_gate_closed_ignores_header(self):
        s = _byok_settings()
        s.byok_enabled = False
        client, _ = _make_app(settings=s)
        app = client.app
        llm, source = _resolve_turn_llm(app, "client-secret-key")
        assert llm is app.state.llm_client
        assert source == "product"

    def test_fake_product_voice_ignores_header(self):
        s = Settings(llm_provider="fake", byok_enabled=True)
        client, _ = _make_app(settings=s)
        app = client.app
        llm, source = _resolve_turn_llm(app, "client-secret-key")
        assert llm is app.state.llm_client
        assert source == "shared"

    def test_byok_builds_client_with_key_override_and_caches(self, monkeypatch):
        built: list[dict] = []
        stub = _StubByokClient()

        def fake_builder(config, *, transport=None, **overrides):
            built.append({"config": config, "overrides": overrides})
            return stub

        monkeypatch.setattr("huible.api.app.build_llm_client", fake_builder)
        client, _ = _make_app(settings=_byok_settings())
        app = client.app
        llm1, source1 = _resolve_turn_llm(app, "client-secret-key")
        llm2, source2 = _resolve_turn_llm(app, "client-secret-key")
        assert source1 == source2 == "byok"
        assert llm1 is llm2 is stub
        # build #1 was the create_app house wiring (no overrides); the byok
        # resolution built exactly once and then served from cache.
        byok_builds = [b for b in built if b["overrides"]]
        assert len(byok_builds) == 1
        assert byok_builds[0]["overrides"] == {"zai_api_key": "client-secret-key"}
        # Built from the product overlay (house product key), not shared.
        assert byok_builds[0]["config"].zai_api_key == "house-product-key"

    def test_byok_construction_failure_falls_back_to_house(self, monkeypatch):
        def failing_builder(config, *, transport=None, **overrides):
            raise LLMConfigError("bad key")

        monkeypatch.setattr("huible.api.app.build_llm_client", failing_builder)
        # House client injected so create_app never reaches the patched
        # builder; only the byok resolution path is exercised.
        client, _ = _make_app(settings=_byok_settings(), llm_client=_StubByokClient())
        app = client.app
        llm, source = _resolve_turn_llm(app, "client-secret-key")
        assert llm is app.state.llm_client
        assert source == "shared"

    def test_empty_header_uses_house(self):
        client, _ = _make_app(settings=_byok_settings())
        app = client.app
        llm, source = _resolve_turn_llm(app, "   ")
        assert llm is app.state.llm_client
        assert source == "product"


class TestByokChatPath:
    def test_byok_turn_meters_byok_source_with_caller_attribution(self, monkeypatch):
        stub = _StubByokClient()

        def fake_builder(config, *, transport=None, **overrides):
            return stub

        monkeypatch.setattr("huible.api.app.build_llm_client", fake_builder)
        client, recorder = _make_app(settings=_byok_settings())
        conv = _consent(client, "conv-byok")
        r = _chat(client, message="tell me about fishing", conv=conv, provider_key="client-secret")
        assert r.status_code == 200, r.text
        assert len(recorder.rows) == 1
        row = recorder.rows[0]
        assert row.key_source == "byok"
        # Attribution is the CALLER's bearer digest, never the provider key.
        assert row.api_key_id == api_key_attribution_id(API_KEY)
        assert row.provider == "zai"
        assert row.tokens_in == 11 and row.tokens_out == 7

    def test_byok_disabled_chat_uses_house(self):
        s = _byok_settings()
        s.byok_enabled = False
        client, recorder = _make_app(settings=s, llm_client=FakeLLMClient(persona_name="Chandler"))
        conv = _consent(client, "conv-byok-off")
        r = _chat(client, message="tell me about fishing", conv=conv, provider_key="client-secret")
        assert r.status_code == 200, r.text
        assert recorder.rows[0].key_source == "shared"
        assert recorder.rows[0].provider == "fake"


# ---------------------------------------------------------------------------
# Metering key_source split through the read surface
# ---------------------------------------------------------------------------


class TestKeySourceAggregation:
    def test_in_memory_splits_by_key_source(self):
        from datetime import date

        recorder = InMemoryUsageRecorder()
        day = date(2026, 8, 31)

        def rec(source: str) -> UsageRecord:
            return UsageRecord(
                api_key_id="k1",
                persona_id="p1",
                conversation_id="c1",
                provider="zai",
                model="glm-5.3",
                tokens_in=100,
                tokens_out=50,
                latency_ms=200,
                key_source=source,
                day=day,
            )

        recorder.record_turn(rec("product"))
        recorder.record_turn(rec("product"))
        recorder.record_turn(rec("byok"))
        rows = recorder.daily_aggregates(from_day=day, to_day=day)
        assert len(rows) == 2
        by_source = {r.key_source: r for r in rows}
        assert by_source["product"].requests == 2
        assert by_source["byok"].requests == 1
        assert by_source["product"].tokens_in == 200

    def test_postgres_recorder_key_source_round_trip_on_sqlite(self):
        from datetime import date

        from sqlalchemy.pool import StaticPool

        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        MeteringBase.metadata.create_all(engine)
        recorder = PostgresUsageRecorder("sqlite://", engine=engine)
        day = date(2026, 8, 31)
        for source in ("shared", "product", "byok"):
            recorder.record_turn(
                UsageRecord(
                    api_key_id="k1",
                    persona_id="p1",
                    conversation_id="c1",
                    provider="zai",
                    model="glm-5.3",
                    tokens_in=100,
                    tokens_out=50,
                    latency_ms=200,
                    key_source=source,
                    day=day,
                )
            )
        rows = recorder.daily_aggregates(from_day=day, to_day=day)
        assert {r.key_source for r in rows} == {"shared", "product", "byok"}
        assert all(r.requests == 1 for r in rows)
        recorder.close()

    def test_endpoint_surfaces_key_source(self, monkeypatch):
        stub = _StubByokClient()
        monkeypatch.setattr(
            "huible.api.app.build_llm_client",
            lambda config, *, transport=None, **overrides: stub,
        )
        # House client injected (offline stub) so the no-header turn also
        # completes without network; it meters as 'shared' (injected house).
        client, _ = _make_app(settings=_byok_settings(), llm_client=_StubByokClient())
        conv = _consent(client, "conv-endpoint")
        assert (
            _chat(client, message="hi", conv=conv, provider_key="client-secret").status_code == 200
        )
        assert _chat(client, message="hi again", conv=conv).status_code == 200
        r = client.get(
            "/api/v1/usage/daily",
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        assert r.status_code == 200, r.text
        rows = r.json()["data"]["rows"]
        sources = {row["key_source"] for row in rows}
        assert sources == {"shared", "byok"}

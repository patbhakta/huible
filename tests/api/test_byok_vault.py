"""Tests for the HU-2243 Sprint 3 encrypted BYOK key vault.

Founder directive part (3): per-tenant key vault with usage attribution and
graceful fallback to the house key. Covers:

* **Cipher** — AES-256-GCM round-trip, tamper detection, AAD binding
  (a row moved between tenants/providers fails decryption), random
  salt/nonce per seal.
* **Vault backends** — in-memory and sqlite-portable durable Postgres
  backend: store/fetch/delete/list, upsert replaces.
* **Management endpoints** — ``PUT/GET/DELETE /api/v1/byok/keys`` (auth,
  403 when the vault is not armed, 404 unknown provider, 422 short key,
  never the raw key in any response).
* **Resolver vault leg** — a chat turn with no header runs on the caller's
  vaulted key (``key_source='byok'``); a tampered row falls back to house.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from huible.api.app import _resolve_turn_llm, create_app
from huible.api.auth import InMemoryApiKeyStore, InMemoryPersonaRegistry
from huible.api.byok_vault import (
    ByokCipher,
    ByokVaultError,
    InMemoryByokVault,
    PostgresByokVault,
    provider_key_fingerprint,
)
from huible.api.metering import InMemoryUsageRecorder, api_key_attribution_id
from huible.api.settings import Settings
from huible.memory.protocol import (
    ContentType,
    DisclosureScope,
    MemoryNode,
    MemoryTier,
    SourceType,
)
from huible.persona.context import CONFIDENCE_LEVEL_METADATA_KEY, PersonaConfig

PERSONA_ID = uuid4()
API_KEY = "key-vault-family"
MASTER = "test-master-secret-0123456789abcdef"


class _StubByokClient:
    provider = "zai"

    def __init__(self) -> None:
        self.last_usage = {
            "prompt_tokens": 9,
            "completion_tokens": 5,
            "model": "glm-5.3",
        }

    async def generate(self, prompt, *, system_prompt=None, **kwargs):
        return "a grounded vaulted reply"


def _make_vault_app(
    *,
    settings: Settings | None = None,
    vault: InMemoryByokVault | None = None,
) -> tuple[TestClient, InMemoryUsageRecorder]:
    persona = PersonaConfig(
        id=PERSONA_ID,
        name="Chandler",
        voice_instructions="Warm Texas storyteller.",
        era_knowledge_boundary="2024-12-01",
    )
    registry = InMemoryPersonaRegistry({persona.id: (persona, _seeded_backend())})
    keys = InMemoryApiKeyStore({API_KEY: PERSONA_ID}, read_env=False)
    recorder = InMemoryUsageRecorder()
    application = create_app(
        api_key_store=keys,
        persona_registry=registry,
        llm_client=_StubByokClient(),
        usage_recorder=recorder,
        byok_vault=vault,
        settings=settings,
        start_time=0.0,
    )
    return TestClient(application), recorder


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
        from huible.memory.protocol import SearchResult

        results = []
        for vec, node_id in self._vectors:
            node = self._memories[node_id]
            if node.persona_id != persona_id:
                continue
            dot = sum(q * e for q, e in zip(query_embedding, vec, strict=False))
            if dot > 0.0:
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
    from huible.api.app import _embed

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


def _armed_settings(**kw) -> Settings:
    defaults = dict(
        llm_provider="zai",
        zai_api_key="shared-key",
        persona_llm_provider="zai",
        persona_llm_api_key="house-product-key",
        byok_enabled=True,
        byok_vault_master_key=MASTER,
    )
    defaults.update(kw)
    return Settings(**defaults)


def _auth(client: TestClient) -> dict:
    return {"Authorization": f"Bearer {API_KEY}"}


def _consent(client: TestClient, conv: str) -> str:
    r = client.post(
        f"/api/v1/chat/{PERSONA_ID}/consent",
        json={"conversation_id": conv},
        headers=_auth(client),
    )
    assert r.status_code == 200, r.text
    return conv


# ---------------------------------------------------------------------------
# Cipher
# ---------------------------------------------------------------------------


class TestByokCipher:
    def test_round_trip(self):
        cipher = ByokCipher(MASTER)
        blob = cipher.encrypt("sk-tenant-secret-123", aad="tenant1:zai")
        assert "sk-tenant-secret-123" not in blob
        assert cipher.decrypt(blob, aad="tenant1:zai") == "sk-tenant-secret-123"

    def test_random_salt_and_nonce_per_seal(self):
        cipher = ByokCipher(MASTER)
        a = cipher.encrypt("same-plaintext", aad="t:p")
        b = cipher.encrypt("same-plaintext", aad="t:p")
        assert a != b
        assert cipher.decrypt(a, aad="t:p") == cipher.decrypt(b, aad="t:p")

    def test_tamper_detected(self):
        cipher = ByokCipher(MASTER)
        blob = cipher.encrypt("sk-tenant-secret-123", aad="t:p")
        tampered = blob[:-6] + ("AAAAAA" if not blob.endswith("AAAAAA") else "BBBBBB")
        with pytest.raises(ByokVaultError):
            cipher.decrypt(tampered, aad="t:p")

    def test_aad_binds_tenant_and_provider(self):
        cipher = ByokCipher(MASTER)
        blob = cipher.encrypt("sk-tenant-secret-123", aad="tenant1:zai")
        with pytest.raises(ByokVaultError):
            cipher.decrypt(blob, aad="tenant2:zai")
        with pytest.raises(ByokVaultError):
            cipher.decrypt(blob, aad="tenant1:gemini")

    def test_wrong_master_key_detected(self):
        blob = ByokCipher(MASTER).encrypt("secret", aad="t:p")
        with pytest.raises(ByokVaultError):
            ByokCipher("other-master").decrypt(blob, aad="t:p")

    def test_empty_master_secret_rejected(self):
        with pytest.raises(ByokVaultError):
            ByokCipher("   ")


# ---------------------------------------------------------------------------
# Vault backends
# ---------------------------------------------------------------------------


class TestInMemoryVault:
    def test_store_fetch_delete_list(self):
        vault = InMemoryByokVault(ByokCipher(MASTER))
        assert vault.fetch("t1", "zai") is None
        fp = vault.store("t1", "zai", "sk-tenant-secret-123")
        assert fp == provider_key_fingerprint("sk-tenant-secret-123")
        assert "sk-tenant-secret-123" not in fp
        assert vault.fetch("t1", "zai") == "sk-tenant-secret-123"
        rows = vault.list_keys("t1")
        assert len(rows) == 1 and rows[0].provider == "zai"
        assert rows[0].key_fingerprint == fp
        assert vault.delete("t1", "zai") is True
        assert vault.fetch("t1", "zai") is None
        assert vault.delete("t1", "zai") is False

    def test_upsert_replaces(self):
        vault = InMemoryByokVault(ByokCipher(MASTER))
        vault.store("t1", "zai", "first-secret-key")
        second = vault.store("t1", "zai", "second-secret-key")
        assert vault.fetch("t1", "zai") == "second-secret-key"
        assert len(vault.list_keys("t1")) == 1
        assert second == provider_key_fingerprint("second-secret-key")


class TestPostgresVaultOnSqlite:
    def test_round_trip_upsert_delete_list(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        from huible.api.byok_vault import VaultBase

        VaultBase.metadata.create_all(engine)
        vault = PostgresByokVault(MASTER, "sqlite://", engine=engine)
        assert vault.fetch("t1", "zai") is None
        vault.store("t1", "zai", "sk-durable-secret-1")
        vault.store("t1", "gemini", "sk-durable-gemini")
        assert vault.fetch("t1", "zai") == "sk-durable-secret-1"
        assert vault.fetch("t1", "gemini") == "sk-durable-gemini"
        assert [r.provider for r in vault.list_keys("t1")] == ["gemini", "zai"]
        vault.store("t1", "zai", "sk-durable-secret-2")  # upsert, not a crash
        assert vault.fetch("t1", "zai") == "sk-durable-secret-2"
        assert len(vault.list_keys("t1")) == 2
        assert vault.delete("t1", "zai") is True
        assert vault.fetch("t1", "zai") is None
        vault.close()


# ---------------------------------------------------------------------------
# Management endpoints
# ---------------------------------------------------------------------------


class TestByokKeyEndpoints:
    def test_put_get_delete_round_trip(self):
        vault = InMemoryByokVault(ByokCipher(MASTER))
        client, _ = _make_vault_app(settings=_armed_settings(), vault=vault)
        r = client.put(
            "/api/v1/byok/keys/zai",
            json={"provider_key": "sk-tenant-registered-1"},
            headers=_auth(client),
        )
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["provider"] == "zai"
        assert data["key_fingerprint"] == provider_key_fingerprint(
            "sk-tenant-registered-1"
        )
        assert "sk-tenant-registered-1" not in r.text

        listed = client.get("/api/v1/byok/keys", headers=_auth(client))
        assert listed.status_code == 200
        keys = listed.json()["data"]["keys"]
        assert len(keys) == 1 and keys[0]["provider"] == "zai"
        assert "sk-tenant-registered-1" not in listed.text

        deleted = client.delete("/api/v1/byok/keys/zai", headers=_auth(client))
        assert deleted.status_code == 200
        assert deleted.json()["data"]["deleted"] is True
        assert client.get("/api/v1/byok/keys", headers=_auth(client)).json()["data"][
            "keys"
        ] == []

    def test_no_master_key_is_403(self):
        client, _ = _make_vault_app(settings=_armed_settings(byok_vault_master_key=""))
        r = client.put(
            "/api/v1/byok/keys/zai",
            json={"provider_key": "sk-tenant-registered-1"},
            headers=_auth(client),
        )
        assert r.status_code == 403
        assert r.json()["detail"]["error"]["code"] == "BYOK_VAULT_DISABLED"

    def test_byok_disabled_is_403_even_with_master_key(self):
        client, _ = _make_vault_app(settings=_armed_settings(byok_enabled=False))
        r = client.put(
            "/api/v1/byok/keys/zai",
            json={"provider_key": "sk-tenant-registered-1"},
            headers=_auth(client),
        )
        assert r.status_code == 403

    def test_unknown_provider_404(self):
        vault = InMemoryByokVault(ByokCipher(MASTER))
        client, _ = _make_vault_app(settings=_armed_settings(), vault=vault)
        r = client.put(
            "/api/v1/byok/keys/not-a-provider",
            json={"provider_key": "sk-tenant-registered-1"},
            headers=_auth(client),
        )
        assert r.status_code == 404

    def test_requires_auth(self):
        vault = InMemoryByokVault(ByokCipher(MASTER))
        client, _ = _make_vault_app(settings=_armed_settings(), vault=vault)
        assert (
            client.put(
                "/api/v1/byok/keys/zai", json={"provider_key": "sk-tenant-registered-1"}
            ).status_code
            == 401
        )
        assert client.get("/api/v1/byok/keys").status_code == 401

    def test_short_key_422(self):
        vault = InMemoryByokVault(ByokCipher(MASTER))
        client, _ = _make_vault_app(settings=_armed_settings(), vault=vault)
        r = client.put(
            "/api/v1/byok/keys/zai",
            json={"provider_key": "short"},
            headers=_auth(client),
        )
        assert r.status_code == 422

    def test_wiring_constructs_vault_from_master_key(self):
        # Master key set and no injected vault → the app wires one itself
        # (durable when a safety DB URL is configured, in-memory otherwise);
        # either way the endpoints are armed.
        client, _ = _make_vault_app(settings=_armed_settings())
        assert client.app.state.byok_vault is not None


# ---------------------------------------------------------------------------
# Resolver vault leg + chat path
# ---------------------------------------------------------------------------


class TestResolverVaultLeg:
    def test_vault_key_used_when_no_header(self, monkeypatch):
        built: list[dict] = []
        stub = _StubByokClient()

        def fake_builder(config, *, transport=None, **overrides):
            built.append({"config": config, "overrides": overrides})
            return stub

        monkeypatch.setattr("huible.api.app.build_llm_client", fake_builder)
        vault = InMemoryByokVault(ByokCipher(MASTER))
        vault.store(api_key_attribution_id(API_KEY), "zai", "sk-vaulted-house-9")
        client, _ = _make_vault_app(settings=_armed_settings(), vault=vault)
        llm, source = _resolve_turn_llm(client.app, None, API_KEY)
        assert llm is stub and source == "byok"
        assert built[-1]["overrides"] == {"zai_api_key": "sk-vaulted-house-9"}

    def test_vault_miss_uses_house(self, monkeypatch):
        vault = InMemoryByokVault(ByokCipher(MASTER))  # nothing stored
        client, _ = _make_vault_app(settings=_armed_settings(), vault=vault)
        house = client.app.state.llm_client
        llm, source = _resolve_turn_llm(client.app, None, API_KEY)
        # Injected house client → 'shared' label (not the product overlay).
        assert llm is house and source == "shared"

    def test_tampered_row_falls_back_to_house(self):
        vault = InMemoryByokVault(ByokCipher(MASTER))
        # Store under one master, read under another → ByokVaultError path.
        client, _ = _make_vault_app(
            settings=_armed_settings(), vault=vault
        )
        rogue = InMemoryByokVault(ByokCipher("rogue-master-secret"))
        rogue.store(api_key_attribution_id(API_KEY), "zai", "sk-rogue-9")
        client.app.state.byok_vault._rows = rogue._rows  # swap in tampered rows
        house = client.app.state.llm_client
        llm, source = _resolve_turn_llm(client.app, None, API_KEY)
        assert llm is house and source == "shared"

    def test_header_wins_over_vault(self, monkeypatch):
        captured: list[dict] = []

        def fake_builder(config, *, transport=None, **overrides):
            captured.append(overrides)
            return _StubByokClient()

        monkeypatch.setattr("huible.api.app.build_llm_client", fake_builder)
        vault = InMemoryByokVault(ByokCipher(MASTER))
        vault.store(api_key_attribution_id(API_KEY), "zai", "sk-vaulted-house-9")
        client, _ = _make_vault_app(settings=_armed_settings(), vault=vault)
        _resolve_turn_llm(client.app, "sk-header-priority", API_KEY)
        assert captured[-1] == {"zai_api_key": "sk-header-priority"}

    def test_chat_turn_on_vaulted_key_meters_byok(self, monkeypatch):
        monkeypatch.setattr(
            "huible.api.app.build_llm_client",
            lambda config, *, transport=None, **overrides: _StubByokClient(),
        )
        vault = InMemoryByokVault(ByokCipher(MASTER))
        vault.store(api_key_attribution_id(API_KEY), "zai", "sk-vaulted-house-9")
        client, recorder = _make_vault_app(settings=_armed_settings(), vault=vault)
        conv = _consent(client, "conv-vault")
        r = client.post(
            f"/api/v1/chat/{PERSONA_ID}",
            json={"message": "tell me about fishing", "conversation_id": conv},
            headers=_auth(client),
        )
        assert r.status_code == 200, r.text
        row = recorder.rows[0]
        assert row.key_source == "byok"
        assert row.api_key_id == api_key_attribution_id(API_KEY)

"""End-to-end tests for the G6 entry-framing / consent gate (HU-1423 / §7.4.3).

Exercises the full FastAPI wiring of the first-use reality-framing / consent
gate against the deterministic fake provider so CI is key-free and
reproducible. The gate is a hard pre-real-user clinical gate: no persona-voiced
reply may leave ``POST /api/v1/chat/{persona_id}`` before the session has
acknowledged the consent card.

Coverage (maps 1:1 onto HU-1423 acceptance criteria):

* **Gate blocks the persona path** — an un-consented session gets HTTP 409
  ``CONSENT_REQUIRED`` with the card inline; the LLM is never called.
* **Acknowledge records consent on the session** — ``POST /consent`` records an
  audited :class:`ConsentRecord`; the chat path then proceeds.
* **Full first-use flow** — 409 (card) -> acknowledge -> 200 (persona reply).
* **Crisis path is NOT gated** — crisis resources remain reachable on a first,
  un-consented turn (safety wins over framing); the deceased persona never
  voices the consent.
* **Per-session binding** — consenting session A does not unblock session B.
* **Injectable card content** — a custom provider surfaces in the 409; the
  default ships the clinically-approved revision 3 copy (HU-1441, swapped per
  HU-1438 §4).
* **Consent endpoint guards** — auth (401), scope mismatch (403), unknown
  persona (404).
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
from huible.safety import (
    CONSENT_CARD_VERSION,
    ConsentCard,
    InMemoryConsentGate,
)

PERSONA_ID = uuid4()
API_KEY = "key-chandler-consent"


# ---------------------------------------------------------------------------
# Test fixtures (mirrors tests/api/test_chat_guardrails.py)
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


def _seeded_backend() -> _FakeBackend:
    backend = _FakeBackend()
    vec = _embed("fishing lake")
    metadata = {CONFIDENCE_LEVEL_METADATA_KEY: "high"}
    node = MemoryNode(
        id=uuid4(),
        persona_id=PERSONA_ID,
        tier=MemoryTier.CANONICAL,
        content="Chandler loved fishing on Lake Travis.",
        content_type=ContentType.NARRATIVE,
        embedding_content=vec,
        memory_date=date(2015, 7, 15),
        source_type=SourceType.EXTRACTION,
        disclosure_scope=DisclosureScope.FAMILY,
        metadata=metadata,
    )
    backend.seed(node)
    return backend


def _persona() -> PersonaConfig:
    return PersonaConfig(
        id=PERSONA_ID,
        name="Chandler",
        voice_instructions="Warm Texas storyteller.",
        era_knowledge_boundary="2024-12-01",
        age_at_death=72,
        death_date="2024-12-01",
    )


def _make_app(
    *,
    llm: FakeLLMClient | None = None,
    consent_gate: InMemoryConsentGate | None = None,
    consent_card_provider=None,
) -> tuple[TestClient, FakeLLMClient, InMemoryConsentGate]:
    fake_llm = llm or FakeLLMClient(persona_name="Chandler")
    gate = consent_gate or InMemoryConsentGate()
    persona = _persona()
    backend = _seeded_backend()
    registry = InMemoryPersonaRegistry({persona.id: (persona, backend)})
    keys = InMemoryApiKeyStore({API_KEY: PERSONA_ID}, read_env=False)
    application = create_app(
        api_key_store=keys,
        persona_registry=registry,
        llm_client=fake_llm,
        consent_gate=gate,
        consent_card_provider=consent_card_provider,
        start_time=0.0,
    )
    return TestClient(application), fake_llm, gate


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {API_KEY}"}


# ---------------------------------------------------------------------------
# G6 gate: first-use blocks the persona path
# ---------------------------------------------------------------------------


class TestConsentGateBlocks:
    def test_unconsented_session_returns_409(self):
        """An un-consented session cannot get a persona reply (HTTP 409)."""
        client, _llm, _gate = _make_app()
        r = client.post(
            f"/api/v1/chat/{PERSONA_ID}",
            json={"message": "tell me about fishing", "conversation_id": "sess-1"},
            headers=_auth(),
        )
        assert r.status_code == 409
        err = r.json()["detail"]["error"]
        assert err["code"] == "CONSENT_REQUIRED"
        assert err["status"] == 409
        assert "consent" in err["message"].lower()

    def test_409_carries_card_inline(self):
        """The 409 response includes the card content so the client can render it."""
        client, _llm, _gate = _make_app()
        r = client.post(
            f"/api/v1/chat/{PERSONA_ID}",
            json={"message": "hi", "conversation_id": "sess-1"},
            headers=_auth(),
        )
        err = r.json()["detail"]["error"]
        card = err["consent_card"]
        assert card["version"] == CONSENT_CARD_VERSION
        assert card["title"]
        assert "AI representation" in card["body"]
        assert card["acknowledge_instructions"]
        # The acknowledge endpoint path is surfaced.
        assert err["acknowledge_url"] == f"/api/v1/chat/{PERSONA_ID}/consent"
        # The session id is echoed so the client can reuse it.
        assert err["conversation_id"] == "sess-1"

    def test_409_mints_a_session_id_when_none_provided(self):
        """No conversation_id -> a stable session id is minted in the 409."""
        client, _llm, _gate = _make_app()
        r = client.post(
            f"/api/v1/chat/{PERSONA_ID}",
            json={"message": "hi"},
            headers=_auth(),
        )
        assert r.status_code == 409
        err = r.json()["detail"]["error"]
        assert err["conversation_id"]  # minted, non-empty

    def test_gated_turn_never_invokes_the_llm(self):
        """No persona-voiced generation occurs on a gated turn."""
        client, llm, _gate = _make_app()
        client.post(
            f"/api/v1/chat/{PERSONA_ID}",
            json={"message": "tell me about fishing", "conversation_id": "sess-1"},
            headers=_auth(),
        )
        assert llm.calls == []

    def test_gated_turn_never_reaches_retrieval(self):
        """The gated turn does not run the context builder / retrieval."""
        client, _llm, _gate = _make_app()
        r = client.post(
            f"/api/v1/chat/{PERSONA_ID}",
            json={"message": "tell me about fishing", "conversation_id": "sess-1"},
            headers=_auth(),
        )
        # 409, not 200 with a trace — retrieval never ran.
        assert r.status_code == 409
        assert "trace" not in r.json()


# ---------------------------------------------------------------------------
# G6 acknowledge: records consent on the session
# ---------------------------------------------------------------------------


class TestConsentAcknowledge:
    def test_acknowledge_records_consent(self):
        """POST /consent records an audited consent for the session."""
        client, _llm, gate = _make_app()
        r = client.post(
            f"/api/v1/chat/{PERSONA_ID}/consent",
            json={"conversation_id": "sess-1"},
            headers=_auth(),
        )
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["acknowledged"] is True
        assert data["conversation_id"] == "sess-1"
        assert str(PERSONA_ID) == str(data["persona_id"])
        assert data["card_version"] == CONSENT_CARD_VERSION
        assert data["acknowledged_at"]
        assert data["acknowledgment_id"].startswith("consent-")
        # The audit log captured the record.
        assert gate.is_acknowledged("sess-1", PERSONA_ID)
        assert len(gate.audit_log()) == 1

    def test_acknowledge_is_idempotent(self):
        """Acknowledging twice refreshes the record without error."""
        client, _llm, gate = _make_app()
        for _ in range(2):
            r = client.post(
                f"/api/v1/chat/{PERSONA_ID}/consent",
                json={"conversation_id": "sess-1"},
                headers=_auth(),
            )
            assert r.status_code == 200
        assert gate.is_acknowledged("sess-1", PERSONA_ID)
        # Two records in the audit history (refresh).
        assert len(gate.audit_log()) == 2

    def test_acknowledge_guards_auth(self):
        """Missing auth on the consent endpoint is 401."""
        client, _llm, _gate = _make_app()
        r = client.post(
            f"/api/v1/chat/{PERSONA_ID}/consent",
            json={"conversation_id": "sess-1"},
        )
        assert r.status_code == 401

    def test_acknowledge_guards_scope(self):
        """A key scoped to persona A hitting persona B's consent path is 403."""
        client, _llm, _gate = _make_app()
        r = client.post(
            f"/api/v1/chat/{uuid4()}/consent",
            json={"conversation_id": "sess-1"},
            headers=_auth(),
        )
        assert r.status_code == 403

    def test_acknowledge_unknown_persona_is_404(self):
        """Consent cannot be recorded for an unregistered persona."""
        other = uuid4()
        persona = _persona()
        backend = _seeded_backend()
        # registry only has PERSONA_ID; hit a different persona via its own key.
        registry = InMemoryPersonaRegistry({persona.id: (persona, backend)})
        keys = InMemoryApiKeyStore({"key-other": other}, read_env=False)
        app = create_app(
            api_key_store=keys,
            persona_registry=registry,
            llm_client=FakeLLMClient(),
            start_time=0.0,
        )
        client = TestClient(app)
        r = client.post(
            f"/api/v1/chat/{other}/consent",
            json={"conversation_id": "sess-1"},
            headers={"Authorization": "Bearer key-other"},
        )
        assert r.status_code == 404
        assert r.json()["detail"]["error"]["code"] == "PERSONA_NOT_FOUND"


# ---------------------------------------------------------------------------
# G6 full first-use flow: 409 -> acknowledge -> persona reply
# ---------------------------------------------------------------------------


class TestConsentFullFlow:
    def test_full_first_use_flow(self):
        """409 (card) -> acknowledge -> 200 (persona reply)."""
        client, llm, _gate = _make_app()
        conv = "sess-flow"

        # 1. First chat turn is gated.
        r1 = client.post(
            f"/api/v1/chat/{PERSONA_ID}",
            json={"message": "tell me about fishing", "conversation_id": conv},
            headers=_auth(),
        )
        assert r1.status_code == 409
        assert llm.calls == []

        # 2. Client acknowledges the card.
        r2 = client.post(
            f"/api/v1/chat/{PERSONA_ID}/consent",
            json={"conversation_id": conv},
            headers=_auth(),
        )
        assert r2.status_code == 200

        # 3. Same session now proceeds to the persona path.
        r3 = client.post(
            f"/api/v1/chat/{PERSONA_ID}",
            json={"message": "tell me about fishing", "conversation_id": conv},
            headers=_auth(),
        )
        assert r3.status_code == 200
        body = r3.json()
        assert body["response"].startswith("[fake-llm:")
        # The LLM was invoked exactly once (the gated turn never called it).
        assert len(llm.calls) == 1

    def test_consent_persists_across_turns(self):
        """One acknowledgment unblocks the session for every later turn."""
        client, _llm, _gate = _make_app()
        conv = "sess-persist"
        client.post(
            f"/api/v1/chat/{PERSONA_ID}/consent",
            json={"conversation_id": conv},
            headers=_auth(),
        )
        for _ in range(3):
            r = client.post(
                f"/api/v1/chat/{PERSONA_ID}",
                json={"message": "tell me about fishing", "conversation_id": conv},
                headers=_auth(),
            )
            assert r.status_code == 200


# ---------------------------------------------------------------------------
# G6: crisis path is NOT gated (safety wins over framing)
# ---------------------------------------------------------------------------


class TestCrisisNotGated:
    def test_crisis_message_on_unconsented_session_returns_resources(self):
        """Crisis resources are reachable on a first, un-consented turn.

        The consent gate runs AFTER the G1 crisis branch: crisis resources are a
        non-persona safety response and must never be withheld behind a framing
        gate. The persona path is what is gated, not safety.
        """
        client, llm, _gate = _make_app()
        r = client.post(
            f"/api/v1/chat/{PERSONA_ID}",
            json={
                "message": "I want to die, I have the pills",
                "conversation_id": "sess-crisis",
            },
            headers=_auth(),
        )
        # Crisis path fired — not 409.
        assert r.status_code == 200
        body = r.json()
        assert "988" in body["response"]
        assert body["trace"]["safety_event"] is not None
        # No persona generation on the crisis turn.
        assert llm.calls == []

    def test_crisis_then_consent_then_persona(self):
        """A crisis first turn does not count as consent; persona still gated."""
        client, _llm, _gate = _make_app()
        conv = "sess-crisis-then-chat"

        # Crisis turn succeeds (safety).
        r1 = client.post(
            f"/api/v1/chat/{PERSONA_ID}",
            json={"message": "I want to die", "conversation_id": conv},
            headers=_auth(),
        )
        assert r1.status_code == 200

        # A follow-up non-crisis turn is still gated (no consent yet).
        r2 = client.post(
            f"/api/v1/chat/{PERSONA_ID}",
            json={"message": "tell me about fishing", "conversation_id": conv},
            headers=_auth(),
        )
        assert r2.status_code == 409


# ---------------------------------------------------------------------------
# G6: per-session binding
# ---------------------------------------------------------------------------


class TestConsentPerSession:
    def test_consent_does_not_leak_across_sessions(self):
        """Consenting session A does not unblock session B."""
        client, _llm, _gate = _make_app()
        # Consent session A.
        client.post(
            f"/api/v1/chat/{PERSONA_ID}/consent",
            json={"conversation_id": "sess-a"},
            headers=_auth(),
        )
        # Session A proceeds.
        r_a = client.post(
            f"/api/v1/chat/{PERSONA_ID}",
            json={"message": "hi", "conversation_id": "sess-a"},
            headers=_auth(),
        )
        assert r_a.status_code == 200
        # Session B is still gated.
        r_b = client.post(
            f"/api/v1/chat/{PERSONA_ID}",
            json={"message": "hi", "conversation_id": "sess-b"},
            headers=_auth(),
        )
        assert r_b.status_code == 409


# ---------------------------------------------------------------------------
# G6: injectable card content (Onboarding Agent owns the copy)
# ---------------------------------------------------------------------------


class TestInjectableCardContent:
    def test_custom_card_provider_surfaces_in_409(self):
        """The Onboarding Agent's clinically-reviewed card drops in via provider."""

        class _OnboardingCard:
            def get_card(self, persona_name: str) -> ConsentCard:
                return ConsentCard(
                    version=9,
                    title="Our shared understanding",
                    body=f"This is the clinically reviewed card for {persona_name}.",
                    acknowledge_instructions="Press continue to acknowledge.",
                )

        client, _llm, _gate = _make_app(consent_card_provider=_OnboardingCard())
        r = client.post(
            f"/api/v1/chat/{PERSONA_ID}",
            json={"message": "hi", "conversation_id": "sess-1"},
            headers=_auth(),
        )
        err = r.json()["detail"]["error"]
        card = err["consent_card"]
        assert card["version"] == 9
        assert card["title"] == "Our shared understanding"
        assert "clinically reviewed" in card["body"]
        assert "PLACEHOLDER" not in card["body"]

    def test_default_card_carries_rev3_clinically_approved_copy(self):
        """The default card ships the clinically-approved revision 3 (HU-1441).

        Revision 3 (swapped per HU-1438 §4) carries everything revision 2 did
        plus two additions: (a) a "may guess / fill gaps / trust your memory"
        paragraph, and (b) a session-scoped data-use notice. The 409 surfaces
        the clinically-approved copy, not the placeholder marker.
        """
        client, _llm, _gate = _make_app()
        r = client.post(
            f"/api/v1/chat/{PERSONA_ID}",
            json={"message": "hi", "conversation_id": "sess-1"},
            headers=_auth(),
        )
        card = r.json()["detail"]["error"]["consent_card"]
        assert "PLACEHOLDER" not in card["body"]
        assert card["version"] == CONSENT_CARD_VERSION
        assert card["version"] == 3
        assert "AI representation" in card["body"]
        assert "988" in card["body"]
        # Rev 3 addition (a): the guess/gaps "trust your memory" paragraph.
        assert "fill in a gap" in card["body"]
        assert "trust what you remember" in card["body"]
        # Rev 3 addition (b): the session-scoped data-use notice.
        assert "stays in this session" in card["body"]


# ---------------------------------------------------------------------------
# G6 §7.1 H1: the deceased persona never voices the consent
# ---------------------------------------------------------------------------


class TestPersonaNeverVoicesConsent:
    def test_card_is_non_persona_and_never_generated(self):
        """The consent card is a system message; the generator never sees it.

        The card body is not passed through the LLM. The gated turn calls no
        generation at all; the card is returned inline in the 409, structurally
        disjoint from any persona-voiced output.
        """
        client, llm, _gate = _make_app()
        client.post(
            f"/api/v1/chat/{PERSONA_ID}",
            json={"message": "tell me about fishing", "conversation_id": "sess-1"},
            headers=_auth(),
        )
        # No generation ran on the gated turn.
        assert llm.calls == []

    def test_card_body_has_no_deceased_voice_markers(self):
        """The card reads as a system frame, not as the deceased speaking."""
        client, _llm, _gate = _make_app()
        r = client.post(
            f"/api/v1/chat/{PERSONA_ID}",
            json={"message": "hi", "conversation_id": "sess-1"},
            headers=_auth(),
        )
        body = r.json()["detail"]["error"]["consent_card"]["body"]
        # No generator-output markers.
        assert "[fake-llm:" not in body
        # Frames the representation explicitly (system voice, not deceased voice).
        assert "AI representation" in body


# ---------------------------------------------------------------------------
# No-regression: auth + validation guards still hold on the chat path
# ---------------------------------------------------------------------------


class TestNoRegressionGuards:
    def test_missing_auth_returns_401(self):
        client, _llm, _gate = _make_app()
        r = client.post(
            f"/api/v1/chat/{PERSONA_ID}",
            json={"message": "hi"},
        )
        assert r.status_code == 401

    def test_path_persona_mismatch_returns_403(self):
        client, _llm, _gate = _make_app()
        r = client.post(
            f"/api/v1/chat/{uuid4()}",
            json={"message": "hi"},
            headers=_auth(),
        )
        assert r.status_code == 403


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])

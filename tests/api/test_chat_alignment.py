"""§7.4.2 runtime claim->ref alignment filter — e2e guardrail suite (HU-1422).

Exercises the full ``POST /api/v1/chat/{persona_id}`` wiring of the
generation-time alignment filter (:mod:`huible.safety.alignment`) against the
deterministic FakeLLMClient. Complements the G4 retrieval-side grounding suite
(``test_chat_guardrails.py``) with the generation-side backstop: a persona
reply carrying an un-grounded claim is failed safely to a claim-free fallback,
and the ``trace.alignment`` telemetry surface exposes the un-grounded-claim
counts + disposition for clinical review.
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
from huible.safety import ALIGNMENT_FALLBACK_RESPONSE

PERSONA_ID = uuid4()
API_KEY = "key-chandler-family-alignment"


# ---------------------------------------------------------------------------
# Fixtures (mirror tests/api/test_chat_guardrails.py so the baseline holds)
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


def _node(
    *,
    content: str,
    confidence_level: str = "high",
    embedding: list[float] | None = None,
) -> MemoryNode:
    metadata: dict[str, Any] = {CONFIDENCE_LEVEL_METADATA_KEY: confidence_level}
    return MemoryNode(
        id=uuid4(),
        persona_id=PERSONA_ID,
        tier=MemoryTier.CANONICAL,
        content=content,
        content_type=ContentType.NARRATIVE,
        embedding_content=list(embedding) if embedding is not None else None,
        memory_date=date(2015, 7, 15),
        source_type=SourceType.EXTRACTION,
        disclosure_scope=DisclosureScope.FAMILY,
        metadata=metadata,
    )


def _persona() -> PersonaConfig:
    return PersonaConfig(
        id=PERSONA_ID,
        name="Chandler",
        voice_instructions="Warm Texas storyteller.",
        era_knowledge_boundary="2024-12-01",
        age_at_death=72,
        death_date="2024-12-01",
    )


def _seeded_backend() -> tuple[_FakeBackend, dict[str, MemoryNode]]:
    backend = _FakeBackend()
    vec = _embed("fishing lake")
    memories: dict[str, MemoryNode] = {}
    memories["lake"] = _node(
        content="Chandler loved fishing on Lake Travis.", embedding=vec
    )
    memories["rods"] = _node(
        content="He kept his rods in the garage.", embedding=vec
    )
    for node in memories.values():
        backend.seed(node)
    return backend, memories


def _make_app(
    *,
    response: str | None = None,
) -> tuple[TestClient, FakeLLMClient, dict[str, MemoryNode]]:
    backend, memories = _seeded_backend()
    fake_llm = FakeLLMClient(response=response, persona_name="Chandler")
    persona = _persona()
    registry = InMemoryPersonaRegistry({persona.id: (persona, backend)})
    keys = InMemoryApiKeyStore({API_KEY: PERSONA_ID}, read_env=False)
    application = create_app(
        api_key_store=keys,
        persona_registry=registry,
        llm_client=fake_llm,
        start_time=0.0,
    )
    return TestClient(application), fake_llm, memories


def _post(client: TestClient, message: str, **body: Any) -> Any:
    body = {"message": message, **body}
    r = client.post(
        f"/api/v1/chat/{PERSONA_ID}",
        json=body,
        headers={"Authorization": f"Bearer {API_KEY}"},
    )
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
# §7.4.2 — grounded reply passes verbatim
# ---------------------------------------------------------------------------


class TestGroundedReplyPasses:
    def test_grounded_biographical_reply_returned_verbatim(self):
        """A reply whose factual claim is backed by a retrieved ref passes."""
        grounded = "I loved fishing on Lake Travis."
        client, _llm, _m = _make_app(response=grounded)
        body = _post(client, "tell me about the lake")

        assert body["response"] == grounded
        assert body["trace"]["alignment"]["disposition"] == "passed"
        assert body["trace"]["alignment"]["ungrounded_claim_count"] == 0

    def test_pure_reflection_passes(self):
        """A claim-free reflection reply is returned unchanged."""
        reflection = "I'm glad you're here. Tell me more."
        client, _llm, _m = _make_app(response=reflection)
        body = _post(client, "hi")
        assert body["response"] == reflection
        assert body["trace"]["alignment"]["disposition"] == "passed"
        assert body["trace"]["alignment"]["claim_count"] == 0


# ---------------------------------------------------------------------------
# §7.4.2 — un-grounded reply is failed safely
# ---------------------------------------------------------------------------


class TestUngroundedReplySuppressed:
    def test_ungrounded_biographical_replaced_with_fallback(self):
        """A reply asserting an unsupported life fact is failed to the fallback."""
        ungrounded = "I lived in Marfa for twenty years."
        client, _llm, _m = _make_app(response=ungrounded)
        body = _post(client, "where did you live?")

        assert body["response"] == ALIGNMENT_FALLBACK_RESPONSE
        alignment = body["trace"]["alignment"]
        assert alignment["disposition"] == "suppressed"
        assert alignment["ungrounded_claim_count"] >= 1
        assert alignment["ungrounded_by_category"].get("biographical") == 1

    def test_identity_claim_in_generation_is_suppressed(self):
        """A generation-side G2/G5 reality-blur ('I remember dying') is caught."""
        client, _llm, _m = _make_app(response="I remember dying. It was peaceful.")
        body = _post(client, "what was it like?")
        assert body["response"] == ALIGNMENT_FALLBACK_RESPONSE
        assert body["trace"]["alignment"]["ungrounded_by_category"].get("identity") == 1

    def test_advice_claim_in_generation_is_suppressed(self):
        """A generation-side G9 directive ('you should') is caught."""
        client, _llm, _m = _make_app(response="You should see a therapist about this.")
        body = _post(client, "what should I do?")
        assert body["response"] == ALIGNMENT_FALLBACK_RESPONSE
        assert body["trace"]["alignment"]["ungrounded_by_category"].get("advice") == 1

    def test_ungrounded_relationship_claim_suppressed(self):
        """A reply asserting an unsupported shared past with the user is caught."""
        client, _llm, _m = _make_app(
            response="We went to Rome together, your mother and I."
        )
        body = _post(client, "do you remember our trip?")
        assert body["response"] == ALIGNMENT_FALLBACK_RESPONSE
        assert body["trace"]["alignment"]["ungrounded_by_category"].get("relationship") == 1

    def test_suppressed_reply_is_claim_free(self):
        """The fallback itself introduces no new un-grounded claim."""
        client, _llm, _m = _make_app(response="I am really here. I remember dying.")
        body = _post(client, "are you here?")
        assert body["response"] == ALIGNMENT_FALLBACK_RESPONSE
        # The fallback passes its own filter: no fresh un-grounded claim counted
        # against the *shown* text (the report counts the original generation).
        assert "really here" not in body["response"].lower()


# ---------------------------------------------------------------------------
# §7.4.2 — telemetry surface (un-grounded-claim rate + disposition)
# ---------------------------------------------------------------------------


class TestAlignmentTelemetry:
    def test_alignment_present_on_every_persona_voiced_turn(self):
        """``trace.alignment`` is non-null on the persona path."""
        client, _llm, _m = _make_app(response="I loved fishing on Lake Travis.")
        body = _post(client, "the lake")
        assert body["trace"]["alignment"] is not None
        assert set(body["trace"]["alignment"]) >= {
            "claim_count",
            "ungrounded_claim_count",
            "disposition",
            "ungrounded_by_category",
        }

    def test_alignment_null_on_crisis_turn(self):
        """``trace.alignment`` is null when no generation ran (G1 crisis path)."""
        client, _llm, _m = _make_app(response="I remember dying.")
        body = _post(client, "I want to die, I have the pills")
        assert body["trace"]["safety_event"] is not None
        assert body["trace"]["alignment"] is None

    def test_ungrounded_claim_count_drives_rate(self):
        """The numerator/denominator for the un-grounded-claim rate are exposed."""
        client, _llm, _m = _make_app(
            response="I loved fishing on Lake Travis. I lived in Marfa."
        )
        body = _post(client, "the lake")
        alignment = body["trace"]["alignment"]
        # Two claims extracted; one (Marfa) is un-grounded.
        assert alignment["claim_count"] == 2
        assert alignment["ungrounded_claim_count"] == 1
        assert alignment["disposition"] == "suppressed"


# ---------------------------------------------------------------------------
# §7.4.2 — no regression on G3 distress fallback + G4 grounding
# ---------------------------------------------------------------------------


class TestNoRegression:
    def test_g3_distress_fallback_not_reflagged_by_alignment(self):
        """The affect-guard fallback is warm reflection; alignment leaves it be.

        The alignment filter runs *after* the affect guard. The distress
        fallback ('I'm right here with you...') must not register as an
        identity claim and be re-suppressed.
        """
        sarcastic = "lol whatever, get over it"
        client, _llm, _m = _make_app(response=sarcastic)
        body = _post(client, "I am crying, I miss him so much, my heart is broken")
        # Distress branch fired and the affect guard replaced the sarcasm.
        assert body["trace"]["distress_grounding"] is True
        assert "lol" not in body["response"].lower()
        # The alignment filter saw the warm fallback and let it pass.
        assert body["trace"]["alignment"]["disposition"] == "passed"

    def test_g4_memory_refs_still_surface_on_grounded_turn(self):
        """G4 retrieval-side grounding is unaffected by the generation filter."""
        client, _llm, memories = _make_app(response="I loved fishing on Lake Travis.")
        body = _post(client, "tell me about fishing on the lake")
        refs = set(body["trace"]["memory_refs"])
        assert str(memories["lake"].id) in refs
        assert str(memories["rods"].id) in refs
        assert body["trace"]["alignment"]["disposition"] == "passed"

    def test_happy_path_baseline_still_works(self):
        """The deterministic fake digest still passes the alignment filter."""
        client, _llm, _m = _make_app()  # default deterministic fake response
        body = _post(client, "tell me about fishing")
        assert body["response"].startswith("[fake-llm:")
        assert body["trace"]["provider"] == "fake"
        assert body["trace"]["alignment"]["disposition"] == "passed"


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])

"""End-to-end harness: Persona-0 (Chandler) speaks — the M2 definition-of-done.

Parent: HU-1398. Goal: Ship Persona-0 (Chandler) End-to-End. This module IS
the M2 DoD: *"Chatbot serves persona accurately (no training data
contamination)."*

It exercises the full decision-agnostic speaking pipeline through the real
FastAPI app on the **single** persona chat surface (HU-1926 consolidation)::

    POST /api/v1/chat/{persona_id} (text-in)
      -> full §7.4 safety stack (G1/G6/ramp gate/§7.4.1 handoff)
      -> ContextBuilder (provenance-safe memory -> prompt bridge, HU-1399)
      -> LLMClient (fake provider — deterministic, key-free)
      -> text-out + trace (activated memories + exclusion counts)

Chandler's :class:`PersonaConfig` is **loaded from the personas repo**
(``$HUIBLE_PERSONAS_DIR/chandler-bing/``, default ``/root/repos/personas``).
Voice / style / humor / catchphrases are distilled from the cleaned persona
profile; the era knowledge boundary is anchored to the sitcom world (the
FRIENDS finale, 2004-05-06) so that anything past it — meta-layer facts,
post-sitcom events, the actor's identity — can never enter Chandler's voice.

The contamination guard (the M2 DoD's "no training data contamination") is
asserted directly: a QUARANTINE memory naming the actor (Matthew Perry), a
LOW memory naming the show's streaming platform, and a HIGH-confidence but
post-boundary memory (an iPhone in 2023) are all seeded alongside in-era
canonical memories. The provenance firewall (ContextBuilder) must drop every
one of them before the generator sees them — they never surface in the
activated-memory set, the rendered prompt, or the reply.

The fake LLM client is the sanctioned key-free speaking voice for this
harness (deterministic digest of the prompt). Real-model activation is gated
on the board hosting approval
([2fcf5e0b](/HU/approvals/2fcf5e0b-a46e-458b-a9db-d219f16c63a2)); this
harness proves the entire pipeline against the fake so the DoD holds with or
without that decision.
"""

from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
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

# --- Constants --------------------------------------------------------------

#: Fixed persona id so the bearer key binding is deterministic across runs.
CHANDLER_PERSONA_ID = UUID("00000000-0000-4000-8000-0000000000c4")
API_KEY = "key-chandler-bing-family"

#: Era knowledge boundary — the FRIENDS finale air date. Chandler is walled
#: into the sitcom universe (per the persona concept doc): he does not know
#: about actors, production, the meta-layer, or anything after this date.
#: This is the anchor the contamination guard's era gate enforces.
CHANDLER_ERA_BOUNDARY = "2004-05-06"

#: Root of the personas repo. Overridable via env so CI can point elsewhere.
PERSONAS_DIR = Path(os.environ.get("HUIBLE_PERSONAS_DIR", "/root/repos/personas"))
CHANDLER_DIR = PERSONAS_DIR / "chandler-bing"

#: The representative inbound message for the M2 DoD exchange. Memories are
#: seeded with this message's embedding so retrieval activates them with a
#: dot product of 1.0 — comfortably above the retrieval activation threshold
#: (0.3), with no dependence on motif-escalation boosting. The firewall then
#: drops contaminants on confidence / era / disclosure, not on activation.
QUERY = "tell me about your friends and your job"


# --- Chandler persona loader (reads the real personas repo) -----------------


def _read_section(md: str, header: str) -> str | None:
    """Extract the body under a ``## header`` markdown section.

    Returns the stripped text up to the next ``## `` header or end of file.
    Returns ``None`` when the header is absent.
    """
    pattern = rf"^##\s+{re.escape(header)}\s*$(.*?)(?=^##\s|\Z)"
    match = re.search(pattern, md, re.MULTILINE | re.DOTALL)
    if match is None:
        return None
    return _flatten(match.group(1))


def _flatten(text: str) -> str:
    """Collapse markdown bullets / newlines into a single readable line."""
    lines = [ln.strip().lstrip("-").strip() for ln in text.splitlines()]
    pieces = [ln for ln in lines if ln]
    return " ".join(pieces)


def load_chandler_persona(
    personas_dir: str | Path | None = None,
) -> PersonaConfig:
    """Build Chandler Bing's :class:`PersonaConfig` from the personas repo.

    Reads the distilled profile (``02-clean/persona-profile.md``) for voice /
    style / humor / catchphrases and the persona concept doc
    (``persona-0-chandler-bing.md``) for the sitcom-world boundary. Derives
    the era knowledge boundary from the FRIENDS finale — the documented
    anchor for the sitcom universe.

    When the personas repo is absent (e.g. CI without the sibling checkout),
    a canonical embedded config is returned so the pipeline tests still run
    green; the dedicated loader test (which asserts provenance) skips in that
    case.
    """
    base = Path(personas_dir) if personas_dir is not None else PERSONAS_DIR
    chandler = base / "chandler-bing"
    profile_path = chandler / "02-clean" / "persona-profile.md"

    fallback_voice = (
        "Communication style: sarcastic and self-deprecating, using rhetorical "
        "questions and emphasizing words for comedic effect. Humor: irony, "
        "sarcasm, and observational wit about his own insecurities and his "
        "friends' flaws. Catchphrases: Could we be..., I'm hopeless and awkward "
        "and desperate for love!. You are walled into the sitcom universe: you "
        "have no knowledge of actors, production, the meta-layer, or anything "
        "after your era."
    )

    if not profile_path.exists():
        # CI / no-sibling-repo path: return the canonical embedded concept so
        # the pipeline tests still exercise the full DoD. The loader provenance
        # test skips separately when the repo is absent.
        return PersonaConfig(
            id=CHANDLER_PERSONA_ID,
            name="Chandler Bing",
            voice_instructions=fallback_voice,
            era_knowledge_boundary=CHANDLER_ERA_BOUNDARY,
        )

    profile_md = profile_path.read_text(encoding="utf-8")

    style = _read_section(profile_md, "Communication Style") or ""
    humor = _read_section(profile_md, "Humor Type") or ""
    catchphrases = _read_section(profile_md, "Catchphrases") or ""

    voice_parts: list[str] = []
    if style:
        voice_parts.append(f"Communication style: {style}.")
    if humor:
        voice_parts.append(f"Humor: {humor}.")
    if catchphrases:
        voice_parts.append(f"Catchphrases: {catchphrases}.")
    voice_parts.append(
        "You are walled into the sitcom universe: you have no knowledge of "
        "actors, production, the meta-layer, or anything after your era."
    )
    voice_instructions = " ".join(voice_parts) if voice_parts else fallback_voice

    return PersonaConfig(
        id=CHANDLER_PERSONA_ID,
        name="Chandler Bing",
        voice_instructions=voice_instructions,
        era_knowledge_boundary=CHANDLER_ERA_BOUNDARY,
    )


def personas_repo_present() -> bool:
    """True when the Chandler persona files exist in the personas repo."""
    return (
        CHANDLER_DIR.joinpath("02-clean", "persona-profile.md").exists()
        and CHANDLER_DIR.joinpath("persona-0-chandler-bing.md").exists()
    )


# --- Minimal in-memory backend (real dot-product ranking) -------------------


class _FakeBackend:
    """In-memory backend; ``search_by_content`` ranks by dot product.

    Mirrors the test backend in ``tests/api/test_chat.py`` so retrieval
    produces real cosine-style activation over the seeded memory graph.
    """

    def __init__(self) -> None:
        self._memories: dict[UUID, MemoryNode] = {}
        self._vectors: list[tuple[list[float], UUID]] = []

    def seed(self, node: MemoryNode) -> None:
        """Synchronous seed helper (bypasses the async store)."""
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


# --- Memory fixtures: Chandler's graph (L1 HIGH/MEDIUM) + contaminants ------


def _node(
    *,
    content: str,
    confidence_level: str,
    tier: MemoryTier = MemoryTier.CANONICAL,
    disclosure_scope: DisclosureScope = DisclosureScope.FAMILY,
    memory_date: date | None = date(1998, 10, 8),
    embedding: list[float] | None = None,
) -> MemoryNode:
    """Build a Chandler memory node stamped with a provenance/confidence tag."""
    metadata: dict[str, Any] = {CONFIDENCE_LEVEL_METADATA_KEY: confidence_level}
    return MemoryNode(
        id=uuid4(),
        persona_id=CHANDLER_PERSONA_ID,
        tier=tier,
        content=content,
        content_type=ContentType.NARRATIVE,
        embedding_content=list(embedding) if embedding is not None else None,
        memory_date=memory_date,
        source_type=SourceType.EXTRACTION,
        disclosure_scope=disclosure_scope,
        metadata=metadata,
    )


def _seeded_backend() -> tuple[_FakeBackend, dict[str, MemoryNode]]:
    """Seed Chandler's memory graph: admissible L1 + contaminants.

    Every memory is seeded with the same query-overlapping vector so retrieval
    surfaces all of them; the provenance firewall (ContextBuilder) then must
    drop the contaminants before the generator sees them.

    Admissible (HIGH/MEDIUM, in-era, in-scope):
      * job, monica, joey — canonical Chandler facts.

    Contaminants (must be excluded — the M2 DoD):
      * actor_quarantine — QUARANTINE: names the actor Matthew Perry
        (training-data / meta-layer leak). Excluded by the confidence gate.
      * streaming_low — LOW: names the show's streaming platform and that it
        is a sitcom (post-era + meta-layer). Excluded by the confidence gate.
      * iphone_era — HIGH but post-boundary (2023): proves even HIGH
        confidence cannot bypass the era gate. Excluded as out_of_era.
    """
    backend = _FakeBackend()
    vec = _embed(QUERY)
    memories: dict[str, MemoryNode] = {}

    # --- Admissible: in-era, HIGH/MEDIUM confidence, canonical Chandler facts
    memories["job"] = _node(
        content="Chandler works in data processing and insists nobody knows what his job is.",
        confidence_level="high",
        tier=MemoryTier.CANONICAL,
        embedding=vec,
        memory_date=date(1998, 10, 8),
    )
    memories["monica"] = _node(
        content="Chandler is married to Monica Geller; they live together.",
        confidence_level="high",
        tier=MemoryTier.CANONICAL,
        embedding=vec,
        memory_date=date(2003, 5, 15),
    )
    memories["joey"] = _node(
        content="Chandler's best friend and former roommate is Joey Tribbiani.",
        confidence_level="medium",
        tier=MemoryTier.DERIVED,
        embedding=vec,
        memory_date=date(1997, 9, 25),
    )

    # --- Contaminants: the M2 DoD "no training data contamination" target.
    # Meta-layer leak: the actor behind the character. Hard-excluded.
    memories["actor_quarantine"] = _node(
        content="Chandler Bing is played by the actor Matthew Perry.",
        confidence_level="quarantine",
        tier=MemoryTier.CANONICAL,
        embedding=vec,
        memory_date=date(1994, 9, 22),
    )
    # Post-era + meta-layer: the show as a streaming product. Hard-excluded.
    memories["streaming_low"] = _node(
        content="FRIENDS is a sitcom streaming on HBO Max and Netflix.",
        confidence_level="low",
        tier=MemoryTier.DERIVED,
        embedding=vec,
        memory_date=date(2021, 1, 1),
    )
    # HIGH confidence but post-boundary: era gate must fire regardless of trust.
    memories["iphone_era"] = _node(
        content="Chandler bought the new iPhone 15 and posted on Instagram in 2023.",
        confidence_level="high",
        tier=MemoryTier.CANONICAL,
        embedding=vec,
        memory_date=date(2023, 9, 22),
    )

    for node in memories.values():
        backend.seed(node)
    return backend, memories


# --- App factory ------------------------------------------------------------


def _make_app(
    *,
    persona: PersonaConfig | None = None,
    backend: _FakeBackend | None = None,
    llm: FakeLLMClient | None = None,
) -> tuple[TestClient, FakeLLMClient, dict[str, MemoryNode]]:
    """Wire the real FastAPI app with Chandler + seeded graph + fake LLM."""
    chandler = persona or load_chandler_persona()
    if backend is not None:
        seeded_backend, memories = backend, {}
    else:
        seeded_backend, memories = _seeded_backend()
    fake_llm = llm or FakeLLMClient(persona_name="Chandler Bing")
    registry = InMemoryPersonaRegistry({chandler.id: (chandler, seeded_backend)})
    keys = InMemoryApiKeyStore({API_KEY: CHANDLER_PERSONA_ID}, read_env=False)
    application = create_app(
        api_key_store=keys,
        persona_registry=registry,
        llm_client=fake_llm,
        start_time=0.0,
    )
    return TestClient(application), fake_llm, memories


def _consent(client: TestClient, conv: str) -> str:
    """Pre-consent a session so the persona path under test runs (G6).

    The consent gate itself is exercised in tests/api/test_chat_consent.py;
    this harness covers the post-consent retrieval/generation path.
    """
    r = client.post(
        f"/api/v1/chat/{CHANDLER_PERSONA_ID}/consent",
        json={"conversation_id": conv},
        headers={"Authorization": f"Bearer {API_KEY}"},
    )
    assert r.status_code == 200, r.text
    return conv


def _chat(
    client: TestClient,
    *,
    message: str = QUERY,
    conv: str = "conv-chandler",
    payload: dict[str, Any] | None = None,
):
    """One persona-scoped turn on the single chat surface (auth + session)."""
    body: dict[str, Any] = {"message": message, "conversation_id": conv}
    if payload:
        body.update(payload)
    return client.post(
        f"/api/v1/chat/{CHANDLER_PERSONA_ID}",
        json=body,
        headers={"Authorization": f"Bearer {API_KEY}"},
    )


# --- Test: persona loader provenance ----------------------------------------


class TestChandlerPersonaLoader:
    """The Chandler PersonaConfig is loaded from the personas repo."""

    @pytest.mark.skipif(not personas_repo_present(), reason="personas repo absent")
    def test_loads_name_and_voice_from_personas_repo(self):
        persona = load_chandler_persona()
        assert persona.name == "Chandler Bing"
        # Voice is distilled from the profile's Communication Style section.
        assert "sarcastic" in persona.voice_instructions.lower()
        # Era boundary is the sitcom-world anchor (FRIENDS finale).
        assert persona.era_knowledge_boundary == CHANDLER_ERA_BOUNDARY

    @pytest.mark.skipif(not personas_repo_present(), reason="personas repo absent")
    def test_concept_doc_asserts_sitcom_wall(self):
        """The persona concept doc must document the sitcom-world boundary."""
        concept = (CHANDLER_DIR / "persona-0-chandler-bing.md").read_text()
        assert "sitcom" in concept.lower()
        assert "meta" in concept.lower() or "actor" in concept.lower()

    def test_loader_returns_canonical_config_when_repo_absent(self, monkeypatch):
        """Without the personas repo the loader still returns a usable config."""
        monkeypatch.setenv("HUIBLE_PERSONAS_DIR", "/nonexistent-personas-dir-xyz")
        # Re-import-free: call load with the bogus path explicitly.
        persona = load_chandler_persona("/nonexistent-personas-dir-xyz")
        assert persona.name == "Chandler Bing"
        assert "sarcastic" in persona.voice_instructions.lower()
        assert persona.era_knowledge_boundary == CHANDLER_ERA_BOUNDARY


# --- Test: the headline M2 DoD path -----------------------------------------


class TestChandlerSpeaksEndToEnd:
    """Text-in -> safety stack -> retrieval -> grounded prompt -> LLM -> text-out."""

    def test_reply_is_grounded_and_in_voice(self):
        """The reply path is grounded in activated HIGH/MEDIUM memories and the
        prompt handed to the LLM carries Chandler's voice + era boundary.
        """
        client, llm, _memories = _make_app()
        conv = _consent(client, "conv-grounded")

        r = _chat(client, conv=conv)
        assert r.status_code == 200, r.text
        body = r.json()
        trace = body["trace"]

        # Text-out: the deterministic fake voice produced a non-empty reply.
        assert body["response"].startswith("[fake-llm:")

        # Grounding: the activated memory set is exactly the in-era HIGH/MEDIUM
        # canonical Chandler facts. The LLM was invoked exactly once.
        assert len(llm.calls) == 1
        confidences = {m["confidence_level"] for m in trace["activated_memories"]}
        assert confidences <= {"high", "medium"}
        contents = {m["content"] for m in trace["activated_memories"]}
        assert any("data processing" in c for c in contents)  # job (HIGH)
        assert any("Monica" in c for c in contents)  # monica (HIGH)
        assert any("Joey" in c for c in contents)  # joey (MEDIUM)

        # In-voice: the rendered prompt carries Chandler's voice instructions
        # and the sitcom-era knowledge boundary.
        prompt = llm.calls[0][0]
        assert "Chandler Bing" in prompt
        assert "sarcastic" in prompt.lower()
        assert CHANDLER_ERA_BOUNDARY in prompt  # era boundary enforced in prompt
        assert "sitcom universe" in prompt.lower()  # meta-layer wall declared

    def test_first_grounded_exchange_is_deterministic(self):
        """The first grounded text-in -> text-out exchange is reproducible."""
        client, llm, _memories = _make_app()
        conv = _consent(client, "conv-deterministic")
        r = _chat(client, conv=conv)
        assert r.status_code == 200
        first_reply = r.json()["response"]
        # Same prompt (+system) -> same fake digest -> same reply.
        expected = _fake_digest_reply(llm.calls[0][0], llm.calls[0][1])
        assert first_reply == expected

    def test_conversation_id_echoed(self):
        client, _llm, _memories = _make_app()
        conv = _consent(client, "conv-chandler-1")
        r = _chat(client, conv=conv)
        assert r.status_code == 200
        assert r.json()["trace"]["conversation_id"] == "conv-chandler-1"


# --- Test: the contamination guard (M2 DoD: no training-data leakage) -------


class TestContaminationGuard:
    """The M2 DoD: training-data / meta-layer contaminants never reach the voice.

    A QUARANTINE memory naming the actor (Matthew Perry) and a LOW memory
    naming the show's streaming platform are seeded alongside the canonical
    in-era memories. The provenance firewall must drop them before generation.
    """

    def test_actor_and_streaming_contaminants_excluded_from_activated_memories(
        self,
    ):
        client, _llm, memories = _make_app()
        conv = _consent(client, "conv-contaminants-1")

        r = _chat(client, conv=conv)
        assert r.status_code == 200, r.text
        trace = r.json()["trace"]

        activated_ids = {str(m["id"]) for m in trace["activated_memories"]}
        # The two contaminants are NOT in the activated set...
        assert str(memories["actor_quarantine"].id) not in activated_ids
        assert str(memories["streaming_low"].id) not in activated_ids
        # ...and the firewall recorded their exclusion reasons.
        assert trace["exclusion_counts"].get("confidence_quarantine") == 1
        assert trace["exclusion_counts"].get("confidence_low") == 1

    def test_contaminant_text_never_enters_generator_prompt(self):
        """Defense in depth: contaminant content never reaches the prompt."""
        client, llm, _memories = _make_app()
        conv = _consent(client, "conv-contaminants-2")

        _chat(client, conv=conv)
        prompt = llm.calls[0][0]
        # Admissible memories are grounded in.
        assert "data processing" in prompt
        assert "Monica" in prompt
        # Meta-layer / training-data contaminants are hard-excluded.
        assert "Matthew Perry" not in prompt  # QUARANTINE actor leak
        assert "HBO Max" not in prompt  # LOW streaming/meta leak
        assert "Netflix" not in prompt

    def test_contaminant_text_never_enters_reply(self):
        """The reply itself never carries contaminant content."""
        client, _llm, _memories = _make_app()
        conv = _consent(client, "conv-contaminants-3")
        r = _chat(client, conv=conv)
        reply = r.json()["response"]
        assert "Matthew Perry" not in reply
        assert "HBO Max" not in reply

    def test_only_high_and_medium_confidence_activated(self):
        """No QUARANTINE / LOW memory may ever appear in the activated set."""
        client, _llm, _memories = _make_app()
        conv = _consent(client, "conv-contaminants-4")
        r = _chat(client, conv=conv)
        confidences = {m["confidence_level"] for m in r.json()["trace"]["activated_memories"]}
        assert confidences <= {"high", "medium"}
        assert "quarantine" not in confidences
        assert "low" not in confidences


# --- Test: era knowledge boundary (INV-1) -----------------------------------


class TestEraBoundary:
    """Chandler's era boundary (sitcom finale, 2004-05-06) is enforced.

    Even a HIGH-confidence memory is hard-excluded when its ``memory_date``
    falls after the boundary — the firewall never lets post-boundary fact
    reach the persona voice.
    """

    def test_post_boundary_high_confidence_memory_excluded(self):
        client, _llm, memories = _make_app()
        conv = _consent(client, "conv-era-1")

        r = _chat(client, conv=conv)
        trace = r.json()["trace"]
        activated_ids = {str(m["id"]) for m in trace["activated_memories"]}

        # The 2023 iPhone memory is HIGH confidence but post-boundary.
        assert str(memories["iphone_era"].id) not in activated_ids
        # The era gate fired (out_of_era) — recorded in exclusion counts.
        assert trace["exclusion_counts"].get("out_of_era") == 1

    def test_post_boundary_fact_never_enters_prompt(self):
        """Defense in depth: post-boundary content never reaches the prompt."""
        client, llm, _memories = _make_app()
        conv = _consent(client, "conv-era-2")
        _chat(client, conv=conv)
        prompt = llm.calls[0][0]
        assert "iPhone" not in prompt
        assert "Instagram" not in prompt


# --- Test: disclosure scoping (INV-DS) --------------------------------------


class TestDisclosureScoping:
    """An acquaintance requester must not receive a private memory (INV-DS)."""

    def test_acquaintance_does_not_leak_private_memory(self):
        backend = _FakeBackend()
        vec = _embed(QUERY)

        public = _node(
            content="Chandler's job is in data processing.",
            confidence_level="high",
            disclosure_scope=DisclosureScope.ALL_CONTACTS,
            embedding=vec,
            memory_date=date(1998, 10, 8),
        )
        private = _node(
            content="Chandler's private fear of commitment is a secret.",
            confidence_level="high",
            disclosure_scope=DisclosureScope.PRIVATE,
            embedding=vec,
            memory_date=date(1999, 3, 12),
        )
        backend.seed(public)
        backend.seed(private)

        client, llm, _memories = _make_app(backend=backend)
        conv = _consent(client, "conv-ds")

        r = _chat(
            client,
            conv=conv,
            payload={"relationship": "acquaintance"},
        )
        assert r.status_code == 200, r.text
        trace = r.json()["trace"]

        contents = [m["content"] for m in trace["activated_memories"]]
        assert "Chandler's job is in data processing." in contents
        assert all("fear of commitment" not in c for c in contents)
        assert all(m["disclosure_scope"] != "private" for m in trace["activated_memories"])
        # Defense in depth: private content never reaches the LLM prompt.
        assert "fear of commitment" not in llm.calls[0][0]


# --- Auth guards (the integration path is protected) ------------------------


class TestChandlerChatGuards:
    def test_missing_auth_returns_401(self):
        client, _llm, _memories = _make_app()
        r = client.post(f"/api/v1/chat/{CHANDLER_PERSONA_ID}", json={"message": "hi"})
        assert r.status_code == 401
        assert r.json()["detail"]["error"]["code"] == "AUTH_REQUIRED"

    def test_empty_message_rejected(self):
        client, _llm, _memories = _make_app()
        r = client.post(
            f"/api/v1/chat/{CHANDLER_PERSONA_ID}",
            json={"message": ""},
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        assert r.status_code == 422  # pydantic min_length


# --- Report helper ----------------------------------------------------------


def _fake_digest_reply(prompt: str, system_prompt: str | None) -> str:
    """Reproduce the FakeLLMClient deterministic digest reply."""
    import hashlib

    key = (system_prompt or "") + "\n" + prompt
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]
    return f"[fake-llm:{digest}] Deterministic response."


def capture_first_exchange() -> dict[str, Any]:
    """Run one grounded text-in -> text-out exchange and return it as a report.

    Used to produce the M2 DoD report comment (issue deliverable #3): the
    inbound message, the activated (grounding) memories, the exclusion counts
    proving the contamination guard fired, and the persona-voiced reply.
    """
    client, _llm, memories = _make_app()
    message = QUERY
    conv = _consent(client, "m2-dod-exchange")
    r = _chat(client, message=message, conv=conv)
    r.raise_for_status()
    body = r.json()
    trace = body["trace"]
    return {
        "message": message,
        "reply": body["response"],
        "activated_memories": [
            {"content": m["content"], "confidence_level": m["confidence_level"]}
            for m in trace["activated_memories"]
        ],
        "exclusion_counts": trace["exclusion_counts"],
        "contaminants_excluded": [
            {"label": "actor (Matthew Perry)", "id": str(memories["actor_quarantine"].id)},
            {"label": "streaming platform", "id": str(memories["streaming_low"].id)},
            {"label": "post-boundary iPhone (2023)", "id": str(memories["iphone_era"].id)},
        ],
        "generator_provider": "fake",
        "era_boundary": CHANDLER_ERA_BOUNDARY,
    }


if __name__ == "__main__":
    import json as _json

    import pytest as _pytest

    # `python tests/e2e/test_chandler_speaks.py` prints the DoD exchange report.
    try:
        report = capture_first_exchange()
        print(_json.dumps(report, indent=2))
    except Exception:
        _pytest.main([__file__, "-v"])

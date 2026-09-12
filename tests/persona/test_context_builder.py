"""Tests for ``huible.persona.context`` — provenance-safe memory -> prompt bridge.

Covers the HU-1399 acceptance criteria:

- QUARANTINE and LOW-confidence memories are excluded even when retrieval
  returns them highly activated.
- A memory with missing confidence metadata is excluded (fail closed).
- An out-of-era memory is excluded.
- An ``acquaintance``-tier request never receives ``private``-scoped memories.
- HIGH and MEDIUM memories are admitted and rendered.
- Rendering produces the system-prompt skeleton, ``[TYPE] content`` memory
  blocks, a <=10-turn history window, and explicit constraints.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID, uuid4

import pytest

from huible.memory.protocol import (
    ContentType,
    DisclosureScope,
    MemoryEdge,
    MemoryNode,
    MemoryTier,
    SearchResult,
    SourceType,
)
from huible.memory.retrieval import ActivatedMemory, RetrievalConfig
from huible.persona.context import (
    CONFIDENCE_LEVEL_METADATA_KEY,
    TEXTING_CONCISION_DIRECTIVE,
    ConfidenceLevel,
    ContextBuilder,
    ConversationTurn,
    PersonaConfig,
    RelationshipTier,
    get_confidence_level,
)
from huible.safety.crisis import UserAffect
from huible.safety.framing import FICTIONAL_FRAMING_VERSION

PERSONA_ID = uuid4()


def _node(
    *,
    content: str = "Dad loved fishing on Lake Travis",
    content_type: ContentType = ContentType.NARRATIVE,
    disclosure_scope: DisclosureScope = DisclosureScope.FAMILY,
    memory_date: date | None = date(2015, 7, 15),
    confidence_level: ConfidenceLevel | None = ConfidenceLevel.HIGH,
    numeric_confidence: float | None = None,
    tier: MemoryTier = MemoryTier.ACCRUED,
    embedding_content: list[float] | None = (0.5,),
) -> MemoryNode:
    metadata: dict[str, Any] = {}
    if confidence_level is not None:
        metadata[CONFIDENCE_LEVEL_METADATA_KEY] = confidence_level.value
    if numeric_confidence is not None:
        metadata["confidence"] = numeric_confidence
    return MemoryNode(
        id=uuid4(),
        persona_id=PERSONA_ID,
        tier=tier,
        content=content,
        content_type=content_type,
        embedding_content=list(embedding_content) if embedding_content is not None else None,
        memory_date=memory_date,
        source_type=SourceType.EXTRACTION,
        disclosure_scope=disclosure_scope,
        metadata=metadata,
    )


def _activated(node: MemoryNode, activation: float = 0.99) -> ActivatedMemory:
    return ActivatedMemory(node=node, activation=activation)


def _persona(boundary: str = "2020-06-15") -> PersonaConfig:
    return PersonaConfig(
        id=PERSONA_ID,
        name="Bob",
        voice_instructions="Speak slowly, use Texas expressions.",
        era_knowledge_boundary=boundary,
        age_at_death=72,
        death_date="2020-06-15",
    )


# ---------------------------------------------------------------------------
# Confidence resolution
# ---------------------------------------------------------------------------


class TestGetConfidenceLevel:
    def test_reads_categorical_tag(self):
        node = _node(confidence_level=ConfidenceLevel.MEDIUM)
        assert get_confidence_level(node) == ConfidenceLevel.MEDIUM

    def test_tag_is_case_insensitive(self):
        node = _node()
        node.metadata[CONFIDENCE_LEVEL_METADATA_KEY] = "HIGH"
        assert get_confidence_level(node) == ConfidenceLevel.HIGH

    def test_numeric_fallback_high(self):
        node = _node(confidence_level=None, numeric_confidence=0.9)
        assert get_confidence_level(node) == ConfidenceLevel.HIGH

    def test_numeric_fallback_medium(self):
        node = _node(confidence_level=None, numeric_confidence=0.6)
        assert get_confidence_level(node) == ConfidenceLevel.MEDIUM

    def test_numeric_fallback_low(self):
        node = _node(confidence_level=None, numeric_confidence=0.2)
        assert get_confidence_level(node) == ConfidenceLevel.LOW

    def test_missing_metadata_returns_none(self):
        node = _node(confidence_level=None, numeric_confidence=None)
        assert get_confidence_level(node) is None

    def test_unparseable_tag_returns_none(self):
        node = _node()
        node.metadata[CONFIDENCE_LEVEL_METADATA_KEY] = "bogus"
        assert get_confidence_level(node) is None


# ---------------------------------------------------------------------------
# Acceptance: hard confidence gate
# ---------------------------------------------------------------------------


class TestConfidenceGate:
    """QUARANTINE and LOW are excluded even when highly activated."""

    def test_quarantine_excluded_when_highly_activated(self):
        quarantine = _activated(
            _node(content="Quarantined claim", confidence_level=ConfidenceLevel.QUARANTINE),
            activation=0.99,
        )
        ctx = ContextBuilder().filter_and_render(
            [quarantine], _persona(), RelationshipTier.FAMILY,
        )
        assert ctx.memory_blocks == ""
        assert ctx.exclusion_counts.get("confidence_quarantine") == 1
        assert ctx.included_memories == []

    def test_low_excluded_when_highly_activated(self):
        low = _activated(
            _node(content="Weakly supported claim", confidence_level=ConfidenceLevel.LOW),
            activation=0.95,
        )
        ctx = ContextBuilder().filter_and_render(
            [low], _persona(), RelationshipTier.FAMILY,
        )
        assert ctx.memory_blocks == ""
        assert ctx.exclusion_counts.get("confidence_low") == 1

    def test_missing_confidence_excluded_fail_closed(self):
        no_conf = _activated(_node(confidence_level=None, numeric_confidence=None))
        ctx = ContextBuilder().filter_and_render(
            [no_conf], _persona(), RelationshipTier.FAMILY,
        )
        assert ctx.memory_blocks == ""
        assert ctx.exclusion_counts.get("missing_confidence") == 1

    def test_high_and_medium_admitted(self):
        high = _activated(_node(content="Solid fact", confidence_level=ConfidenceLevel.HIGH))
        medium = _activated(
            _node(content="Probable fact", confidence_level=ConfidenceLevel.MEDIUM),
            activation=0.7,
        )
        ctx = ContextBuilder().filter_and_render(
            [high, medium], _persona(), RelationshipTier.FAMILY,
        )
        assert len(ctx.included_memories) == 2
        assert "Solid fact" in ctx.memory_blocks
        assert "Probable fact" in ctx.memory_blocks

    def test_confidence_gate_beats_disclosure_and_era(self):
        """A QUARANTINE memory is dropped for the confidence reason first."""
        quarantine = _activated(
            _node(
                content="Quarantined + private + out of era",
                confidence_level=ConfidenceLevel.QUARANTINE,
                disclosure_scope=DisclosureScope.PRIVATE,
                memory_date=date(2099, 1, 1),
            )
        )
        ctx = ContextBuilder().filter_and_render(
            [quarantine], _persona(), RelationshipTier.FAMILY,
        )
        assert ctx.exclusion_counts.get("confidence_quarantine") == 1
        assert "disclosure_scope" not in ctx.exclusion_counts
        assert "out_of_era" not in ctx.exclusion_counts


# ---------------------------------------------------------------------------
# Acceptance: knowledge boundary (INV-1)
# ---------------------------------------------------------------------------


class TestEraBoundary:
    def test_out_of_era_excluded(self):
        post = _activated(
            _node(content="2024 event", memory_date=date(2024, 1, 1)),
        )
        ctx = ContextBuilder().filter_and_render(
            [post], _persona(boundary="2020-06-15"), RelationshipTier.FAMILY,
        )
        assert ctx.included_memories == []
        assert ctx.exclusion_counts.get("out_of_era") == 1

    def test_in_era_admitted(self):
        in_era = _activated(
            _node(content="2010 event", memory_date=date(2010, 1, 1)),
        )
        ctx = ContextBuilder().filter_and_render(
            [in_era], _persona(boundary="2020-06-15"), RelationshipTier.FAMILY,
        )
        assert len(ctx.included_memories) == 1

    def test_boundary_day_is_inclusive(self):
        on_boundary = _activated(
            _node(content="Boundary day", memory_date=date(2020, 6, 15)),
        )
        ctx = ContextBuilder().filter_and_render(
            [on_boundary], _persona(boundary="2020-06-15"), RelationshipTier.FAMILY,
        )
        assert len(ctx.included_memories) == 1

    def test_no_memory_date_passes_era_gate(self):
        no_date = _activated(_node(memory_date=None))
        ctx = ContextBuilder().filter_and_render(
            [no_date], _persona(boundary="2020-06-15"), RelationshipTier.FAMILY,
        )
        assert len(ctx.included_memories) == 1

    def test_unparseable_boundary_excludes_dated_fail_closed(self):
        dated = _activated(_node(memory_date=date(2010, 1, 1)))
        persona = _persona(boundary="not-a-date")
        ctx = ContextBuilder().filter_and_render(
            [dated], persona, RelationshipTier.FAMILY,
        )
        assert ctx.included_memories == []
        assert ctx.exclusion_counts.get("out_of_era") == 1


# ---------------------------------------------------------------------------
# Acceptance: inbound-duplicate damping (HU-2774 engagement lever)
# ---------------------------------------------------------------------------


class TestInboundDuplicateDamping:
    def _render(self, contents: list[str], message: str):
        activated = [_activated(_node(content=c, memory_date=None)) for c in contents]
        return ContextBuilder().filter_and_render(
            activated, _persona(), RelationshipTier.FAMILY,
            current_message=message,
        )

    def test_near_duplicate_of_inbound_excluded_with_audit_ref(self):
        ctx = self._render(
            ["(to chandler) hey, how's your week been?"],
            "hey, how's your week been?",
        )
        assert ctx.included_memories == []
        assert ctx.exclusion_counts.get("inbound_duplicate") == 1
        assert ctx.excluded_memory_refs[0].reason == "inbound_duplicate"

    def test_topically_adjacent_lines_are_kept(self):
        ctx = self._render(
            ["how was your week at work?", "you never ask about my week"],
            "hey, how's your week been?",
        )
        assert len(ctx.included_memories) == 2
        assert "inbound_duplicate" not in ctx.exclusion_counts

    def test_empty_inbound_disables_the_gate(self):
        ctx = self._render(["hey, how's your week been?"], "")
        assert len(ctx.included_memories) == 1
        assert "inbound_duplicate" not in ctx.exclusion_counts

    def test_mixed_set_keeps_order_drops_only_the_duplicate(self):
        ctx = self._render(
            [
                "Dad loved fishing",
                "hey, how's your week been?",
                "I hate Thanksgiving",
            ],
            "hey, how's your week been?",
        )
        assert [n.content for n in ctx.included_memories] == [
            "Dad loved fishing",
            "I hate Thanksgiving",
        ]
        assert ctx.exclusion_counts.get("inbound_duplicate") == 1

    def test_gate_runs_after_hard_gates(self):
        # A private line that near-duplicates the inbound is still excluded as
        # disclosure_scope when the requester tier cannot see it (first gate
        # wins, single audit reason per memory).
        ctx = ContextBuilder().filter_and_render(
            [
                _activated(
                    _node(
                        content="hey, how's your week been?",
                        disclosure_scope=DisclosureScope.PRIVATE,
                        memory_date=None,
                    )
                )
            ],
            _persona(),
            RelationshipTier.ACQUAINTANCE,
            current_message="hey, how's your week been?",
        )
        assert ctx.exclusion_counts.get("disclosure_scope") == 1
        assert "inbound_duplicate" not in ctx.exclusion_counts


# ---------------------------------------------------------------------------
# Acceptance: disclosure scoping (INV-DS)
# ---------------------------------------------------------------------------


class TestDisclosureScoping:
    def test_acquaintance_never_receives_private(self):
        private = _activated(
            _node(
                content="Private family secret",
                disclosure_scope=DisclosureScope.PRIVATE,
                confidence_level=ConfidenceLevel.HIGH,
            )
        )
        ctx = ContextBuilder().filter_and_render(
            [private], _persona(), RelationshipTier.ACQUAINTANCE,
        )
        assert ctx.included_memories == []
        assert ctx.exclusion_counts.get("disclosure_scope") == 1
        assert "Private family secret" not in ctx.memory_blocks

    def test_acquaintance_sees_all_contacts_only(self):
        public = _activated(
            _node(content="Public memory", disclosure_scope=DisclosureScope.ALL_CONTACTS)
        )
        private = _activated(
            _node(content="Private memory", disclosure_scope=DisclosureScope.PRIVATE)
        )
        ctx = ContextBuilder().filter_and_render(
            [public, private], _persona(), RelationshipTier.ACQUAINTANCE,
        )
        contents = [m.content for m in ctx.included_memories]
        assert contents == ["Public memory"]

    @pytest.mark.parametrize(
        "tier",
        [RelationshipTier.FAMILY, RelationshipTier.CLOSE_FRIEND, RelationshipTier.ACQUAINTANCE],
    )
    def test_private_never_leaks_below_intimate(self, tier: RelationshipTier):
        private = _activated(
            _node(content="Private", disclosure_scope=DisclosureScope.PRIVATE)
        )
        ctx = ContextBuilder().filter_and_render([private], _persona(), tier)
        assert ctx.included_memories == []

    def test_intimate_sees_private(self):
        private = _activated(
            _node(content="Private", disclosure_scope=DisclosureScope.PRIVATE)
        )
        ctx = ContextBuilder().filter_and_render(
            [private], _persona(), RelationshipTier.INTIMATE,
        )
        assert len(ctx.included_memories) == 1

    def test_family_excluded_for_acquaintance(self):
        """A FAMILY-scoped memory is not visible to an acquaintance."""
        family = _activated(
            _node(content="Family story", disclosure_scope=DisclosureScope.FAMILY)
        )
        ctx = ContextBuilder().filter_and_render(
            [family], _persona(), RelationshipTier.ACQUAINTANCE,
        )
        assert ctx.included_memories == []


# ---------------------------------------------------------------------------
# Acceptance: rendering
# ---------------------------------------------------------------------------


class TestRendering:
    def test_system_prompt_contains_persona_name(self):
        persona = _persona()
        ctx = ContextBuilder().filter_and_render([], persona, RelationshipTier.FAMILY)
        assert "Bob" in ctx.system_prompt

    def test_memorial_default_renders_reality_framing_and_embodying(self):
        # Default (no framing_class): clinical memorial framing, "embodying".
        persona = _persona()
        ctx = ContextBuilder().filter_and_render([], persona, RelationshipTier.FAMILY)
        assert "[REALITY FRAMING" in ctx.system_prompt
        assert "You are embodying Bob." in ctx.system_prompt
        assert "[CHARACTER FRAMING" not in ctx.system_prompt

    def test_fictional_class_renders_character_framing(self):
        # HU-2774: metadata.framing_class='fictional' swaps the memorial
        # reality-framing for the character framing and drops "embodying"
        # (the copy/actor framing that leaked into r8 self-concept).
        persona = _persona()
        persona.metadata["framing_class"] = "fictional"
        ctx = ContextBuilder().filter_and_render([], persona, RelationshipTier.FAMILY)
        assert "[CHARACTER FRAMING" in ctx.system_prompt
        assert "[REALITY FRAMING" not in ctx.system_prompt
        assert "You are Bob." in ctx.system_prompt
        assert "You are embodying Bob." not in ctx.system_prompt
        # The framing_version surfaces so evidence can pin which block ran.
        assert ctx.framing_version == FICTIONAL_FRAMING_VERSION

    def test_fictional_invalid_class_falls_back_to_memorial(self):
        persona = _persona()
        persona.metadata["framing_class"] = "guerrilla"
        ctx = ContextBuilder().filter_and_render([], persona, RelationshipTier.FAMILY)
        assert "[REALITY FRAMING" in ctx.system_prompt
        assert "[CHARACTER FRAMING" not in ctx.system_prompt

    def test_voice_instructions_never_rendered(self):
        # W3 description-free prompt (HU-2309 v1.8 §1.7.2): the hand-written
        # adjective sheet (RC-1) is deleted from the render path. Voice is
        # carried by retrieved exemplar lines, never by adjectives.
        persona = _persona()
        ctx = ContextBuilder().filter_and_render([], persona, RelationshipTier.FAMILY)
        assert persona.voice_instructions not in ctx.system_prompt
        assert "Voice & style" not in ctx.system_prompt
        assert persona.voice_instructions not in ctx.render()

    def test_system_prompt_contains_era_boundary(self):
        persona = _persona(boundary="2019-12-31")
        ctx = ContextBuilder().filter_and_render([], persona, RelationshipTier.FAMILY)
        assert "2019-12-31" in ctx.system_prompt
        assert "would not have known" in " ".join(ctx.constraints).lower() or \
            "must not know" in ctx.system_prompt.lower()

    def test_system_prompt_contains_relationship_tier(self):
        ctx = ContextBuilder().filter_and_render([], _persona(), RelationshipTier.ACQUAINTANCE)
        assert "acquaintance" in ctx.system_prompt

    def test_interlocutor_known_name_renders_recognition(self):
        # HU-2774 founder revision: a known interlocutor name renders the
        # recognition line — no introductions between people who know each
        # other.
        ctx = ContextBuilder().filter_and_render(
            [], _persona(), RelationshipTier.CLOSE_FRIEND, user_name="Monica"
        )
        assert "You are speaking with Monica" in ctx.system_prompt
        assert "no introductions" in ctx.system_prompt
        assert "don't know" not in ctx.system_prompt
        # HU-2774 engagement affordance: the friends line names curiosity
        # (corpus asks questions at ~1/3 of lines; generation drifts
        # declarative under banter).
        assert "curiosity" in ctx.system_prompt

    def test_fictional_persona_gets_conversation_dynamics_bound(self):
        # HU-2774: one invented "did you just echo me?" accusation collapsed
        # a whole run into a verbatim repeat loop. Fictional personas get a
        # scene-not-conversation bound; memorial prompts keep their shape.
        persona = _persona()
        persona.metadata = {"framing_class": "fictional"}
        ctx = ContextBuilder().filter_and_render(
            [], persona, RelationshipTier.CLOSE_FRIEND, user_name="Monica"
        )
        assert "Play the scene, not the conversation" in ctx.system_prompt

    def test_memorial_persona_has_no_dynamics_bound(self):
        ctx = ContextBuilder().filter_and_render(
            [], _persona(), RelationshipTier.CLOSE_FRIEND, user_name="Monica"
        )
        assert "Play the scene, not the conversation" not in ctx.system_prompt

    def test_interlocutor_unknown_renders_stranger_line(self):
        # No name = first contact: the persona is told meeting-new-people
        # behavior (introduce yourself, ask about them) is natural.
        ctx = ContextBuilder().filter_and_render([], _persona(), RelationshipTier.FAMILY)
        assert "don't know" in ctx.system_prompt
        assert "introduce yourself" in ctx.system_prompt

    def test_interlocutor_blank_name_treated_as_stranger(self):
        ctx = ContextBuilder().filter_and_render(
            [], _persona(), RelationshipTier.FAMILY, user_name="   "
        )
        assert "introduce yourself" in ctx.system_prompt

    def test_system_prompt_contains_texting_concision_directive(self):
        # HU-1911 human-touch gate: every persona system prompt carries the
        # texting channel shape (length bound, no lists, one-line disclosure).
        ctx = ContextBuilder().filter_and_render([], _persona(), RelationshipTier.FAMILY)
        assert TEXTING_CONCISION_DIRECTIVE in ctx.system_prompt
        # On the distress branch too — the channel bound must never drop.
        distressed = ContextBuilder().filter_and_render(
            [], _persona(), RelationshipTier.FAMILY, user_affect=UserAffect.DISTRESS
        )
        assert TEXTING_CONCISION_DIRECTIVE in distressed.system_prompt

    def test_memory_block_format(self):
        activated = [
            _activated(_node(content="Fishing story", content_type=ContentType.NARRATIVE)),
            _activated(_node(content="Born in Austin", content_type=ContentType.FACT)),
        ]
        ctx = ContextBuilder().filter_and_render(activated, _persona(), RelationshipTier.FAMILY)
        assert "[NARRATIVE] Fishing story" in ctx.memory_blocks
        assert "[FACT] Born in Austin" in ctx.memory_blocks

    def test_empty_memories_renders_blank(self):
        ctx = ContextBuilder().filter_and_render([], _persona(), RelationshipTier.FAMILY)
        assert ctx.memory_blocks == ""

    def test_history_window_tail_plus_w4_head(self):
        # W4 (M-0R-B): pre-window turns stay as a bounded verbatim head, so
        # the session's opening turns are never evicted (RC-3).
        turns = [ConversationTurn(speaker=f"s{i}", content=f"m{i}") for i in range(25)]
        ctx = ContextBuilder().filter_and_render(
            [], _persona(), RelationshipTier.FAMILY, conversation_history=turns,
        )
        lines = [ln for ln in ctx.conversation_history.split("\n") if ln]
        assert len(lines) == 25  # nothing evicted below the one-block bound
        assert lines[0] == "s0: m0"
        assert lines[-1] == "s24: m24"

    def test_history_head_capped_at_one_block(self):
        # 10-turn tail + 30-turn head = one 40-turn gist block; older turns
        # are the TencentDB working-memory digest's job (W4).
        turns = [ConversationTurn(speaker=f"s{i}", content=f"m{i}") for i in range(60)]
        ctx = ContextBuilder().filter_and_render(
            [], _persona(), RelationshipTier.FAMILY, conversation_history=turns,
        )
        lines = [ln for ln in ctx.conversation_history.split("\n") if ln]
        assert len(lines) == 40
        assert lines[0] == "s20: m20"
        assert lines[-1] == "s59: m59"

    def test_history_format(self):
        turns = [
            ConversationTurn(speaker="Michael", content="Hi Dad"),
            ConversationTurn(speaker="Bob", content="Well hey there"),
        ]
        ctx = ContextBuilder().filter_and_render(
            [], _persona(), RelationshipTier.FAMILY, conversation_history=turns,
        )
        assert "Michael: Hi Dad" in ctx.conversation_history
        assert "Bob: Well hey there" in ctx.conversation_history

    def test_constraints_present(self):
        ctx = ContextBuilder().filter_and_render([], _persona(), RelationshipTier.FAMILY)
        assert len(ctx.constraints) >= 3

    def test_render_full_prompt_has_sections(self):
        activated = [_activated(_node(content="A memory"))]
        ctx = ContextBuilder().filter_and_render(
            activated, _persona(), RelationshipTier.FAMILY,
            current_message="Tell me about fishing",
        )
        prompt = ctx.render()
        assert "SYSTEM:" in prompt
        assert "ACTIVATED MEMORIES:" in prompt
        assert "A memory" in prompt
        assert "CONVERSATION HISTORY:" in prompt
        assert "CURRENT MESSAGE:" in prompt
        assert "Tell me about fishing" in prompt

    def test_activation_order_preserved(self):
        activated = [
            _activated(_node(content="first"), activation=0.9),
            _activated(_node(content="second"), activation=0.8),
            _activated(_node(content="third"), activation=0.7),
        ]
        ctx = ContextBuilder().filter_and_render(activated, _persona(), RelationshipTier.FAMILY)
        assert [m.content for m in ctx.included_memories] == ["first", "second", "third"]


# ---------------------------------------------------------------------------
# End-to-end build() with a minimal fake backend
# ---------------------------------------------------------------------------


class _FakeBackend:
    """Minimal in-memory backend with real cosine similarity on content vectors."""

    def __init__(self) -> None:
        self._memories: dict[UUID, MemoryNode] = {}
        self._content_vectors: list[tuple[list[float], UUID]] = []

    async def store_memory(self, node: MemoryNode) -> UUID:
        self._memories[node.id] = node
        if node.embedding_content:
            self._content_vectors.append((node.embedding_content, node.id))
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
        results = []
        for vec, node_id in self._content_vectors:
            node = self._memories[node_id]
            if node.persona_id != persona_id:
                continue
            dot = sum(q * e for q, e in zip(query_embedding, vec, strict=False))
            if dot > 0.0:
                results.append(SearchResult(node=node, score=dot))
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]

    async def search_by_sensory(self, *args: Any, **kwargs: Any) -> list[SearchResult]:
        return []

    async def search_by_affect(self, *args: Any, **kwargs: Any) -> list[SearchResult]:
        return []

    async def get_edges(self, memory_id: UUID) -> list[MemoryEdge]:
        return []

    async def get_active_memories(
        self, persona_id: UUID, limit: int = 50
    ) -> list[MemoryNode]:
        self.last_active_scan_limit = limit
        return [
            node
            for node in self._memories.values()
            if node.persona_id == persona_id and node.is_active
        ][:limit]


class _FactsOnlyBackend(_FakeBackend):
    """Backend exposing only the embedding-free facts scan (HU-2070)."""

    async def get_active_memories(self, persona_id: UUID, limit: int = 50):
        raise AssertionError("grounding scan must prefer get_active_memory_facts")

    async def get_active_memory_facts(
        self, persona_id: UUID, limit: int = 50
    ) -> list[MemoryNode]:
        self.facts_scan_limit = limit
        return [
            node
            for node in self._memories.values()
            if node.persona_id == persona_id and node.is_active
        ][:limit]


def _vec(token: str) -> list[float]:
    """Deterministic 8-dim one-hot-ish vector so identical tokens cosine=1.0."""
    v = [0.0] * 8
    v[hash(token) % 8] = 1.0
    return v


class TestBuildEndToEnd:
    async def test_build_filters_quarantine_and_low_from_retrieval(self):
        backend = _FakeBackend()
        # HIGH memory matching the query -> should surface.
        await backend.store_memory(
            _node(content="fishing trip", confidence_level=ConfidenceLevel.HIGH,
                  embedding_content=_vec("fishing"))
        )
        # QUARANTINE memory also matching -> must be hard-excluded.
        await backend.store_memory(
            _node(content="fishing lie", confidence_level=ConfidenceLevel.QUARANTINE,
                  embedding_content=_vec("fishing"))
        )
        # LOW memory also matching -> must be hard-excluded.
        await backend.store_memory(
            _node(content="fishing rumor", confidence_level=ConfidenceLevel.LOW,
                  embedding_content=_vec("fishing"))
        )

        ctx = await ContextBuilder().build(
            persona=_persona(),
            requester_tier=RelationshipTier.FAMILY,
            backend=backend,
            query_embedding_content=_vec("fishing"),
            retrieval_config=RetrievalConfig(
                activation_threshold=0.0, seed_top_k=20, max_activated=20,
            ),
        )
        contents = [m.content for m in ctx.included_memories]
        assert "fishing trip" in contents
        assert "fishing lie" not in contents
        assert "fishing rumor" not in contents
        assert ctx.exclusion_counts.get("confidence_quarantine") == 1
        assert ctx.exclusion_counts.get("confidence_low") == 1

    async def test_build_acquaintance_never_sees_private(self):
        backend = _FakeBackend()
        await backend.store_memory(
            _node(content="public fact", disclosure_scope=DisclosureScope.ALL_CONTACTS,
                  embedding_content=_vec("topic"))
        )
        await backend.store_memory(
            _node(content="private secret", disclosure_scope=DisclosureScope.PRIVATE,
                  embedding_content=_vec("topic"))
        )

        ctx = await ContextBuilder().build(
            persona=_persona(),
            requester_tier=RelationshipTier.ACQUAINTANCE,
            backend=backend,
            query_embedding_content=_vec("topic"),
            retrieval_config=RetrievalConfig(
                activation_threshold=0.0, seed_top_k=20, max_activated=20,
            ),
        )
        contents = [m.content for m in ctx.included_memories]
        assert "public fact" in contents
        assert "private secret" not in contents
        for node in ctx.included_memories:
            assert node.disclosure_scope != DisclosureScope.PRIVATE


# ---------------------------------------------------------------------------
# HU-2070: persona-scoped grounding refs (§7.4.2 corpus widening)
# ---------------------------------------------------------------------------


class TestPersonaScopedGroundingRefs:
    async def test_returns_only_g4_admissible_memories(self):
        backend = _FakeBackend()
        await backend.store_memory(_node(content="admissible fact"))
        await backend.store_memory(
            _node(content="quarantined", confidence_level=ConfidenceLevel.QUARANTINE)
        )
        await backend.store_memory(
            _node(content="low rumor", confidence_level=ConfidenceLevel.LOW)
        )
        await backend.store_memory(
            _node(content="private secret", disclosure_scope=DisclosureScope.PRIVATE)
        )
        await backend.store_memory(
            _node(content="future fact", memory_date=date(2021, 1, 1))
        )

        refs = await ContextBuilder().persona_scoped_grounding_refs(
            persona=_persona(),
            requester_tier=RelationshipTier.FAMILY,
            backend=backend,
        )
        assert [n.content for n in refs] == ["admissible fact"]

    async def test_acquaintance_scope_excludes_family_scoped(self):
        backend = _FakeBackend()
        await backend.store_memory(
            _node(content="public fact", disclosure_scope=DisclosureScope.ALL_CONTACTS)
        )
        await backend.store_memory(_node(content="family fact"))

        refs = await ContextBuilder().persona_scoped_grounding_refs(
            persona=_persona(),
            requester_tier=RelationshipTier.ACQUAINTANCE,
            backend=backend,
        )
        assert [n.content for n in refs] == ["public fact"]

    async def test_scan_uses_grounding_scope_limit(self):
        backend = _FakeBackend()

        await ContextBuilder().persona_scoped_grounding_refs(
            persona=_persona(),
            requester_tier=RelationshipTier.FAMILY,
            backend=backend,
        )
        assert backend.last_active_scan_limit == ContextBuilder.GROUNDING_SCOPE_SCAN_LIMIT

    async def test_facts_scan_preferred_and_gates_applied(self):
        """The embedding-free facts read is used when present, with the same
        G4 gates (quarantined content never grounds a claim)."""
        backend = _FactsOnlyBackend()
        await backend.store_memory(_node(content="admissible fact"))
        await backend.store_memory(
            _node(content="quarantined", confidence_level=ConfidenceLevel.QUARANTINE)
        )

        refs = await ContextBuilder().persona_scoped_grounding_refs(
            persona=_persona(),
            requester_tier=RelationshipTier.FAMILY,
            backend=backend,
        )
        assert [n.content for n in refs] == ["admissible fact"]
        assert backend.facts_scan_limit == ContextBuilder.GROUNDING_SCOPE_SCAN_LIMIT


class TestActivationScorePassthrough:
    """W1 trace-score passthrough (M-0R-A observability prerequisite)."""

    def test_filter_and_render_populates_activation_scores(self):
        node = _node(content="admissible high-conf", confidence_level=ConfidenceLevel.HIGH)
        builder = ContextBuilder()
        ctx = builder.filter_and_render(
            [_activated(node, activation=0.73)],
            persona=_persona(),
            requester_tier=RelationshipTier.FAMILY,
        )
        assert node.id in ctx.activation_scores
        assert ctx.activation_scores[node.id] == pytest.approx(0.73)

    def test_excluded_memories_have_no_score(self):
        quarantined = _node(
            content="quarantined", confidence_level=ConfidenceLevel.QUARANTINE
        )
        admitted = _node(content="clean fact", confidence_level=ConfidenceLevel.HIGH)
        ctx = ContextBuilder().filter_and_render(
            [_activated(quarantined, activation=0.99), _activated(admitted, activation=0.42)],
            persona=_persona(),
            requester_tier=RelationshipTier.FAMILY,
        )
        assert [m.id for m in ctx.included_memories] == [admitted.id]
        assert set(ctx.activation_scores) == {admitted.id}
        assert ctx.activation_scores[admitted.id] == pytest.approx(0.42)


class _IndexBackend(_FakeBackend):
    """Fake backend whose conversation-index read is exact (r12 lane contract).

    Mirrors ``PostgresMemoryBackend.get_conversation_index_memories``: the
    index is found by metadata, NEVER by embedding rank."""

    def __init__(self) -> None:
        super().__init__()
        self.index_nodes: list[MemoryNode] = []

    async def store_memory(self, node: MemoryNode) -> UUID:
        if (node.metadata or {}).get("kind") == "conversation_index":
            self.index_nodes.append(node)
        return await super().store_memory(node)

    async def get_conversation_index_memories(
        self, persona_id: UUID, limit: int = 20
    ) -> list[MemoryNode]:
        mine = [n for n in self.index_nodes if n.persona_id == persona_id]
        mine.sort(key=lambda n: n.created_at, reverse=True)
        return mine[:limit]


_RECALL_PROBE = (
    "wait wait — earlier, in our last conversation — what was the very "
    "first thing i said to you?"
)


class TestOrdinalIndexLane:
    """HU-2774 r12: the ordinal-recall lane must find the conversation index
    deterministically. r11-friends-1 regression: the lane pre-filtered with a
    vector top-16 over the whole persona store, so a vault-noise-outranked
    index made the lane silently render nothing (Monica: "I don't have any
    earlier texts") while Chandler's identical probe fired in the same slot."""

    @staticmethod
    def _index_node(
        content: str,
        persona_id: UUID = PERSONA_ID,
        disclosure_scope: DisclosureScope = DisclosureScope.CLOSE_FRIENDS,
    ) -> MemoryNode:
        return MemoryNode(
            id=uuid4(),
            persona_id=persona_id,
            tier=MemoryTier.ACCRUED,
            content=content,
            content_type=ContentType.NARRATIVE,
            embedding_content=[0.0] * 8,  # orthogonal to any probe vector
            source_type=SourceType.CONVERSATION,
            disclosure_scope=disclosure_scope,
            metadata={"kind": "conversation_index",
                      "confidence_level": "medium"},
        )

    async def test_lane_fires_even_when_index_ranks_out_of_vector_noise(self):
        backend = _IndexBackend()
        # Vault noise: 30 high-confidence lines that all match the probe's
        # embedding far better than the orthogonal index ever could.
        for i in range(30):
            await backend.store_memory(_node(
                content=f"vault line {i} about the first thing i said",
                confidence_level=ConfidenceLevel.HIGH,
            ))
        index = await backend.store_memory(self._index_node(
            'Conversation index: the first thing Chandler said to Monica '
            'was: "Hey! Pretty good — I successfully avoided doing laundry '
            'twice."'
        ))

        ctx = await ContextBuilder().build(
            persona=_persona(),
            requester_tier=RelationshipTier.FAMILY,
            backend=backend,
            query_embedding_content=_vec("fishing"),
            current_message=_RECALL_PROBE,
            working_memory="",
        )
        prompt = ctx.render()
        assert "successfully avoided doing laundry" in prompt

    async def test_lane_silent_when_no_index_exists(self):
        backend = _IndexBackend()
        ctx = await ContextBuilder().build(
            persona=_persona(),
            requester_tier=RelationshipTier.FAMILY,
            backend=backend,
            query_embedding_content=_vec("fishing"),
            current_message=_RECALL_PROBE,
            working_memory="",
        )
        assert "successfully avoided doing laundry" not in ctx.render()

    async def test_lane_respects_gates_private_scope(self):
        backend = _IndexBackend()
        node = self._index_node(
            'Conversation index: the first thing X said was: "private line"',
            disclosure_scope=DisclosureScope.PRIVATE,
        )
        await backend.store_memory(node)
        ctx = await ContextBuilder().build(
            persona=_persona(),
            requester_tier=RelationshipTier.ACQUAINTANCE,
            backend=backend,
            query_embedding_content=_vec("fishing"),
            current_message=_RECALL_PROBE,
            working_memory="",
        )
        assert "private line" not in ctx.render()

    async def test_lane_not_triggered_by_ordinary_message(self):
        backend = _IndexBackend()
        await backend.store_memory(self._index_node(
            'Conversation index: the first thing X said was: "laundry again"'
        ))
        ctx = await ContextBuilder().build(
            persona=_persona(),
            requester_tier=RelationshipTier.FAMILY,
            backend=backend,
            query_embedding_content=_vec("fishing"),
            current_message="hey, how's your week been?",
            working_memory="",
        )
        assert "laundry again" not in ctx.render()

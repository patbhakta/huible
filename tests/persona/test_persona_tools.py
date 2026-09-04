"""W5 persona tools tests (HU-2309 v1.8 §1.7.2 / M-0R-E).

Unit coverage for :mod:`huible.persona.tools` (era clock, caretaker routing
classifiers, caretaker copy) and the ContextBuilder wiring:

- the in-world era clock is deterministic and era-gated (the persona's
  "today" pins to ``era_knowledge_boundary`` once the real date passes it;
  the time-of-day carries through; an unparseable boundary is fail-closed);
- the temporal/interest classifiers are narrow shape discriminators —
  conversational/autobiographical temporal references never route out of
  voice;
- the era-clock line renders into the system prompt only when a clock is
  handed to the builder;
- the interest tool grounds interest-shaped turns in the persona's own
  era-admissible preference/fact vault lines (the vault-derived
  interest/topic map) — out-of-era and gated candidates never render.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

from huible.memory.protocol import (
    ContentType,
    DisclosureScope,
    MemoryNode,
    MemoryTier,
    SearchResult,
    SourceType,
)
from huible.memory.retrieval import RetrievalConfig
from huible.persona.context import (
    CONFIDENCE_LEVEL_METADATA_KEY,
    ConfidenceLevel,
    ContextBuilder,
    PersonaConfig,
    RelationshipTier,
)
from huible.persona.tools import (
    caretaker_reply,
    era_clock_system_line,
    in_world_now,
    is_interest_question,
    is_temporal_question,
    parse_era_boundary,
)

PERSONA_ID = uuid4()


# ---------------------------------------------------------------------------
# Era clock units
# ---------------------------------------------------------------------------


class TestParseEraBoundary:
    def test_valid(self):
        assert parse_era_boundary("2004-05-06") == date(2004, 5, 6)

    def test_missing_is_none(self):
        assert parse_era_boundary("") is None
        assert parse_era_boundary(None) is None

    def test_garbage_fails_closed(self):
        assert parse_era_boundary("pre-2004") is None


class TestInWorldNow:
    NOW = datetime(2026, 9, 4, 14, 5, tzinfo=UTC)
    BOUNDARY = date(2004, 5, 6)

    def test_past_boundary_pins_date_and_keeps_time_of_day(self):
        in_world = in_world_now(self.NOW, self.BOUNDARY)
        assert in_world is not None
        assert in_world.date() == self.BOUNDARY
        assert in_world.time() == self.NOW.time()

    def test_never_reports_a_date_past_the_boundary(self):
        in_world = in_world_now(self.NOW, self.BOUNDARY)
        assert in_world is not None
        assert in_world.date() <= self.BOUNDARY

    def test_in_era_real_date_passes_through(self):
        now = datetime(2003, 10, 31, 9, 41, tzinfo=UTC)
        in_world = in_world_now(now, self.BOUNDARY)
        assert in_world == now

    def test_boundary_day_itself_is_in_era(self):
        now = datetime(2004, 5, 6, 21, 0, tzinfo=UTC)
        assert in_world_now(now, self.BOUNDARY) == now

    def test_missing_boundary_fails_closed(self):
        assert in_world_now(self.NOW, None) is None


class TestEraClockLine:
    def test_line_carries_the_pinned_date_and_era_clause(self):
        in_world = datetime(2004, 5, 6, 14, 5, tzinfo=UTC)
        line = era_clock_system_line(in_world)
        assert "In-world clock:" in line
        assert "May 6, 2004" in line
        assert "14:05" in line
        assert "never state the real-world current date" in line

    def test_none_renders_nothing(self):
        assert era_clock_system_line(None) == ""


# ---------------------------------------------------------------------------
# Caretaker routing classifier (temporal-question shape)
# ---------------------------------------------------------------------------


class TestTemporalClassifier:
    def test_temporal_questions_match(self):
        for message in (
            "what day is it?",
            "What time is it?",
            "what's the date today?",
            "Whats the time",
            "what year is it?",
            "what year are we in?",
            "What's the time over there?",
            "hey — today's date, go",
            "how late is it?",
            "is it morning there?",
            "do you know the time?",
            "do you know the date?",
        ):
            assert is_temporal_question(message), message

    def test_persona_turns_never_match(self):
        """Autobiographical/conversational temporal refs stay persona-voiced."""
        for message in (
            "what was the first thing I said to you?",
            "what are you talking about?",
            "remember what you said earlier today?",
            "what do you think about it?",
            "what day did we meet?",
            "what time did the game start?",
            "hey you guys!",
            "do you know the time of our foosball final?",
            "",
        ):
            assert not is_temporal_question(message), message


class TestCaretakerReply:
    def test_reply_is_labeled_out_of_persona_and_carries_the_real_date(self):
        now = datetime(2026, 9, 4, 14, 5, tzinfo=UTC)
        reply = caretaker_reply(now, "Chandler")
        assert reply.startswith("[Caretaker — out of character, not Chandler]")
        assert "September 4, 2026" in reply
        assert "14:05" in reply
        assert "Friday" in reply

    def test_reply_never_claims_the_persona_voice(self):
        now = datetime(2026, 9, 4, 14, 5, tzinfo=UTC)
        reply = caretaker_reply(now, "Chandler")
        assert "Chandler's world doesn't include this" in reply


# ---------------------------------------------------------------------------
# Interest classifier (hobby-question shape)
# ---------------------------------------------------------------------------


class TestInterestClassifier:
    def test_interest_questions_match(self):
        for message in (
            "do you like your job?",
            "Do you enjoy foosball?",
            "are you into any sports?",
            "what do you do for fun?",
            "what are your hobbies?",
            "what's your favorite movie?",
            "what do you do in your free time?",
            "tell me about your hobbies",
        ):
            assert is_interest_question(message), message

    def test_plain_turns_never_match(self):
        for message in (
            "hey you guys!",
            "how was your day?",
            "what was the first thing I said to you?",
            "I had a rough day at work.",
            "",
        ):
            assert not is_interest_question(message), message


# ---------------------------------------------------------------------------
# ContextBuilder wiring
# ---------------------------------------------------------------------------


def _node(
    *,
    content: str = "general — is: filler",
    content_type: ContentType = ContentType.FACT,
    disclosure_scope: DisclosureScope = DisclosureScope.FAMILY,
    memory_date: date | None = None,
    confidence_level: ConfidenceLevel | None = ConfidenceLevel.MEDIUM,
    persona_id: UUID = PERSONA_ID,
) -> MemoryNode:
    metadata: dict[str, Any] = {}
    if confidence_level is not None:
        metadata[CONFIDENCE_LEVEL_METADATA_KEY] = confidence_level.value
    return MemoryNode(
        id=uuid4(),
        persona_id=persona_id,
        tier=MemoryTier.ACCRUED,
        content=content,
        content_type=content_type,
        embedding_content=[0.5],
        memory_date=memory_date,
        source_type=SourceType.EXTRACTION,
        disclosure_scope=disclosure_scope,
        metadata=metadata,
    )


class _InterestBackend:
    """Fake backend serving scripted seeds for any vector search."""

    def __init__(self, seeds: list[SearchResult]) -> None:
        self.seeds = seeds
        self.search_calls = 0
        self._known: dict[UUID, MemoryNode] = {sr.node.id: sr.node for sr in seeds}

    async def get_memory(self, memory_id: Any) -> MemoryNode | None:
        return self._known.get(memory_id)

    async def search_by_content(
        self,
        persona_id: Any,
        query_embedding: list[float],
        top_k: int = 20,
        disclosure_scope: DisclosureScope | None = None,
    ) -> list[SearchResult]:
        self.search_calls += 1
        return self.seeds[:top_k]

    async def search_by_sensory(
        self,
        persona_id: Any,
        query_embedding: list[float],
        top_k: int = 20,
        disclosure_scope: DisclosureScope | None = None,
    ) -> list[SearchResult]:
        return []

    async def search_by_affect(
        self,
        persona_id: Any,
        query_embedding: list[float],
        top_k: int = 20,
        disclosure_scope: DisclosureScope | None = None,
    ) -> list[SearchResult]:
        return []

    async def get_edges(self, memory_id: Any) -> list:
        return []


def _persona() -> PersonaConfig:
    return PersonaConfig(
        id=PERSONA_ID,
        name="Chandler",
        era_knowledge_boundary="2004-05-06",
    )


def _seed(node: MemoryNode, score: float = 0.7) -> SearchResult:
    return SearchResult(node=node, score=score)


class TestBuilderEraClockLine:
    async def test_clock_line_renders_when_clock_is_passed(self):
        now = datetime(2026, 9, 4, 14, 5, tzinfo=UTC)
        ctx = await ContextBuilder().build(
            persona=_persona(),
            requester_tier=RelationshipTier.FAMILY,
            backend=_InterestBackend([]),
            query_embedding_content=[0.1],
            real_now=now,
        )
        assert "In-world clock:" in ctx.system_prompt
        assert "May 6, 2004" in ctx.system_prompt

    async def test_no_clock_keeps_pre_w5_prompt_shape(self):
        ctx = await ContextBuilder().build(
            persona=_persona(),
            requester_tier=RelationshipTier.FAMILY,
            backend=_InterestBackend([]),
            query_embedding_content=[0.1],
        )
        assert "In-world clock:" not in ctx.system_prompt

    async def test_unparseable_boundary_fails_closed_no_clock(self):
        persona = PersonaConfig(id=PERSONA_ID, name="Chandler", era_knowledge_boundary="the 90s")
        ctx = await ContextBuilder().build(
            persona=persona,
            requester_tier=RelationshipTier.FAMILY,
            backend=_InterestBackend([]),
            query_embedding_content=[0.1],
            real_now=datetime(2026, 9, 4, tzinfo=UTC),
        )
        assert "In-world clock:" not in ctx.system_prompt


class TestInterestToolLane:
    async def test_interest_turn_grounded_in_vault_lines(self):
        pref = _node(
            content="general — is: I love foosball, I am basically a professional.",
            content_type=ContentType.PREFERENCE,
        )
        backend = _InterestBackend([_seed(pref)])
        ctx = await ContextBuilder().build(
            persona=_persona(),
            requester_tier=RelationshipTier.FAMILY,
            backend=backend,
            query_embedding_content=[0.1],
            current_message="do you like foosball?",
        )
        assert ctx.interest_tool_fired
        assert "[INTEREST] I love foosball" in ctx.render()
        assert "YOUR INTERESTS" in ctx.render()

    async def test_hard_gates_apply_to_interest_lines(self):
        """Out-of-era / non-interest / gated candidates never render."""
        out_of_era = _node(
            content="general — is: loves the 2020 streaming shows",
            content_type=ContentType.PREFERENCE,
            memory_date=date(2020, 6, 1),
        )
        narrative = _node(
            content="general — is: a narrative line",
            content_type=ContentType.NARRATIVE,
        )
        low_conf = _node(
            content="general — is: low confidence preference",
            content_type=ContentType.PREFERENCE,
            confidence_level=ConfidenceLevel.LOW,
        )
        in_era = _node(
            content="general — is: cannot stand crossword puzzles.",
            content_type=ContentType.PREFERENCE,
        )
        backend = _InterestBackend(
            [_seed(n, score=0.8) for n in (out_of_era, narrative, low_conf, in_era)]
        )
        ctx = await ContextBuilder().build(
            persona=_persona(),
            requester_tier=RelationshipTier.FAMILY,
            backend=backend,
            query_embedding_content=[0.1],
            current_message="what are your hobbies?",
        )
        assert len(ctx.interest_exemplars) == 1
        assert ctx.interest_exemplars[0].id == in_era.id
        rendered = ctx.render()
        assert "2020 streaming shows" not in rendered

    async def test_non_interest_turn_never_probes(self):
        backend = _InterestBackend([_seed(_node())])
        ctx = await ContextBuilder().build(
            persona=_persona(),
            requester_tier=RelationshipTier.FAMILY,
            backend=backend,
            query_embedding_content=[0.1],
            current_message="hey you guys!",
        )
        # 1 call = the main retrieval lane only; the interest probe never ran.
        assert backend.search_calls == 1
        assert not ctx.interest_tool_fired
        assert "YOUR INTERESTS" not in ctx.render()

    async def test_disabled_lane_never_probes(self):
        backend = _InterestBackend([_seed(_node())])
        ctx = await ContextBuilder().build(
            persona=_persona(),
            requester_tier=RelationshipTier.FAMILY,
            backend=backend,
            query_embedding_content=[0.1],
            current_message="do you like movies?",
            interest_tool=False,
        )
        assert backend.search_calls == 1
        assert not ctx.interest_tool_fired

    async def test_below_floor_interest_seeds_skipped(self):
        backend = _InterestBackend([_seed(_node(), score=0.1)])
        ctx = await ContextBuilder().build(
            persona=_persona(),
            requester_tier=RelationshipTier.FAMILY,
            backend=backend,
            query_embedding_content=[0.1],
            current_message="do you like movies?",
            retrieval_config=RetrievalConfig(activation_threshold=0.3),
        )
        assert ctx.interest_exemplars == []
        assert not ctx.interest_tool_fired

    async def test_interest_lines_are_a_distinct_prompt_section(self):
        """Evidence separation: the interest section is prompt surface, not
        retrieval output — it renders in its own [INTEREST] block and is the
        only thing the lane toggles."""
        pref = _node(
            content="general — is: I love foosball.",
            content_type=ContentType.PREFERENCE,
        )
        backend = _InterestBackend([_seed(pref)])
        ctx = await ContextBuilder().build(
            persona=_persona(),
            requester_tier=RelationshipTier.FAMILY,
            backend=backend,
            query_embedding_content=[0.1],
            current_message="what do you do for fun?",
        )
        rendered = ctx.render()
        assert "YOUR INTERESTS" in rendered
        assert "[INTEREST] I love foosball." in rendered
        assert ctx.interest_tool_fired
        # Toggling the lane only removes the section — retrieval is untouched.
        ctx_off = await ContextBuilder().build(
            persona=_persona(),
            requester_tier=RelationshipTier.FAMILY,
            backend=backend,
            query_embedding_content=[0.1],
            current_message="what do you do for fun?",
            interest_tool=False,
        )
        assert "YOUR INTERESTS" not in ctx_off.render()
        assert [n.id for n in ctx_off.included_memories] == [n.id for n in ctx.included_memories]

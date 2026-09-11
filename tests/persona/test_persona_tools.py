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
    PINNED_NOON,
    PERSONA_IN_WORLD_CLOCK_KEY,
    caretaker_reply,
    era_clock_system_line,
    in_world_now,
    is_career_question,
    is_current_events_question,
    is_emotion_question,
    is_interest_question,
    is_temporal_question,
    parse_era_boundary,
    resolve_in_world_time_of_day,
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

    def test_time_of_day_override_pins_noon_keeps_date_pin(self):
        in_world = in_world_now(self.NOW, self.BOUNDARY, time_of_day=PINNED_NOON)
        assert in_world is not None
        assert in_world.date() == self.BOUNDARY
        assert in_world.time() == PINNED_NOON

    def test_time_of_day_override_respected_in_era_too(self):
        now = datetime(2003, 10, 31, 2, 55, tzinfo=UTC)
        in_world = in_world_now(now, self.BOUNDARY, time_of_day=PINNED_NOON)
        assert in_world is not None
        assert in_world.date() == now.date()
        assert in_world.time() == PINNED_NOON


class TestResolveInWorldTimeOfDay:
    def test_noon_metadata_pins_noon(self):
        assert resolve_in_world_time_of_day({PERSONA_IN_WORLD_CLOCK_KEY: "noon"}) == PINNED_NOON

    def test_absent_metadata_is_none(self):
        assert resolve_in_world_time_of_day(None) is None
        assert resolve_in_world_time_of_day({}) is None

    def test_invalid_mode_falls_back_to_wall_clock(self):
        assert resolve_in_world_time_of_day({PERSONA_IN_WORLD_CLOCK_KEY: "midnight"}) is None
        assert resolve_in_world_time_of_day({PERSONA_IN_WORLD_CLOCK_KEY: 42}) is None


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
# M1.4 scoped-lane classifiers (HU-2732)
# ---------------------------------------------------------------------------


class TestScopedLaneClassifiers:
    def test_current_events_questions_match(self):
        for message in (
            "anything in the news?",
            "so what's going on in the world?",
            "what's happening in town?",
            "did you hear about the game?",
            "have you heard the news?",
            "who won the game last night?",
            "any news?",
        ):
            assert is_current_events_question(message), message

    def test_current_events_bare_greeting_never_matches(self):
        """Narrow classifier doctrine: a bare "what's happening?" is a
        greeting/personal check-in, not a world-events probe."""
        for message in (
            "hey — what's happening?",
            "what's going on with you?",
            "hey you guys!",
            "",
        ):
            assert not is_current_events_question(message), message

    def test_emotion_questions_match(self):
        for message in (
            "how do you feel about all this?",
            "are you okay?",
            "are you lonely?",
            "do you ever feel like giving up?",
            "does that bother you?",
            "do you believe in love?",
            "do you miss her?",
            "how did that make you feel?",
        ):
            assert is_emotion_question(message), message

    def test_emotion_plain_turns_never_match(self):
        for message in (
            "hey you guys!",
            "what time is it?",
            "I love foosball.",
            "",
        ):
            assert not is_emotion_question(message), message

    def test_career_questions_match(self):
        for message in (
            "how's work?",
            "how is work treating you?",
            "how was your day at the office?",
            "what do you do for a living?",
            "where do you work?",
            "what do you do?",
            "how's your boss?",
            "do you like your coworkers?",
        ):
            assert is_career_question(message), message

    def test_career_plain_turns_never_match(self):
        for message in (
            "hey you guys!",
            "I had a rough day.",
            "do you like foosball?",
            "",
        ):
            assert not is_career_question(message), message


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

    async def test_noon_clock_metadata_pins_battery_hour_to_noon(self):
        now = datetime(2026, 9, 4, 2, 55, tzinfo=UTC)  # battery wall-clock hour
        persona = PersonaConfig(
            id=PERSONA_ID,
            name="Chandler",
            era_knowledge_boundary="2004-05-06",
            metadata={PERSONA_IN_WORLD_CLOCK_KEY: "noon"},
        )
        ctx = await ContextBuilder().build(
            persona=persona,
            requester_tier=RelationshipTier.FAMILY,
            backend=_InterestBackend([]),
            query_embedding_content=[0.1],
            real_now=now,
        )
        assert "In-world clock:" in ctx.system_prompt
        assert "May 6, 2004" in ctx.system_prompt
        assert "12:00" in ctx.system_prompt
        assert "02:55" not in ctx.system_prompt

    async def test_without_clock_metadata_wall_time_carries_through(self):
        now = datetime(2026, 9, 4, 2, 55, tzinfo=UTC)
        ctx = await ContextBuilder().build(
            persona=_persona(),
            requester_tier=RelationshipTier.FAMILY,
            backend=_InterestBackend([]),
            query_embedding_content=[0.1],
            real_now=now,
        )
        assert "In-world clock:" in ctx.system_prompt
        assert "02:55" in ctx.system_prompt

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


class TestQuestionExemplarLane:
    """HU-2774 question-affordance lane: the persona's own question-shaped
    vault lines render as register modeling (the corpus asks at ~1/3 of
    lines; generation drifts declarative under banter)."""

    async def _build(self, seeds, message="man, what a week it's been."):
        return await ContextBuilder().build(
            persona=_persona(),
            requester_tier=RelationshipTier.FAMILY,
            backend=_InterestBackend(seeds),
            query_embedding_content=[0.1],
            deflection_probe_embedding=[0.2],
            current_message=message,
        )

    async def test_question_line_renders_ask_block(self):
        q = _node(content="(to chandler) so how was your week at work?")
        stmt = _node(content="I love foosball")
        ctx = await self._build([_seed(stmt), _seed(q)])
        assert len(ctx.question_exemplars) == 1
        assert "[ASK]" in ctx.render()
        assert "QUESTIONS YOU ASK" in ctx.render()

    async def test_lane_suppressed_when_inbound_is_a_question(self):
        # Answering a question is the natural move there — the lane only
        # injects question register on declarative drift (otherwise it
        # compounds into answer-with-question ping-pong, r10-final-verify-1).
        q = _node(content="(to chandler) so how was your week at work?")
        ctx = await self._build([_seed(q)], message="how's your week been?")
        assert ctx.question_exemplars == []
        assert "QUESTIONS YOU ASK" not in ctx.render()

    async def test_no_question_lines_renders_nothing(self):
        stmt = _node(content="I love foosball")
        ctx = await self._build([_seed(stmt)])
        assert ctx.question_exemplars == []
        assert "QUESTIONS YOU ASK" not in ctx.render()

    async def test_inbound_duplicate_question_line_is_damped(self):
        # A vault question that merely restates the inbound never renders
        # (the damping gate runs upstream of the lane).
        dup = _node(content="hey, how's your week been?")
        ctx = await self._build(
            [_seed(dup)], message="hey, how's your week been?"
        )
        assert ctx.question_exemplars == []
        assert ctx.exclusion_counts.get("inbound_duplicate") == 1


# --- HU-2828: persona-location time-of-day (Chandler = NYC) ---------------------


class TestPersonaLocationTimezone:
    def test_resolve_persona_tz_valid_and_invalid(self):
        from zoneinfo import ZoneInfo

        from huible.persona.tools import resolve_persona_tz

        assert (
            resolve_persona_tz({"location_timezone": "America/New_York"})
            == ZoneInfo("America/New_York")
        )
        assert resolve_persona_tz({"location_timezone": "Mars/Olympus"}) is None
        assert resolve_persona_tz({"location_timezone": ""}) is None
        assert resolve_persona_tz({"location_timezone": 12}) is None
        assert resolve_persona_tz(None) is None
        assert resolve_persona_tz({}) is None

    def test_in_world_now_converts_to_persona_location(self):
        from huible.persona.tools import resolve_persona_tz

        # 20:33 UTC = 16:33 America/New_York (EDT, UTC-4 in September).
        real_now = datetime(2026, 9, 11, 20, 33, tzinfo=UTC)
        ny = resolve_persona_tz({"location_timezone": "America/New_York"})
        pinned = in_world_now(real_now, date(2004, 5, 6), tz=ny)
        assert pinned is not None
        assert pinned.date() == date(2004, 5, 6)  # era date pin unchanged
        assert pinned.hour == 16 and pinned.minute == 33
        assert pinned.tzname() == "EDT"

    def test_in_world_now_without_tz_is_byte_identical(self):
        real_now = datetime(2026, 9, 11, 20, 33, tzinfo=UTC)
        legacy = in_world_now(real_now, date(2004, 5, 6))
        assert legacy is not None
        assert legacy.hour == 20 and legacy.minute == 33

    def test_caretaker_renders_persona_location_clock(self):
        from huible.persona.tools import resolve_persona_tz

        real_now = datetime(2026, 9, 11, 20, 33, tzinfo=UTC)
        ny = resolve_persona_tz({"location_timezone": "America/New_York"})
        reply = caretaker_reply(real_now, "Chandler", tz=ny)
        assert "16:33" in reply
        assert "EDT" in reply
        # Legacy call keeps the caller's clock (UTC).
        legacy = caretaker_reply(real_now, "Chandler")
        assert "20:33" in legacy

    def _ctx(self, persona, **kwargs):
        return ContextBuilder().filter_and_render(
            [],
            persona=persona,
            requester_tier=RelationshipTier.FAMILY,
            real_now=datetime(2026, 9, 11, 20, 33, tzinfo=UTC),
            **kwargs,
        )

    def test_machine_flow_keeps_noon_pin_human_portal_opts_out(self):
        from huible.persona.context import PERSONA_FRAMING_CLASS_KEY

        persona = PersonaConfig(
            id=uuid4(),
            name="Chandler",
            voice_instructions="",
            era_knowledge_boundary="2004-05-06",
            metadata={
                PERSONA_FRAMING_CLASS_KEY: "fictional",
                "location_timezone": "America/New_York",
                PERSONA_IN_WORLD_CLOCK_KEY: "noon",
            },
        )
        machine = self._ctx(persona).system_prompt
        clock = [line for line in machine.splitlines() if "In-world clock" in line]
        assert clock and "(12:00)" in clock[0]  # battery-flow pin unchanged
        human = self._ctx(persona, honor_noon_pin=False).system_prompt
        clock = [line for line in human.splitlines() if "In-world clock" in line]
        assert clock and "(16:33)" in clock[0]  # persona-local real time

    def test_realworld_hits_render_and_ground(self):
        from huible.persona.realworld import SearchHit

        persona = PersonaConfig(
            id=uuid4(),
            name="Chandler",
            voice_instructions="",
            era_knowledge_boundary="2004-05-06",
        )
        hits = [
            SearchHit(
                title="Rents",
                url="https://x.test",
                content="Median rent in Greenwich Village is $4,200 a month.",
            )
        ]
        ctx = self._ctx(persona, realworld_hits=hits)
        # r5: the researched facts render as in-voice memory lines in the
        # imitation zone, directly after ACTIVATED MEMORIES — the position
        # canon memories used to win from (portal-5134 live evidence).
        rendered = ctx.render()
        assert "YOUR WORLD RIGHT NOW" in rendered
        assert "[CURWORLD] Median rent in Greenwich Village is $4,200 a month." in rendered
        rendered_after_memories = rendered.split("ACTIVATED MEMORIES:", 1)[1]
        assert "[CURWORLD]" in rendered_after_memories.split("VOICE EXEMPLARS", 1)[0]
        assert "$4,200" in ctx.realworld_grounding
        assert ctx.realworld_lane_fired
        # The system prompt carries the authority line so the researched
        # facts actually win the answer (live probe 2026-09-11: without it
        # the model slid back to canon / invented a score).
        assert "answer from those lines" in ctx.system_prompt

    def test_no_realworld_hits_no_authority_line(self):
        persona = PersonaConfig(
            id=uuid4(),
            name="Chandler",
            voice_instructions="",
            era_knowledge_boundary="2004-05-06",
        )
        ctx = self._ctx(persona)
        assert "answer from those lines" not in ctx.system_prompt
        assert "YOUR WORLD RIGHT NOW" not in ctx.render()
        assert "[CURWORLD]" not in ctx.render()

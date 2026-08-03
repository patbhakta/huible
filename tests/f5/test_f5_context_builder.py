from __future__ import annotations

from dataclasses import dataclass

from tests.f5.conftest import RelationshipTier, build_context, format_turns


@dataclass
class FakeMemory:
    content_type: str = "narrative"
    content: str = "Dad loved fishing on Lake Travis"


@dataclass
class FakeTurn:
    speaker: str = "Michael"
    content: str = "Tell me about fishing with Dad"


class TestF5_1_ContextStructure:
    """F5.1: Context builder produces structured prompt
    with system, memories, history, constraints."""

    def test_has_system_prompt(self, persona_config, family_tier):
        ctx = build_context(persona_config, [], [], family_tier)
        assert "system" in ctx
        assert persona_config.name in ctx["system"]
        assert str(persona_config.age_at_death) in ctx["system"]
        assert family_tier.description in ctx["system"]

    def test_has_voice_instructions(self, persona_config, family_tier):
        ctx = build_context(persona_config, [], [], family_tier)
        assert persona_config.voice_instructions in ctx["system"]

    def test_has_relationship_description(self, persona_config, family_tier):
        ctx = build_context(persona_config, [], [], family_tier)
        assert family_tier.description in ctx["system"]

    def test_empty_memories(self, persona_config, family_tier):
        ctx = build_context(persona_config, [], [], family_tier)
        assert "memories" in ctx
        assert ctx["memories"] == ""

    def test_memory_blocks_formatted(self, persona_config, family_tier):
        memories = [FakeMemory(content_type="narrative", content="Fishing story")]
        ctx = build_context(persona_config, memories, [], family_tier)
        assert "[Memory: narrative]" in ctx["memories"]
        assert "Fishing story" in ctx["memories"]

    def test_multiple_memories(self, persona_config, family_tier):
        memories = [
            FakeMemory(content_type="fact", content="Dad worked at Lockheed"),
            FakeMemory(content_type="sensory", content="Smell of barbecue"),
        ]
        ctx = build_context(persona_config, memories, [], family_tier)
        assert "[Memory: fact]" in ctx["memories"]
        assert "[Memory: sensory]" in ctx["memories"]

    def test_history_formatted(self, persona_config, family_tier):
        history = [
            {"speaker": "Michael", "content": "Hey Dad"},
            {"speaker": "Bob", "content": "Well hey there"},
        ]
        ctx = build_context(persona_config, [], history, family_tier)
        assert "Michael: Hey Dad" in ctx["history"]
        assert "Bob: Well hey there" in ctx["history"]

    def test_constraints_present(self, persona_config, family_tier):
        ctx = build_context(persona_config, [], [], family_tier)
        assert "constraints" in ctx
        assert len(ctx["constraints"]) >= 3

    def test_death_date_constraint(self, persona_config, family_tier):
        ctx = build_context(persona_config, [], [], family_tier)
        assert persona_config.death_date in str(ctx["constraints"])

    def test_knowledge_boundary_constraint(self, persona_config, family_tier):
        ctx = build_context(persona_config, [], [], family_tier)
        assert "would not have known" in str(ctx["constraints"])

    def test_voice_constraint(self, persona_config, family_tier):
        ctx = build_context(persona_config, [], [], family_tier)
        assert "Speak naturally" in str(ctx["constraints"])

    def test_recent_turns_limited_to_10(self, persona_config, family_tier):
        history = [{"speaker": f"s{i}", "content": f"m{i}"} for i in range(20)]
        ctx = build_context(persona_config, [], history, family_tier)
        turns = ctx["history"].strip().split("\n")
        assert len(turns) <= 10


class TestF5_2_RelationshipTier:
    """F5.2: Relationship tier is injected into the system prompt."""

    def test_family_tier(self, persona_config):
        tier = RelationshipTier(description="son", rank=0)
        ctx = build_context(persona_config, [], [], tier)
        assert "son" in ctx["system"]

    def test_acquaintance_tier(self, persona_config):
        tier = RelationshipTier(description="acquaintance", rank=3)
        ctx = build_context(persona_config, [], [], tier)
        assert "acquaintance" in ctx["system"]

    def test_tier_affects_description_only(self, persona_config):
        family = RelationshipTier(description="wife", rank=0)
        friend = RelationshipTier(description="old friend", rank=2)
        ctx_f = build_context(persona_config, [], [], family)
        ctx_r = build_context(persona_config, [], [], friend)
        assert "wife" in ctx_f["system"]
        assert "old friend" in ctx_r["system"]


class TestF5_3_FormatTurns:
    """F5.3: Turn formatting utility."""

    def test_single_turn(self):
        result = format_turns([{"speaker": "A", "content": "hello"}])
        assert result == "A: hello"

    def test_multiple_turns(self):
        turns = [
            {"speaker": "A", "content": "hello"},
            {"speaker": "B", "content": "hi there"},
        ]
        result = format_turns(turns)
        assert "A: hello" in result
        assert "B: hi there" in result

    def test_empty_turns(self):
        assert format_turns([]) == ""

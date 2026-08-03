from __future__ import annotations

from dataclasses import dataclass

import pytest


@dataclass
class PersonaConfig:
    name: str = "Bob"
    age_at_death: int = 72
    voice_instructions: str = "Speak slowly, use Texas expressions, occasional 'well now'"
    death_date: str = "2021-03-15"


@dataclass
class RelationshipTier:
    description: str = "son"
    rank: int = 0


def build_context(
    persona_config: PersonaConfig,
    activated_memories: list,
    conversation_history: list,
    relationship_tier: RelationshipTier,
) -> dict:
    memory_blocks = []
    for mem in activated_memories:
        memory_blocks.append(f"[Memory: {mem.content_type}] {mem.content}")

    recent_turns = conversation_history[-10:]

    return {
        "system": (
            f"You are {persona_config.name}, {persona_config.age_at_death} years old.\n"
            f"{persona_config.voice_instructions}\n"
            f"You are speaking with {relationship_tier.description}."
        ),
        "memories": "\n".join(memory_blocks),
        "history": format_turns(recent_turns),
        "constraints": [
            f"Do not reference anything {persona_config.name} would not have known.",
            f"You died on {persona_config.death_date}. Do not reference events after that date.",
            "Speak naturally. Use your own voice, not formal language.",
        ],
    }


def format_turns(turns: list) -> str:
    lines: list[str] = []
    for turn in turns:
        lines.append(f"{turn['speaker']}: {turn['content']}")
    return "\n".join(lines)


@pytest.fixture
def persona_config() -> PersonaConfig:
    return PersonaConfig()


@pytest.fixture
def family_tier() -> RelationshipTier:
    return RelationshipTier(description="son", rank=0)


@pytest.fixture
def acquaintance_tier() -> RelationshipTier:
    return RelationshipTier(description="acquaintance", rank=3)

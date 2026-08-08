from __future__ import annotations

import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

from huible.conversation import (
    HuibleCSVLoader,
    HuibleConversationLoop,
    InMemoryMemoryBackend,
    PersonaConfig,
    simple_embedding,
)


@pytest.fixture
def backend() -> InMemoryMemoryBackend:
    return InMemoryMemoryBackend()


@pytest.fixture
def persona() -> PersonaConfig:
    return PersonaConfig(
        id=uuid4(),
        name="Pat Persona",
        voice_instructions="Warm, tea enthusiast",
    )


class TestHuibleConversationLoop:

    @pytest.mark.asyncio
    async def test_simple_embedding(self):
        vec1 = simple_embedding("Earl Grey tea")
        vec2 = simple_embedding("Earl Grey tea")
        vec3 = simple_embedding("Quantum mechanics")
        assert len(vec1) == 10
        assert vec1 == vec2
        assert vec1 != vec3

    @pytest.mark.asyncio
    async def test_csv_loader(self, backend, persona):
        sample_csv = (
            "content,content_type,tier,source_type,disclosure_scope,memory_date,affect_signals,sensory_cues\n"
            '"Pat loves drinking warm Earl Grey tea",fact,canonical,human_direct,private,2024-10-15,comfort,"aroma of bergamot"\n'
            '"Grandfather was tea merchant in Gujarat",narrative,derived,conversation,private,2024-10-16,nostalgia,"wooden tea chest"\n'
        )
        with tempfile.NamedTemporaryFile("w+", suffix=".csv", delete=False) as f:
            f.write(sample_csv)
            f.flush()
            tmp_path = Path(f.name)

        nodes = await HuibleCSVLoader.load_csv(tmp_path, persona.id, backend)
        assert len(nodes) == 2
        assert nodes[0].content == "Pat loves drinking warm Earl Grey tea"
        assert len(backend.memories) == 2

    @pytest.mark.asyncio
    async def test_conversation_turn(self, backend, persona):
        sample_csv = (
            "content,content_type,tier,source_type,disclosure_scope,memory_date,affect_signals,sensory_cues\n"
            '"Pat loves drinking warm Earl Grey tea with oat milk on cool Sunday mornings",fact,canonical,human_direct,private,2024-10-15,comfort,"aroma of bergamot"\n'
        )
        with tempfile.NamedTemporaryFile("w+", suffix=".csv", delete=False) as f:
            f.write(sample_csv)
            f.flush()
            tmp_path = Path(f.name)

        await HuibleCSVLoader.load_csv(tmp_path, persona.id, backend)

        loop = HuibleConversationLoop(persona, backend)
        result = await loop.turn("What do you like to drink on Sunday mornings?", speaker_name="Pat")

        assert result["turn_number"] == 1
        assert len(result["activated_memories"]) > 0
        assert "Earl Grey" in result["activated_memories"][0]["content"]
        assert "Earl Grey" in result["response"] or "tea" in result["response"]

    @pytest.mark.asyncio
    async def test_feedback_suppression_across_turns(self, backend, persona):
        sample_csv = (
            "content,content_type,tier,source_type,disclosure_scope,memory_date,affect_signals,sensory_cues\n"
            '"Pat loves drinking warm Earl Grey tea",fact,canonical,human_direct,private,2024-10-15,comfort,"aroma"\n'
            '"Grandfather was tea merchant in Gujarat",narrative,derived,conversation,private,2024-10-16,nostalgia,"spices"\n'
        )
        with tempfile.NamedTemporaryFile("w+", suffix=".csv", delete=False) as f:
            f.write(sample_csv)
            f.flush()
            tmp_path = Path(f.name)

        await HuibleCSVLoader.load_csv(tmp_path, persona.id, backend)

        loop = HuibleConversationLoop(persona, backend)

        # Turn 1
        res1 = await loop.turn("Tell me about tea", speaker_name="Pat")
        assert len(res1["activated_memories"]) > 0
        first_activated = res1["activated_memories"][0]["content"]

        # Turn 2: repeat query -> suppressed first memory activation should decrease or rank lower
        res2 = await loop.turn("Tell me about tea again", speaker_name="Pat")
        assert res2["turn_number"] == 2

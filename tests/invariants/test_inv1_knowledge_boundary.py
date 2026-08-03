"""INV-1: Knowledge Boundary Enforcement

Invariant: The persona must not know things the person couldn't have known.
Memories with memory_date after the persona's death_date must never be
surfaced. Memories from before the persona's birth must also be excluded.

This invariant is enforced by filtering retrieval results through a
knowledge boundary. The retrieval layer returns all matches; the knowledge
boundary filter removes out-of-era memories before they reach the persona.

The filter_by_knowledge_boundary function is the enforcement point.
Tests verify that the filter correctly excludes post-mortem and pre-birth
memories while preserving in-era memories.
"""

from __future__ import annotations

from datetime import date

from huible.memory.protocol import MemoryNode, MemoryTier
from tests.invariants.conftest import PERSONA_ID, make_node

PERSONA_BIRTH_DATE = date(1950, 3, 20)
PERSONA_DEATH_DATE = date(2020, 6, 15)


def filter_by_knowledge_boundary(
    memories: list[MemoryNode],
    birth_date: date | None = None,
    death_date: date | None = None,
) -> list[MemoryNode]:
    """Filter memories to only those within the persona's knowledge era.

    INV-1 enforcement point. If a memory has a memory_date, it must fall
    within [birth_date, death_date]. Memories without a memory_date are
    presumed valid (they lack temporal metadata rather than violating the
    boundary).
    """
    result: list[MemoryNode] = []
    for mem in memories:
        if mem.memory_date is None:
            result.append(mem)
            continue
        if birth_date and mem.memory_date < birth_date:
            continue
        if death_date and mem.memory_date > death_date:
            continue
        result.append(mem)
    return result


class TestInv1NoPostMortemMemories:
    """INV-1a: Memories dated after persona death must be filtered out."""

    def test_post_mortem_excluded(self):
        post = make_node(
            PERSONA_ID,
            content="Event in 2025",
            memory_date=date(2025, 1, 1),
        )
        pre = make_node(
            PERSONA_ID,
            content="Event in 2015",
            memory_date=date(2015, 7, 15),
        )

        filtered = filter_by_knowledge_boundary(
            [post, pre],
            birth_date=PERSONA_BIRTH_DATE,
            death_date=PERSONA_DEATH_DATE,
        )

        assert len(filtered) == 1
        assert filtered[0].id == pre.id

    def test_death_date_boundary_inclusive(self):
        on_death = make_node(
            PERSONA_ID,
            content="On the day",
            memory_date=PERSONA_DEATH_DATE,
        )

        filtered = filter_by_knowledge_boundary(
            [on_death],
            birth_date=PERSONA_BIRTH_DATE,
            death_date=PERSONA_DEATH_DATE,
        )

        assert len(filtered) == 1
        assert filtered[0].id == on_death.id

    def test_multiple_post_mortem_all_excluded(self):
        post_memories = [
            make_node(PERSONA_ID, f"Event {i}", memory_date=date(2021 + i, 1, 1))
            for i in range(10)
        ]
        in_era = make_node(PERSONA_ID, "Valid memory", memory_date=date(2010, 1, 1))

        filtered = filter_by_knowledge_boundary(
            [*post_memories, in_era],
            birth_date=PERSONA_BIRTH_DATE,
            death_date=PERSONA_DEATH_DATE,
        )

        assert len(filtered) == 1
        assert filtered[0].id == in_era.id

    def test_no_memory_date_presumed_valid(self):
        no_date = make_node(PERSONA_ID, content="No date memory", memory_date=None)

        filtered = filter_by_knowledge_boundary(
            [no_date],
            birth_date=PERSONA_BIRTH_DATE,
            death_date=PERSONA_DEATH_DATE,
        )

        assert len(filtered) == 1


class TestInv1NoPreBirthMemories:
    """INV-1b: Memories from before the persona's birth must be filtered out."""

    def test_pre_birth_excluded(self):
        pre_birth = make_node(
            PERSONA_ID,
            content="1940 event",
            memory_date=date(1940, 1, 1),
        )
        in_era = make_node(
            PERSONA_ID,
            content="1960 event",
            memory_date=date(1960, 1, 1),
        )

        filtered = filter_by_knowledge_boundary(
            [pre_birth, in_era],
            birth_date=PERSONA_BIRTH_DATE,
            death_date=PERSONA_DEATH_DATE,
        )

        assert len(filtered) == 1
        assert filtered[0].id == in_era.id

    def test_birth_date_boundary_inclusive(self):
        on_birth = make_node(
            PERSONA_ID,
            content="Day of birth",
            memory_date=PERSONA_BIRTH_DATE,
        )

        filtered = filter_by_knowledge_boundary(
            [on_birth],
            birth_date=PERSONA_BIRTH_DATE,
            death_date=PERSONA_DEATH_DATE,
        )

        assert len(filtered) == 1


class TestInv1WorldKnowledgeEraEnforced:
    """INV-1c: WORLD tier memories also respect the knowledge boundary."""

    def test_world_knowledge_pre_birth_excluded(self):
        world_pre = make_node(
            PERSONA_ID,
            content="WWII ended in 1945",
            tier=MemoryTier.WORLD,
            memory_date=date(1945, 5, 8),
        )
        world_during = make_node(
            PERSONA_ID,
            content="Moon landing 1969",
            tier=MemoryTier.WORLD,
            memory_date=date(1969, 7, 20),
        )
        world_post = make_node(
            PERSONA_ID,
            content="Event after death 2025",
            tier=MemoryTier.WORLD,
            memory_date=date(2025, 1, 1),
        )

        filtered = filter_by_knowledge_boundary(
            [world_pre, world_during, world_post],
            birth_date=PERSONA_BIRTH_DATE,
            death_date=PERSONA_DEATH_DATE,
        )

        filtered_ids = {m.id for m in filtered}
        assert world_pre.id not in filtered_ids
        assert world_post.id not in filtered_ids
        assert world_during.id in filtered_ids

    def test_canonical_facts_within_era_preserved(self):
        canonical = make_node(
            PERSONA_ID,
            content="Born in Austin",
            tier=MemoryTier.CANONICAL,
            memory_date=date(1950, 3, 20),
        )

        filtered = filter_by_knowledge_boundary(
            [canonical],
            birth_date=PERSONA_BIRTH_DATE,
            death_date=PERSONA_DEATH_DATE,
        )

        assert len(filtered) == 1

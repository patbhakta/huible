"""INV-FL: Feedback Loop Suppression

Invariant: Recently activated memories must be suppressed in subsequent
retrieval passes. The suppression factor is applied to memories that were
activated in the last N conversation turns, preventing the same memories
from dominating every retrieval result.

This prevents degenerate feedback loops where the persona repeatedly
retrieves and references the same memories.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from huible.memory.retrieval import (
    ConversationTurn,
    RetrievalConfig,
    apply_suppression,
    get_recently_activated,
    retrieve,
)
from tests.invariants.conftest import PERSONA_ID, make_node

QUERY_VEC = [0.1] * 1536
SUPPRESSION_FACTOR = 0.1
SUPPRESSION_WINDOW = 10


class TestInvFLRecentlyActivatedSuppressed:
    """INV-FLa: Memories activated in recent turns must have reduced activation."""

    async def test_suppressed_memory_has_lower_activation(self, backend):
        memory = make_node(
            PERSONA_ID,
            content="Dad loved fishing",
            embedding_content=[0.5] * 1536,
        )
        await backend.store_memory(memory)

        config = RetrievalConfig(
            activation_threshold=0.0,
            seed_top_k=100,
            max_activated=100,
            suppression_factor=SUPPRESSION_FACTOR,
            suppression_window=SUPPRESSION_WINDOW,
        )

        results_without_suppression = await retrieve(
            backend,
            PERSONA_ID,
            QUERY_VEC,
            config=config,
        )

        history = [ConversationTurn(activated_memory_ids=[memory.id])]
        results_with_suppression = await retrieve(
            backend,
            PERSONA_ID,
            QUERY_VEC,
            conversation_history=history,
            config=config,
        )

        map_without = {am.node.id: am.activation for am in results_without_suppression}
        map_with = {am.node.id: am.activation for am in results_with_suppression}

        if memory.id in map_without and memory.id in map_with:
            assert map_with[memory.id] <= map_without[memory.id] * SUPPRESSION_FACTOR + 1e-6

    async def test_non_suppressed_memories_unchanged(self, backend):
        m1 = make_node(PERSONA_ID, "Memory A", embedding_content=[0.3] * 1536)
        m2 = make_node(PERSONA_ID, "Memory B", embedding_content=[0.5] * 1536)
        await backend.store_memory(m1)
        await backend.store_memory(m2)

        config = RetrievalConfig(
            activation_threshold=0.0,
            seed_top_k=100,
            max_activated=100,
            suppression_factor=SUPPRESSION_FACTOR,
            suppression_window=SUPPRESSION_WINDOW,
        )

        history = [ConversationTurn(activated_memory_ids=[m1.id])]
        results = await retrieve(
            backend,
            PERSONA_ID,
            QUERY_VEC,
            conversation_history=history,
            config=config,
        )

        act_map = {am.node.id: am.activation for am in results}
        if m1.id in act_map and m2.id in act_map:
            assert act_map[m1.id] < act_map[m2.id]


class TestInvFLSuppressionFactorApplied:
    """INV-FLb: The suppression factor must be exactly 0.1 (default)."""

    def test_suppression_factor_applied_correctly(self):
        node_id = uuid4()
        activation_map = {node_id: 1.0}
        recent_ids = {node_id}

        apply_suppression(activation_map, recent_ids, suppression_factor=SUPPRESSION_FACTOR)

        assert activation_map[node_id] == pytest.approx(0.1, abs=1e-9)

    def test_suppression_factor_is_multiplicative(self):
        node_id = uuid4()
        original = 0.85
        activation_map = {node_id: original}
        recent_ids = {node_id}

        apply_suppression(activation_map, recent_ids, suppression_factor=SUPPRESSION_FACTOR)

        assert activation_map[node_id] == pytest.approx(original * SUPPRESSION_FACTOR, abs=1e-9)

    def test_non_recent_memory_unchanged(self):
        recent_id = uuid4()
        other_id = uuid4()
        activation_map = {recent_id: 0.9, other_id: 0.9}
        recent_ids = {recent_id}

        apply_suppression(activation_map, recent_ids, suppression_factor=SUPPRESSION_FACTOR)

        assert activation_map[other_id] == pytest.approx(0.9, abs=1e-9)


class TestInvFLSuppressionWindowEnforced:
    """INV-FLc: Only memories from the last N turns are suppressed."""

    def test_only_last_n_turns_tracked(self):
        window = 3
        id_old = uuid4()
        id_new = uuid4()

        history = [
            ConversationTurn(activated_memory_ids=[id_old]),
            ConversationTurn(activated_memory_ids=[uuid4()]),
            ConversationTurn(activated_memory_ids=[uuid4()]),
            ConversationTurn(activated_memory_ids=[id_new]),
        ]

        recent = get_recently_activated(history, last_n_turns=window)
        assert id_old not in recent
        assert id_new in recent

    def test_all_recent_turns_included(self):
        window = 5
        ids = [uuid4() for _ in range(5)]

        history = [ConversationTurn(activated_memory_ids=[i]) for i in ids]

        recent = get_recently_activated(history, last_n_turns=window)
        assert recent == set(ids)

    def test_empty_history_returns_empty(self):
        recent = get_recently_activated([], last_n_turns=10)
        assert recent == set()

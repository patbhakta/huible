from __future__ import annotations

import pytest

from huible.memory.protocol import DisclosureScope
from huible.memory.retrieval import (
    ConversationTurn,
    RetrievalConfig,
    retrieve,
)
from tests.f1.conftest import CosineFakeBackend


class TestF1_3_FeedbackSuppression:
    """F1.3: Suppress feedback loops — same query second time returns different results."""

    async def test_same_query_produces_different_results(
        self, backend: CosineFakeBackend
    ) -> None:
        nodes = list(backend._memories.values())
        persona_id = nodes[0].persona_id
        query_emb = nodes[0].embedding_content or [0.0] * 1536

        config = RetrievalConfig(
            activation_threshold=0.01,
            max_spread_depth=2,
            decay_factor=0.9,
            suppression_window=10,
            suppression_factor=0.1,
            max_activated=50,
        )

        first_results = await retrieve(
            backend,
            persona_id,
            query_emb,
            disclosure_tier=DisclosureScope.PRIVATE,
            config=config,
        )

        if not first_results:
            pytest.skip("No results returned from first query")

        first_ids = {r.node.id: r.activation for r in first_results}
        history = [
            ConversationTurn(activated_memory_ids=list(first_ids.keys()))
        ]

        second_results = await retrieve(
            backend,
            persona_id,
            query_emb,
            conversation_history=history,
            disclosure_tier=DisclosureScope.PRIVATE,
            config=config,
        )

        if not second_results:
            pytest.skip("No results returned from second query")

        second_ids = {r.node.id: r.activation for r in second_results}
        top_first_id = max(first_ids, key=first_ids.get)
        if top_first_id in second_ids:
            assert second_ids[top_first_id] < first_ids[top_first_id], (
                f"Top memory from first query (activation={first_ids[top_first_id]:.4f}) "
                f"was NOT suppressed on second query (activation={second_ids[top_first_id]:.4f})"
            )

    async def test_recently_activated_memories_suppressed(
        self, backend: CosineFakeBackend
    ) -> None:
        nodes = list(backend._memories.values())
        persona_id = nodes[0].persona_id
        query_emb = nodes[0].embedding_content or [0.0] * 1536

        config = RetrievalConfig(
            activation_threshold=0.01,
            max_spread_depth=2,
            decay_factor=0.9,
            suppression_factor=0.1,
        )

        first_results = await retrieve(
            backend,
            persona_id,
            query_emb,
            disclosure_tier=DisclosureScope.PRIVATE,
            config=config,
        )

        if len(first_results) < 2:
            pytest.skip("Need >= 2 results to test differential suppression")

        suppressed_ids = {r.node.id for r in first_results[:3]}
        history = [ConversationTurn(activated_memory_ids=list(suppressed_ids))]

        second_results = await retrieve(
            backend,
            persona_id,
            query_emb,
            conversation_history=history,
            disclosure_tier=DisclosureScope.PRIVATE,
            config=config,
        )

        suppressed_in_second = {
            r.node.id: r.activation
            for r in second_results
            if r.node.id in suppressed_ids
        }
        if suppressed_in_second:
            for mid, act in suppressed_in_second.items():
                original_act = next(
                    r.activation for r in first_results if r.node.id == mid
                )
                assert act < original_act, (
                    f"Memory {mid} was NOT suppressed: {original_act:.4f} -> {act:.4f}"
                )

    async def test_suppression_window_respected(
        self, backend: CosineFakeBackend
    ) -> None:
        nodes = list(backend._memories.values())
        persona_id = nodes[0].persona_id
        query_emb = nodes[0].embedding_content or [0.0] * 1536

        config = RetrievalConfig(
            activation_threshold=0.01,
            max_spread_depth=1,
            decay_factor=0.9,
            suppression_window=2,
            suppression_factor=0.1,
        )

        first_results = await retrieve(
            backend,
            persona_id,
            query_emb,
            disclosure_tier=DisclosureScope.PRIVATE,
            config=config,
        )

        if not first_results:
            pytest.skip("No results returned")

        target_ids = {r.node.id: r.activation for r in first_results}

        target_ids = {r.node.id: r.activation for r in first_results}
        target_list = list(target_ids.keys())
        half = len(target_list) // 2
        suppressed_ids = set(target_list[:half])
        out_of_window_ids = set(target_list[half:])

        history = [
            ConversationTurn(activated_memory_ids=list(suppressed_ids)),
            ConversationTurn(activated_memory_ids=out_of_window_ids),
        ]

        window_config = RetrievalConfig(
            activation_threshold=0.01,
            max_spread_depth=1,
            decay_factor=0.9,
            suppression_window=1,
            suppression_factor=0.1,
        )
        second_results = await retrieve(
            backend,
            persona_id,
            query_emb,
            conversation_history=history,
            disclosure_tier=DisclosureScope.PRIVATE,
            config=window_config,
        )

        result_map = {r.node.id: r.activation for r in second_results}
        non_suppressed_count = sum(
            1 for mid in out_of_window_ids
            if mid in result_map and result_map[mid] >= target_ids[mid] * 0.9
        )
        assert non_suppressed_count > 0, (
            "Suppression window should allow out-of-window memories to remain unsuppressed"
        )

    async def test_suppression_shifts_ranking(
        self, backend: CosineFakeBackend
    ) -> None:
        nodes = list(backend._memories.values())
        persona_id = nodes[0].persona_id
        query_emb = nodes[0].embedding_content or [0.0] * 1536

        config = RetrievalConfig(
            activation_threshold=0.01,
            max_spread_depth=2,
            decay_factor=0.9,
            suppression_factor=0.05,
            max_activated=20,
        )

        first_results = await retrieve(
            backend,
            persona_id,
            query_emb,
            disclosure_tier=DisclosureScope.PRIVATE,
            config=config,
        )

        if len(first_results) < 3:
            pytest.skip("Need >= 3 results")

        suppress_ids = {r.node.id for r in first_results[:5]}
        history = [ConversationTurn(activated_memory_ids=list(suppress_ids))]

        second_results = await retrieve(
            backend,
            persona_id,
            query_emb,
            conversation_history=history,
            disclosure_tier=DisclosureScope.PRIVATE,
            config=config,
        )

        first_top = first_results[0].node.id
        if second_results:
            second_top = second_results[0].node.id
            if first_top in suppress_ids:
                assert (
                    second_top != first_top
                    or second_results[0].activation
                    < first_results[0].activation
                ), "Ranking should shift after suppression of top results"

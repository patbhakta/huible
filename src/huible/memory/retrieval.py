from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from uuid import UUID

from huible.memory.protocol import (
    DisclosureScope,
    MemoryBackend,
    MemoryNode,
    SearchResult,
)


@dataclass(slots=True)
class RetrievalConfig:
    activation_threshold: float = 0.3
    max_activated: int = 50
    decay_factor: float = 0.6
    suppression_window: int = 10
    max_spread_depth: int = 3
    seed_top_k: int = 20
    suppression_factor: float = 0.1
    motif_boost_factor: float = 1.3
    motif_threshold: int = 3
    motif_max_themes: int = 5


@dataclass(slots=True)
class ActivatedMemory:
    node: MemoryNode
    activation: float


@dataclass(slots=True)
class ConversationTurn:
    activated_memory_ids: list[UUID] = field(default_factory=list)


async def multi_vector_search(
    backend: MemoryBackend,
    persona_id: UUID,
    query_embedding_content: list[float],
    query_embedding_sensory: list[float] | None = None,
    query_embedding_affect: list[float] | None = None,
    top_k: int = 20,
) -> list[SearchResult]:
    content_results = await backend.search_by_content(
        persona_id, query_embedding_content, top_k=top_k
    )
    seen: dict[UUID, SearchResult] = {}
    for sr in content_results:
        seen[sr.node.id] = sr

    if query_embedding_sensory:
        sensory_results = await backend.search_by_sensory(
            persona_id, query_embedding_sensory, top_k=top_k
        )
        for sr in sensory_results:
            if sr.node.id in seen:
                seen[sr.node.id] = SearchResult(
                    node=sr.node, score=max(seen[sr.node.id].score, sr.score)
                )
            else:
                seen[sr.node.id] = sr

    if query_embedding_affect:
        affect_results = await backend.search_by_affect(
            persona_id, query_embedding_affect, top_k=top_k
        )
        for sr in affect_results:
            if sr.node.id in seen:
                seen[sr.node.id] = SearchResult(
                    node=sr.node, score=max(seen[sr.node.id].score, sr.score)
                )
            else:
                seen[sr.node.id] = sr

    return sorted(seen.values(), key=lambda sr: sr.score, reverse=True)[:top_k]


def get_recently_activated(
    conversation_history: Sequence[ConversationTurn],
    last_n_turns: int = 10,
) -> set[UUID]:
    recent: set[UUID] = set()
    for turn in conversation_history[-last_n_turns:]:
        recent.update(turn.activated_memory_ids)
    return recent


def apply_suppression(
    activation_map: dict[UUID, float],
    recent_ids: set[UUID],
    suppression_factor: float = 0.1,
) -> None:
    for node_id in recent_ids:
        if node_id in activation_map:
            activation_map[node_id] *= suppression_factor


async def spread_activation(
    backend: MemoryBackend,
    activation_map: dict[UUID, float],
    config: RetrievalConfig,
) -> dict[UUID, float]:
    for depth in range(config.max_spread_depth):
        depth_threshold = config.activation_threshold * (config.decay_factor**depth)
        new_activations: dict[UUID, float] = {}
        nodes_to_process = [
            (nid, act)
            for nid, act in activation_map.items()
            if act >= depth_threshold
        ]
        for node_id, activation in nodes_to_process:
            edges = await backend.get_edges(node_id)
            for edge in edges:
                propagated = activation * edge.weight * config.decay_factor
                existing = new_activations.get(edge.target_id, 0.0)
                if propagated > existing:
                    new_activations[edge.target_id] = propagated
        for node_id, act in new_activations.items():
            if act > activation_map.get(node_id, 0.0):
                activation_map[node_id] = act
    return activation_map


def cluster_by_theme(
    nodes: dict[UUID, float],
    all_nodes: dict[UUID, MemoryNode],
    max_themes: int = 5,
) -> list[list[UUID]]:
    theme_groups: dict[str, list[UUID]] = defaultdict(list)
    for node_id in nodes:
        node = all_nodes.get(node_id)
        if node:
            theme_groups[node.content_type.value].append(node_id)
    sorted_groups = sorted(theme_groups.values(), key=len, reverse=True)
    return sorted_groups[:max_themes]


def apply_motif_escalation(
    activation_map: dict[UUID, float],
    all_nodes: dict[UUID, MemoryNode],
    config: RetrievalConfig,
) -> None:
    motifs = cluster_by_theme(activation_map, all_nodes, max_themes=config.motif_max_themes)
    for motif_group in motifs:
        if len(motif_group) >= config.motif_threshold:
            for node_id in motif_group:
                activation_map[node_id] *= config.motif_boost_factor


DISCLOSURE_ORDER: list[DisclosureScope] = [
    DisclosureScope.ALL_CONTACTS,
    DisclosureScope.CLOSE_FRIENDS,
    DisclosureScope.FAMILY,
    DisclosureScope.PRIVATE,
]


def filter_by_disclosure(
    memory_ids: set[UUID] | dict[UUID, float],
    disclosure_tier: DisclosureScope,
    all_nodes: dict[UUID, MemoryNode],
) -> set[UUID]:
    tier_rank = DISCLOSURE_ORDER.index(disclosure_tier)
    eligible: set[UUID] = set()
    ids_iter = memory_ids if isinstance(memory_ids, set) else set(memory_ids.keys())
    for node_id in ids_iter:
        node = all_nodes.get(node_id)
        if node:
            node_rank = DISCLOSURE_ORDER.index(node.disclosure_scope)
            if node_rank <= tier_rank:
                eligible.add(node_id)
    return eligible


async def retrieve(
    backend: MemoryBackend,
    persona_id: UUID,
    query_embedding_content: list[float],
    query_embedding_sensory: list[float] | None = None,
    query_embedding_affect: list[float] | None = None,
    conversation_history: Sequence[ConversationTurn] | None = None,
    disclosure_tier: DisclosureScope = DisclosureScope.FAMILY,
    config: RetrievalConfig | None = None,
) -> list[ActivatedMemory]:
    if config is None:
        config = RetrievalConfig()
    if conversation_history is None:
        conversation_history = []

    seed_results = await multi_vector_search(
        backend,
        persona_id,
        query_embedding_content,
        query_embedding_sensory,
        query_embedding_affect,
        top_k=config.seed_top_k,
    )

    activation_map: dict[UUID, float] = {}
    for sr in seed_results:
        activation_map[sr.node.id] = sr.score

    recent_ids = get_recently_activated(conversation_history, config.suppression_window)
    apply_suppression(activation_map, recent_ids, config.suppression_factor)

    activation_map = await spread_activation(backend, activation_map, config)

    all_nodes: dict[UUID, MemoryNode] = {}
    for node_id in activation_map:
        memory = await backend.get_memory(node_id)
        if memory:
            all_nodes[node_id] = memory

    apply_motif_escalation(activation_map, all_nodes, config)

    eligible = filter_by_disclosure(activation_map, disclosure_tier, all_nodes)

    activated_memories = sorted(
        [
            (nid, act)
            for nid, act in activation_map.items()
            if nid in eligible and act >= config.activation_threshold
        ],
        key=lambda x: x[1],
        reverse=True,
    )[: config.max_activated]

    results: list[ActivatedMemory] = []
    for node_id, act in activated_memories:
        node = all_nodes.get(node_id)
        if node:
            results.append(ActivatedMemory(node=node, activation=act))
    return results
